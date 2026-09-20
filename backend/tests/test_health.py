import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


TEST_ROOT = tempfile.TemporaryDirectory(prefix="codegraph-phase0-")
TEST_PATH = Path(TEST_ROOT.name)

os.environ.update({
    "ENVIRONMENT": "test",
    "STORAGE_MODE": "local",
    "DATABASE_URL": f"sqlite:///{(TEST_PATH / 'test.db').as_posix()}",
    "UPLOAD_DIR": str(TEST_PATH / "uploads"),
    "CHROMA_DB_DIR": str(TEST_PATH / "chroma"),
    "PROJECT_TREES_DIR": str(TEST_PATH / "trees"),
    "ARTIFACTS_DIR": str(TEST_PATH / "artifacts"),
    "SQLITE_DIR": str(TEST_PATH / "data"),
})

from fastapi.testclient import TestClient
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from agents.commenter import commenter_node
from agents.architect import architect_node
from agents.supervisor import supervisor_node
from providers.local_embeddings import LocalHashingEmbeddings
from providers.router import ModelRouter
from config import settings
from main import app, release_local_ingestion_slot, reserve_local_ingestion_slot
from models.user import IngestionJobModel, ProjectModel
from utils.database import SessionLocal, build_engine_options, engine
from utils.project_persistence import enforce_user_project_quota, reserve_project_ingestion
from utils.ingestion_jobs import _recover_stale_jobs, claim_next_job, enqueue_ingestion_job, execute_job
from utils.rate_limit import SlidingWindowLimiter
from utils.auth import UserSignUp
from utils.readiness import critical_config_errors


class HealthEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        engine.dispose()
        TEST_ROOT.cleanup()

    def test_liveness_returns_ok_and_request_id(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertRegex(response.headers["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_readiness_checks_database(self):
        response = self.client.get("/api/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready", "database": "ready"})

    def test_production_rejects_fallback_jwt_secret(self):
        previous_environment = settings.ENVIRONMENT
        previous_secret = settings.JWT_SECRET_KEY
        try:
            settings.ENVIRONMENT = "production"
            settings.JWT_SECRET_KEY = "fallback_secret_key"
            self.assertIn("production_jwt_secret_is_not_configured", critical_config_errors())
        finally:
            settings.ENVIRONMENT = previous_environment
            settings.JWT_SECRET_KEY = previous_secret

    def test_readiness_returns_503_for_invalid_production_config(self):
        previous_environment = settings.ENVIRONMENT
        previous_secret = settings.JWT_SECRET_KEY
        try:
            settings.ENVIRONMENT = "production"
            settings.JWT_SECRET_KEY = "fallback_secret_key"
            response = self.client.get("/api/ready")
        finally:
            settings.ENVIRONMENT = previous_environment
            settings.JWT_SECRET_KEY = previous_secret

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Service dependencies are not ready."})

    def test_postgres_disables_client_side_prepared_statements(self):
        options = build_engine_options("postgresql+psycopg://user:password@pooler/db")

        self.assertIsNone(options["connect_args"]["prepare_threshold"])
        self.assertTrue(options["pool_pre_ping"])

    def test_default_ingestion_preserves_source_code_without_llm_calls(self):
        source_path = TEST_PATH / "preserve_me.py"
        original = "def answer():\n    return 42\n"
        source_path.write_text(original, encoding="utf-8")
        previous_setting = settings.ENABLE_LLM_CODE_COMMENTING
        settings.ENABLE_LLM_CODE_COMMENTING = False
        try:
            result = commenter_node({
                "unprocessed_files": [str(source_path)],
                "processed_files": [],
                "comment_report": [],
            })
        finally:
            settings.ENABLE_LLM_CODE_COMMENTING = previous_setting

        self.assertEqual(source_path.read_text(encoding="utf-8"), original)
        self.assertEqual(result["processed_files"], [str(source_path)])
        self.assertEqual(result["comment_report"][0]["status"], "preserved_original")

    def test_local_embeddings_are_deterministic_and_fixed_width(self):
        embeddings = LocalHashingEmbeddings()
        first = embeddings.embed_query("def calculate_total(items): return sum(items)")
        repeated = embeddings.embed_query("def calculate_total(items): return sum(items)")
        unrelated = embeddings.embed_query("class DatabaseConnection: pass")

        self.assertEqual(len(first), 384)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, unrelated)

    def test_embedding_router_can_run_without_external_provider(self):
        previous_routes = settings.EMBEDDING_MODELS
        settings.EMBEDDING_MODELS = "local:hashing-v1"
        try:
            embeddings = ModelRouter().embeddings()
        finally:
            settings.EMBEDDING_MODELS = previous_routes

        self.assertIsInstance(embeddings, LocalHashingEmbeddings)

    def test_default_architecture_generation_avoids_llm_calls(self):
        source_path = TEST_PATH / "architecture_source.py"
        source_path.write_text("import pathlib\n", encoding="utf-8")
        architecture_file = TEST_PATH / "architecture.mmd"
        dependency_file = TEST_PATH / "dependencies.mmd"
        report_file = TEST_PATH / "ingestion-report.json"
        previous_setting = settings.ENABLE_LLM_ARCHITECTURE
        settings.ENABLE_LLM_ARCHITECTURE = False
        try:
            with (
                patch("agents.architect.model_router.invoke_text") as invoke_text,
                patch("agents.architect.architecture_path", return_value=architecture_file),
                patch("agents.architect.dependency_graph_path", return_value=dependency_file),
                patch("agents.architect.ingestion_report_path", return_value=report_file),
            ):
                architect_node({
                    "processed_files": [str(source_path)],
                    "project_path": str(TEST_PATH),
                    "user_id": "test-user",
                    "project_name": "test-project",
                })
        finally:
            settings.ENABLE_LLM_ARCHITECTURE = previous_setting

        invoke_text.assert_not_called()
        report = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertEqual(report["architecture_model"]["provider"], "deterministic")

    def test_local_code_question_uses_one_model_call_and_keeps_file_references(self):
        context = "--- Context Block 1 (File: main.py, Language: py) ---\napp = FastAPI()"
        with (
            patch("agents.supervisor.retrieve_code_context") as retrieve,
            patch(
                "agents.supervisor.model_router.invoke_chat",
                return_value=(AIMessage(content="The FastAPI application is defined in the retrieved entrypoint."), {"provider": "groq"}),
            ) as invoke_chat,
        ):
            retrieve.invoke.return_value = context
            result = supervisor_node({
                "project_name": "user_project",
                "public_project_name": "project",
                "messages": [HumanMessage(content="Which file defines the FastAPI application?")],
                "provider_events": [],
            })

        retrieve.invoke.assert_called_once()
        invoke_chat.assert_called_once()
        self.assertNotIn("tools", invoke_chat.call_args.kwargs)
        self.assertIn("`main.py`", result["messages"][0].content)

    def test_durable_reservation_rejects_processing_and_allows_terminal_retry(self):
        user_id = "duplicate-test-user"
        slug = "duplicate-project"
        db = SessionLocal()
        try:
            db.query(ProjectModel).filter(ProjectModel.user_id == user_id, ProjectModel.slug == slug).delete()
            db.commit()
            first = reserve_project_ingestion(db, user_id, slug, "Duplicate Project", "git")
            self.assertEqual(first.status, "processing")

            with self.assertRaises(HTTPException) as conflict:
                reserve_project_ingestion(db, user_id, slug, "Duplicate Project", "git")
            self.assertEqual(conflict.exception.status_code, 409)

            first.status = "error"
            db.commit()
            retried = reserve_project_ingestion(db, user_id, slug, "Duplicate Project", "git")
            self.assertEqual(retried.status, "processing")
            self.assertIsNone(retried.error)
        finally:
            db.rollback()
            db.query(ProjectModel).filter(ProjectModel.user_id == user_id, ProjectModel.slug == slug).delete()
            db.commit()
            db.close()

    def test_durable_job_claim_and_completion(self):
        db = SessionLocal()
        project = reserve_project_ingestion(db, "job-user", "job-project", "Job Project", "git")
        job = enqueue_ingestion_job(db, project)
        job_id = job.id
        project_id = project.id
        db.close()

        self.assertEqual(claim_next_job(), job_id)
        with patch("utils.ingestion_jobs._run_pipeline"), patch("utils.ingestion_jobs.write_status"):
            execute_job(job_id)

        db = SessionLocal()
        try:
            completed = db.get(IngestionJobModel, job_id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(completed.project.status, "ready")
            self.assertIsNotNone(completed.finished_at)
        finally:
            db.delete(db.get(ProjectModel, project_id))
            db.commit()
            db.close()

    def test_stale_running_job_becomes_retryable(self):
        db = SessionLocal()
        project = reserve_project_ingestion(db, "stale-user", "stale-project", "Stale Project", "git")
        job = enqueue_ingestion_job(db, project)
        job.status = "running"
        job.attempt_count = 1
        job.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=settings.INGESTION_JOB_STALE_SECONDS + 1)
        project_id = project.id
        db.commit()
        try:
            self.assertEqual(_recover_stale_jobs(db), 1)
            db.refresh(job)
            self.assertEqual(job.status, "retryable")
            self.assertEqual(job.project.status, "retryable")
            self.assertEqual(job.error_code, "stale_worker")
        finally:
            db.delete(db.get(ProjectModel, project_id))
            db.commit()
            db.close()

    def test_project_quota_blocks_new_projects(self):
        previous_limit = settings.MAX_PROJECTS_PER_USER
        settings.MAX_PROJECTS_PER_USER = 1
        db = SessionLocal()
        project = reserve_project_ingestion(db, "quota-user", "existing-project", "Existing Project", "git")
        project_id = project.id
        try:
            with self.assertRaises(HTTPException) as blocked:
                enforce_user_project_quota(db, "quota-user", "another-project")
            self.assertEqual(blocked.exception.status_code, 413)
        finally:
            settings.MAX_PROJECTS_PER_USER = previous_limit
            db.delete(db.get(ProjectModel, project_id))
            db.commit()
            db.close()

    def test_durable_job_failure_is_terminal_after_max_attempts(self):
        db = SessionLocal()
        project = reserve_project_ingestion(db, "failed-job-user", "failed-job", "Failed Job", "git")
        job = enqueue_ingestion_job(db, project)
        job.max_attempts = 1
        db.commit()
        job_id = job.id
        project_id = project.id
        db.close()

        self.assertEqual(claim_next_job(), job_id)
        with patch("utils.ingestion_jobs._run_pipeline", side_effect=RuntimeError("provider unavailable")), patch("utils.ingestion_jobs.write_status"):
            execute_job(job_id)

        db = SessionLocal()
        try:
            failed = db.get(IngestionJobModel, job_id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.project.status, "error")
            self.assertEqual(failed.error_code, "RuntimeError")
        finally:
            db.delete(db.get(ProjectModel, project_id))
            db.commit()
            db.close()

    def test_rate_limiter_returns_retry_after_and_recovers(self):
        limiter = SlidingWindowLimiter()
        self.assertEqual(limiter.allow("client", 2, 10, now=0), (True, 0))
        self.assertEqual(limiter.allow("client", 2, 10, now=1), (True, 0))
        allowed, retry_after = limiter.allow("client", 2, 10, now=2)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)
        self.assertEqual(limiter.allow("client", 2, 10, now=11), (True, 0))

    def test_password_policy_requires_mixed_case_and_number(self):
        with self.assertRaises(ValueError):
            UserSignUp(email="test@example.com", password="alllowercase")
        valid = UserSignUp(email="test@example.com", password="StrongPass9")
        self.assertEqual(valid.password, "StrongPass9")


if __name__ == "__main__":
    unittest.main()
