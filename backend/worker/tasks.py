import base64
import shutil
import subprocess
from pathlib import Path

from core.celery_app import celery_app
from core.config import get_settings
from core.db import SessionLocal
from core.models import Repository, RepositoryStatus, User
from core.security import decrypt_token


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
        )

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

        repo.status = RepositoryStatus.READY
        db.commit()
    finally:
        db.close()
