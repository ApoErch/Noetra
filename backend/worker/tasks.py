import base64
import shutil
import subprocess
import time
from pathlib import Path

from celery.utils.log import get_task_logger

from core.celery_app import celery_app
from core.config import get_settings
from core.db import SessionLocal
from core.models import Repository, RepositoryStatus, User
from core.redis_client import release_index_lock
from core.security import decrypt_token
from worker.embedding import embed_repository
from worker.indexing import index_repository_files

CLONE_TIMEOUT_SECONDS = 300

# `get_task_logger` (not the stdlib `logging` module directly) is the Celery-standard
# way to log from inside a task — it nests under Celery's own logger so these lines
# inherit the worker's --loglevel and log formatting instead of needing separate setup.
logger = get_task_logger(__name__)


@celery_app.task
def clone_repository(repository_id: str) -> None:
    """Clone a queued `Repository` into local storage using its owner's decrypted GitHub token, then index it."""
    db = SessionLocal()
    task_start = time.perf_counter()
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

        clone_start = time.perf_counter()

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

        logger.info("repo %s: clone stage took %.2fs", repository_id, time.perf_counter() - clone_start)

        # Everything from here — file walk, parsing, graphing, chunking, and the
        # matching status transitions — is the shared indexing core, so the eval
        # seed indexes repos through the exact same code path. Metrics doesn't
        # exist yet, so status stops advancing after embedding rather than
        # jumping to READY, which is reserved for "chat + full hybrid search +
        # metrics all unlocked" (docs/WORKFLOW.md).
        index_repository_files(db, repo, dest)

        # Embedding is the first stage that can fail for reasons that are not the
        # repo's fault (a provider outage, a bad API key) rather than something really
        # wrong with the repo. Every earlier stage is deterministic and local, so
        # letting its exception hit the `except` below and mark the repo FAILED is
        # correct there — it would not be correct here, since it would brick a
        # repo that is already fully chunked and perfectly lexically searchable.
        # So: catch it, log it, leave status=EMBEDDING (set at entry to
        # embed_repository) rather than re-raising. `WHERE embedding IS NULL`
        # makes a retry resumable, and search() gates the semantic leg on having
        # embedded chunks, not on status, so lexical search keeps working either way.
        try:
            embed_repository(db, repo)
        except Exception:
            db.rollback()
            logger.exception("repo %s: embedding stage failed, leaving status=EMBEDDING", repository_id)

        logger.info("repo %s: full pipeline took %.2fs", repository_id, time.perf_counter() - task_start)
    except Exception as exc:
        db.rollback()
        if repo is not None:
            repo.status = RepositoryStatus.FAILED
            repo.error_message = str(exc)
            db.commit()
        raise
    finally:
        db.close()
        release_index_lock(repository_id)


@celery_app.task
def delete_repository_clone(repository_id: str) -> None:
    """Remove a deleted repo's on-disk clone directory. Its DB row is already gone by the time this runs — the path is derived purely from `repository_id`, same as `clone_repository` builds it."""
    dest = Path(get_settings().clone_storage_dir) / repository_id
    if dest.exists():
        shutil.rmtree(dest)
