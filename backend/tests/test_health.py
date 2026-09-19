import os
import tempfile
import unittest
from pathlib import Path


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

from config import settings
from main import app
from utils.database import engine
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


if __name__ == "__main__":
    unittest.main()
