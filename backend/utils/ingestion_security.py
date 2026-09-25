import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from config import settings


NESTED_ARCHIVE_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
}
GIT_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$")


class IngestionValidationError(ValueError):
    """Raised when untrusted repository input violates an ingestion boundary."""


def validate_git_repo_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url or len(url) > 2048:
        raise IngestionValidationError("Repository URL is missing or too long.")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise IngestionValidationError("Repository URL is invalid.") from exc

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        raise IngestionValidationError("Only HTTPS repository URLs are supported.")
    if parsed.username or parsed.password:
        raise IngestionValidationError("Repository URLs must not contain credentials.")
    if hostname not in settings.allowed_git_hosts:
        raise IngestionValidationError("Repository host is not allowed.")
    if port not in (None, 443):
        raise IngestionValidationError("Repository URL uses an unsupported port.")
    if parsed.query or parsed.fragment:
        raise IngestionValidationError("Repository URL must not contain a query or fragment.")

    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or not GIT_PATH_PATTERN.fullmatch(decoded_path):
        raise IngestionValidationError("Repository URL must identify one owner and repository.")
    path_parts = [part for part in decoded_path.strip("/").split("/") if part]
    if any(part in {".", ".."} for part in path_parts):
        raise IngestionValidationError("Repository URL contains an unsafe path.")

    return urlunsplit(("https", hostname, parsed.path.rstrip("/"), "", ""))


def _normalized_member_path(filename: str) -> PurePosixPath:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or path.is_absolute():
        raise IngestionValidationError("ZIP archive contains an unsafe file path.")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise IngestionValidationError("ZIP archive contains an unsafe file path.")
    if path.parts and ":" in path.parts[0]:
        raise IngestionValidationError("ZIP archive contains an unsafe file path.")
    return path


def _is_zip_symlink(member: zipfile.ZipInfo) -> bool:
    unix_mode = (member.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(unix_mode)


def safe_extract_zip(zip_path: Path, destination: Path) -> dict[str, int]:
    """Validate an entire archive before writing any member to disk."""
    destination = destination.resolve()
    seen_paths: set[str] = set()
    total_size = 0
    file_count = 0

    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise IngestionValidationError("Uploaded file is not a valid ZIP archive.") from exc

    with archive:
        members = archive.infolist()
        for member in members:
            member_path = _normalized_member_path(member.filename)
            canonical_path = member_path.as_posix().rstrip("/").casefold()
            if canonical_path in seen_paths:
                raise IngestionValidationError("ZIP archive contains duplicate file paths.")
            seen_paths.add(canonical_path)

            if len(member_path.as_posix()) > settings.MAX_ARCHIVE_PATH_LENGTH:
                raise IngestionValidationError("ZIP archive contains a path that is too long.")
            if len(member_path.parts) > settings.MAX_ARCHIVE_DEPTH:
                raise IngestionValidationError("ZIP archive directory depth exceeds the limit.")
            if _is_zip_symlink(member):
                raise IngestionValidationError("ZIP archive must not contain symbolic links.")
            if member.flag_bits & 0x1:
                raise IngestionValidationError("Encrypted ZIP files are not supported.")

            if member.is_dir():
                continue
            file_count += 1
            total_size += member.file_size
            if file_count > settings.MAX_ZIP_FILES:
                raise IngestionValidationError("ZIP archive contains too many files.")
            if total_size > settings.MAX_ZIP_EXTRACTED_BYTES:
                raise IngestionValidationError("ZIP archive expands beyond the allowed size.")
            if member.file_size and member.compress_size == 0:
                raise IngestionValidationError("ZIP archive has an unsafe compression ratio.")
            if member.compress_size:
                ratio = member.file_size / member.compress_size
                if ratio > settings.MAX_ZIP_COMPRESSION_RATIO:
                    raise IngestionValidationError("ZIP archive has an unsafe compression ratio.")
            if member_path.suffix.lower() in NESTED_ARCHIVE_EXTENSIONS:
                raise IngestionValidationError("Nested archives are not supported.")

        destination.mkdir(parents=True, exist_ok=True)
        actual_total = 0
        try:
            for member in members:
                member_path = _normalized_member_path(member.filename)
                target = (destination / Path(*member_path.parts)).resolve()
                if not target.is_relative_to(destination):
                    raise IngestionValidationError("ZIP archive contains an unsafe file path.")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                member_total = 0
                with archive.open(member) as source, target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        member_total += len(chunk)
                        actual_total += len(chunk)
                        if member_total > member.file_size or actual_total > settings.MAX_ZIP_EXTRACTED_BYTES:
                            raise IngestionValidationError("ZIP archive expanded beyond its validated size.")
                        output.write(chunk)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    return {"file_count": file_count, "extracted_bytes": actual_total}


def validate_repository_tree(root: Path) -> dict[str, int]:
    """Reject links and unexpectedly large working trees before scanning begins."""
    root = root.resolve()
    total_bytes = 0
    file_count = 0
    for current_root, dirs, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        dirs[:] = [directory for directory in dirs if directory != ".git"]
        for directory in dirs:
            if (current / directory).is_symlink():
                raise IngestionValidationError("Repository must not contain symbolic links.")
        for filename in files:
            path = current / filename
            if path.is_symlink():
                raise IngestionValidationError("Repository must not contain symbolic links.")
            try:
                relative = path.relative_to(root)
                size = path.stat().st_size
            except OSError as exc:
                raise IngestionValidationError("Repository contains an unreadable file.") from exc
            if len(relative.as_posix()) > settings.MAX_ARCHIVE_PATH_LENGTH:
                raise IngestionValidationError("Repository contains a path that is too long.")
            if len(relative.parts) > settings.MAX_ARCHIVE_DEPTH:
                raise IngestionValidationError("Repository directory depth exceeds the limit.")
            file_count += 1
            total_bytes += size
            if file_count > settings.MAX_ZIP_FILES:
                raise IngestionValidationError("Repository contains too many files.")
            if total_bytes > settings.MAX_ZIP_EXTRACTED_BYTES:
                raise IngestionValidationError("Repository working tree exceeds the size limit.")
    return {"file_count": file_count, "total_bytes": total_bytes}
