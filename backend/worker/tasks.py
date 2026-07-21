import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

from core.celery_app import celery_app
from core.config import get_settings
from core.db import SessionLocal
from core.models import File, Repository, RepositoryStatus, User
from core.security import decrypt_token

CLONE_TIMEOUT_SECONDS = 300
MAX_FILE_SIZE_BYTES = 1_000_000


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 a file in chunks instead of loading it whole into memory — matters for files too large to store content for."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@celery_app.task
def clone_repository(repository_id: str) -> None:
    """Clone a queued `Repository` into local storage using its owner's decrypted GitHub token, updating status as it goes."""
    db = SessionLocal()
    try:
        repo = db.get(Repository, repository_id)
        if repo is None:
            return

        user = db.get(User, repo.user_id)
        token = decrypt_token(user.access_token)

        repo.status = RepositoryStatus.CLONING
        db.commit()

        dest = Path(get_settings().clone_storage_dir) / str(repo.id)
        if dest.exists():
            shutil.rmtree(dest)

        # GitHub's git-over-HTTPS server (not the REST API) only understands HTTP
        # Basic auth, not a `Bearer` header — Basic is what a URL like
        # https://<token>@github.com/... gets turned into internally anyway. Any
        # non-empty username works; GitHub itself uses "x-access-token" as the
        # convention. We build the header ourselves (instead of putting the token
        # in the clone URL) so it's never written into the resulting repo's config.
        basic_auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()

        try:
            result = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "-c",
                    f"http.extraHeader=Authorization: Basic {basic_auth}",
                    repo.github_url,
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            # Caught separately (rather than letting it hit the generic `except
            # Exception` below) because TimeoutExpired's default string form
            # includes the full command list — which contains the Basic-auth
            # header with the token in it. We never want that written into
            # repo.error_message, since that's shown straight to the user.
            repo.status = RepositoryStatus.FAILED
            repo.error_message = f"Clone timed out after {CLONE_TIMEOUT_SECONDS}s"
            db.commit()
            return

        if result.returncode != 0:
            repo.status = RepositoryStatus.FAILED
            repo.error_message = result.stderr.strip()
            db.commit()
            return

        # `git clone -c http.extraHeader=...` persists that setting into the new
        # repo's .git/config (git does this deliberately so later `fetch`/`pull`
        # keep using the same auth) — which would leave the token sitting in
        # plaintext on disk. Strip it now that the clone is done; we don't need
        # this repo's git remote to stay authenticated going forward.
        subprocess.run(
            ["git", "config", "--unset-all", "http.extraHeader"],
            cwd=dest,
            capture_output=True,
        )

        # `git ls-files` lists every tracked path (respects .gitignore) — this is
        # the data source for the file tree browser, independent of the Tree-sitter
        # parsing stage (Milestone 4) that comes later. Content is stored in
        # Postgres, not read from disk at request time, so the API service never
        # needs filesystem access to this clone (see docs/WORKFLOW.md).
        ls_files = subprocess.run(["git", "ls-files"], cwd=dest, capture_output=True, text=True)
        for rel_path in ls_files.stdout.splitlines():
            file_path = dest / rel_path

            # Some tracked paths are symlinks (e.g. the Linux kernel's
            # scripts/dtc/include-prefixes/* point at directories). git's actual
            # blob content for a symlink is the target path string itself, not
            # the linked-to file/directory — reading it a normal way would either
            # read through the link (wrong content) or, if it points at a
            # directory, raise IsADirectoryError. os.readlink reads the link
            # itself, matching what git considers that path's content to be.
            if file_path.is_symlink():
                db.add(
                    File(
                        repository_id=repo.id,
                        path=rel_path,
                        content=None,
                        is_binary=True,
                        content_hash=hashlib.sha256(file_path.readlink().as_posix().encode()).hexdigest(),
                    )
                )
                continue

            # Skip storing content for very large files — reading a huge file
            # into memory and into one Postgres row isn't worth it for a file
            # tree browser. Still record the file (with its hash) via chunked
            # reading so a giant file never has to sit in memory whole.
            if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
                db.add(
                    File(
                        repository_id=repo.id,
                        path=rel_path,
                        content=None,
                        is_binary=True,
                        content_hash=_hash_file(file_path),
                    )
                )
                continue

            raw = file_path.read_bytes()
            # A NUL byte is the classic "this is binary" signal (git itself uses it
            # for deciding whether to diff a file) — checked before attempting to
            # decode because NUL is technically valid UTF-8, so decoding alone
            # would let NUL-containing content through, and Postgres text columns
            # reject NUL bytes outright.
            if b"\x00" in raw:
                text = None
                is_binary = True
            else:
                try:
                    text = raw.decode("utf-8")
                    is_binary = False
                except UnicodeDecodeError:
                    text = None
                    is_binary = True

            db.add(
                File(
                    repository_id=repo.id,
                    path=rel_path,
                    content=text,
                    is_binary=is_binary,
                    content_hash=hashlib.sha256(raw).hexdigest(),
                )
            )
        db.commit()

        repo.status = RepositoryStatus.READY
        db.commit()
    except Exception as exc:
        db.rollback()
        if repo is not None:
            repo.status = RepositoryStatus.FAILED
            repo.error_message = str(exc)
            db.commit()
        raise
    finally:
        db.close()
