import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch


TEST_ROOT = tempfile.TemporaryDirectory(prefix="codegraph-ingestion-security-")
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

from config import settings
from main import release_local_ingestion_slot, reserve_local_ingestion_slot
from tools.ingester import purge_project_from_chroma
from tools.ingester import ingest_to_chroma
from tools.scanner import get_codebase_map
from utils.ingestion_security import (
    IngestionValidationError,
    safe_extract_zip,
    validate_git_repo_url,
)


class GitUrlValidationTests(unittest.TestCase):
    def test_accepts_public_github_repository(self):
        self.assertEqual(
            validate_git_repo_url("https://github.com/example/project.git"),
            "https://github.com/example/project.git",
        )

    def test_rejects_credentials_local_paths_and_unapproved_hosts(self):
        rejected = [
            "https://token@github.com/example/project.git",
            "file:///tmp/project",
            "https://127.0.0.1/example/project.git",
            "https://example.com/example/project.git",
            "https://github.com/example/project.git?token=secret",
            "https://github.com/example/project/extra",
        ]
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(IngestionValidationError):
                validate_git_repo_url(url)


class ZipExtractionTests(unittest.TestCase):
    def setUp(self):
        self.case_root = TEST_PATH / self.id().replace(".", "-")
        self.case_root.mkdir(parents=True, exist_ok=True)

    def _archive(self, entries: list[tuple[str, str | bytes]]) -> Path:
        path = self.case_root / "upload.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
        return path

    def test_extracts_valid_archive_after_validation(self):
        archive = self._archive([("src/app.py", "print('safe')\n")])
        destination = self.case_root / "output"

        stats = safe_extract_zip(archive, destination)

        self.assertEqual(stats, {"file_count": 1, "extracted_bytes": 14})
        self.assertEqual((destination / "src" / "app.py").read_text(), "print('safe')\n")

    def test_rejects_path_traversal_without_writing_files(self):
        archive = self._archive([("src/valid.py", "valid"), ("../outside.py", "unsafe")])
        destination = self.case_root / "output"

        with self.assertRaises(IngestionValidationError):
            safe_extract_zip(archive, destination)

        self.assertFalse((self.case_root / "outside.py").exists())
        self.assertFalse(destination.exists())

    def test_rejects_case_insensitive_duplicate_paths(self):
        archive = self._archive([("src/App.py", "one"), ("src/app.py", "two")])

        with self.assertRaisesRegex(IngestionValidationError, "duplicate"):
            safe_extract_zip(archive, self.case_root / "output")

    def test_rejects_symbolic_links(self):
        archive_path = self.case_root / "upload.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            link = zipfile.ZipInfo("src/link.py")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../../secret")

        with self.assertRaisesRegex(IngestionValidationError, "symbolic links"):
            safe_extract_zip(archive_path, self.case_root / "output")

    def test_rejects_archive_over_extracted_size_limit(self):
        original_limit = settings.MAX_ZIP_EXTRACTED_BYTES
        settings.MAX_ZIP_EXTRACTED_BYTES = 4
        try:
            archive = self._archive([("app.py", "12345")])
            with self.assertRaisesRegex(IngestionValidationError, "expands"):
                safe_extract_zip(archive, self.case_root / "output")
        finally:
            settings.MAX_ZIP_EXTRACTED_BYTES = original_limit

    def test_rejects_too_many_files(self):
        original_limit = settings.MAX_ZIP_FILES
        settings.MAX_ZIP_FILES = 1
        try:
            archive = self._archive([("one.py", "1"), ("two.py", "2")])
            with self.assertRaisesRegex(IngestionValidationError, "too many files"):
                safe_extract_zip(archive, self.case_root / "output")
        finally:
            settings.MAX_ZIP_FILES = original_limit

    def test_rejects_nested_archives(self):
        archive = self._archive([("src/nested.zip", b"not another archive")])

        with self.assertRaisesRegex(IngestionValidationError, "Nested archives"):
            safe_extract_zip(archive, self.case_root / "output")

    def test_rejects_high_compression_ratio(self):
        archive_path = self.case_root / "upload.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("large.py", "a" * 20_000)

        with self.assertRaisesRegex(IngestionValidationError, "compression ratio"):
            safe_extract_zip(archive_path, self.case_root / "output")


class ScannerBoundaryTests(unittest.TestCase):
    def test_scanner_skips_secrets_and_stops_at_global_file_limit(self):
        project = TEST_PATH / "scanner-project"
        (project / "a").mkdir(parents=True, exist_ok=True)
        (project / "b").mkdir(parents=True, exist_ok=True)
        (project / ".env").write_text("API_KEY=secret")
        (project / "notes.txt").write_text("not source")
        (project / "a" / "one.py").write_text("print(1)")
        (project / "a" / "two.py").write_text("print(2)")
        (project / "b" / "three.py").write_text("print(3)")

        original_limit = settings.MAX_PROJECT_FILES
        settings.MAX_PROJECT_FILES = 2
        try:
            result = get_codebase_map(str(project), user_id="test", project_name="bounded")
        finally:
            settings.MAX_PROJECT_FILES = original_limit

        self.assertEqual(result["total_files"], 2)
        self.assertTrue(all(not path.endswith(".env") for path in result["files_to_process"]))
        self.assertGreaterEqual(result["skip_reasons"]["sensitive_filename"], 1)
        self.assertGreaterEqual(result["skip_reasons"]["project_file_limit"], 1)

    def test_scanner_enforces_total_source_byte_limit(self):
        project = TEST_PATH / "scanner-byte-project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "one.py").write_text("1234")
        (project / "two.py").write_text("5678")

        original_limit = settings.MAX_PROJECT_SOURCE_BYTES
        settings.MAX_PROJECT_SOURCE_BYTES = 4
        try:
            result = get_codebase_map(str(project), user_id="test", project_name="byte-bounded")
        finally:
            settings.MAX_PROJECT_SOURCE_BYTES = original_limit

        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["selected_bytes"], 4)
        self.assertEqual(result["skip_reasons"]["project_byte_limit"], 1)


class DuplicateIngestionTests(unittest.TestCase):
    def test_local_slot_blocks_same_project_until_release(self):
        self.assertTrue(reserve_local_ingestion_slot("user", "same-project"))
        try:
            self.assertFalse(reserve_local_ingestion_slot("user", "same-project"))
            self.assertTrue(reserve_local_ingestion_slot("other-user", "same-project"))
            release_local_ingestion_slot("other-user", "same-project")
        finally:
            release_local_ingestion_slot("user", "same-project")

        self.assertTrue(reserve_local_ingestion_slot("user", "same-project"))
        release_local_ingestion_slot("user", "same-project")

    def test_vectorstore_replacement_removes_previous_index(self):
        namespace = "test_replace_vectors"
        vector_path = settings.CHROMA_DB_DIR / namespace
        vector_path.mkdir(parents=True, exist_ok=True)
        stale_file = vector_path / "stale.txt"
        stale_file.write_text("old source that must not remain")

        self.assertTrue(purge_project_from_chroma(namespace, strict=True))
        self.assertFalse(vector_path.exists())

    def test_reingestion_replaces_collection_without_deleting_live_database(self):
        project_file = TEST_PATH / "replace.py"
        project_file.write_text("def replacement():\n    return 'new'\n")
        client = MagicMock()
        existing = MagicMock()
        existing.name = "langchain"
        client.list_collections.return_value = [existing]

        with (
            patch("tools.ingester.model_router.embeddings", return_value=MagicMock()),
            patch("tools.ingester.ensure_chroma_defaults", return_value=client),
            patch("tools.ingester.Chroma.from_documents") as from_documents,
            patch("tools.ingester.close_vectorstore"),
        ):
            chunks = ingest_to_chroma(
                [str(project_file)],
                "replacement-project",
                project_root=str(TEST_PATH),
                user_id="user",
            )

        self.assertGreater(chunks, 0)
        client.delete_collection.assert_called_once_with("langchain")
        from_documents.assert_called_once()
        self.assertEqual(from_documents.call_args.kwargs["collection_name"], "langchain")


if __name__ == "__main__":
    unittest.main()
