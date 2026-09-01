import argparse
import base64
import os
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.models import Repository, RepositoryStatus, User
from core.security import decrypt_token
from eval.repos import EVAL_REPOS, EvalRepo
from worker.embedding import embed_repository
from worker.indexing import index_repository_files

# Fixed namespace so the eval user and each repo get deterministic UUIDs — re-running
# the seed finds the same rows (idempotent), and run.py can recompute a repo's id from
# its key without querying the database.
_NAMESPACE = uuid.UUID("6f3e9d2a-0000-4000-8000-000000000000")
_FETCH_TIMEOUT_SECONDS = 300


def eval_id(name: str) -> uuid.UUID:
    """Deterministic UUID for an eval row (the user, or a repo by key) from a stable name."""
    return uuid.uuid5(_NAMESPACE, name)


def _ensure_eval_user(db: Session) -> User:
    """Get or create the synthetic user that owns every eval repo (its own token is never used)."""
    user_id = eval_id("eval-user")
    user = db.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            github_id=-1,  # sentinel: real GitHub ids are always positive, so this never collides
            username="eval-harness",
            access_token="unused",  # cloning borrows the repo owner's token, never this one
        )
        db.add(user)
        db.commit()
    return user


def _resolve_token(db: Session, repo: EvalRepo) -> str | None:
    """Token to clone `repo`: EVAL_GITHUB_TOKEN if set, else the owning user's stored token. None for public."""
    if not repo.private:
        return None
    env_token = os.environ.get("EVAL_GITHUB_TOKEN")
    if env_token:
        return env_token
    # Borrow the stored OAuth token of the user who owns the repo. They logged in with
    # `repo` scope (core/github.py SCOPES), so their token can read their own private repo —
    # no separate PAT needed. The owner is the segment before the repo name in the URL.
    owner = repo.github_url.rstrip("/").split("/")[-2]
    user = db.query(User).filter(User.username.ilike(owner)).first()
    if user is None:
        raise RuntimeError(
            f"private repo needs a token: set EVAL_GITHUB_TOKEN, or log in as '{owner}' "
            "so the seed can borrow that user's stored token"
        )
    return decrypt_token(user.access_token)


def _clone_at_sha(repo: EvalRepo, dest: Path, token: str | None) -> None:
    """Shallow-fetch a repo at its pinned commit into `dest`; `token` authenticates a private fetch."""
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "remote", "add", "origin", repo.github_url], check=True)

    fetch = ["git", "-C", str(dest)]
    if token:
        # GitHub's git-over-HTTPS server wants HTTP Basic auth (not Bearer). Passed with
        # `-c` on *fetch* (not clone), so it stays process-local and is never written into
        # the repo's .git/config — unlike `git clone -c http.*`, which persists it.
        basic_auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        fetch += ["-c", f"http.extraHeader=Authorization: Basic {basic_auth}"]
    fetch += ["fetch", "--depth", "1", "origin", repo.sha]
    subprocess.run(fetch, check=True, timeout=_FETCH_TIMEOUT_SECONDS)

    subprocess.run(["git", "-C", str(dest), "checkout", "-q", repo.sha], check=True)


def _embed_repo(db: Session, repo: Repository, key: str) -> None:
    """Embed a repo's chunks and print progress; non-fatal on failure.

    Mirrors worker/tasks.py's production handling: an embedding failure (quota, rate
    limit) shouldn't kill the whole seed run over one repo — print it and move on. The
    repo stays fully lexically searchable, and WHERE embedding IS NULL means a later
    re-run (even without --force) picks up wherever this one stopped.
    """
    embed_start = time.perf_counter()
    try:
        embedded = embed_repository(db, repo)
    except Exception as exc:
        db.rollback()
        print(f"  embedding failed for {key}: {exc}")
        return
    elapsed = time.perf_counter() - embed_start
    if embedded:
        print(f"  embedded {embedded} chunk(s) for {key} in {elapsed:.1f}s")
    else:
        print(f"  {key}: embeddings already complete")


def seed(force: bool = False, no_embed: bool = False) -> None:
    """Index every pinned eval repo into the database under the synthetic eval user (idempotent)."""
    db = SessionLocal()
    try:
        user = _ensure_eval_user(db)
        for repo_def in EVAL_REPOS:
            repo_id = eval_id(repo_def.key)
            existing = db.get(Repository, repo_id)
            if existing is not None and not force:
                print(f"skip {repo_def.key}: already indexed (use --force to reseed)")
                # Still resume embedding even without --force — embed_repository selects
                # WHERE embedding IS NULL, so re-running after an interrupted seed (a
                # 429, a killed process) continues instead of needing a full reseed.
                if not no_embed:
                    _embed_repo(db, existing, repo_def.key)
                continue

            try:
                token = _resolve_token(db, repo_def)
            except RuntimeError as exc:
                print(f"skip {repo_def.key}: {exc}")
                continue

            if existing is not None:  # force → wipe and reseed
                db.delete(existing)  # cascades files / code_entities / dependency_edges
                db.commit()

            repo = Repository(
                id=repo_id,
                user_id=user.id,
                github_url=repo_def.github_url,
                name=repo_def.key,
                status=RepositoryStatus.CLONING,
            )
            db.add(repo)
            db.commit()

            print(f"seeding {repo_def.key} @ {repo_def.sha[:7]} ...")
            try:
                with TemporaryDirectory() as tmp:
                    _clone_at_sha(repo_def, Path(tmp), token)
                    index_repository_files(db, repo, Path(tmp))
            except Exception:
                # Don't leave a half-seeded row behind — it would block a clean re-run
                # (which skips repos that already exist). Remove it so the repo retries.
                db.rollback()
                stuck = db.get(Repository, repo_id)
                if stuck is not None:
                    db.delete(stuck)
                    db.commit()
                raise
            print(f"  indexed: {repo_def.key} (status={repo.status.value})")

            # Deliberately outside the try/except above: an indexing failure means the
            # repo really is broken (delete and retry from scratch is correct), but an
            # embedding failure (rate limit, quota) shouldn't wipe a repo that's already
            # fully chunked and searchable. Left unembedded, it just resumes above on the
            # next --force-less run.
            if not no_embed:
                _embed_repo(db, repo, repo_def.key)
    finally:
        db.close()


def main() -> None:
    """CLI entry point: `python -m eval.seed [--force] [--no-embed]`."""
    parser = argparse.ArgumentParser(description="Seed the pinned eval repos into the database.")
    parser.add_argument("--force", action="store_true", help="delete and re-index repos already seeded")
    parser.add_argument(
        "--no-embed", action="store_true", help="skip embedding — for fast chunker-only iteration"
    )
    args = parser.parse_args()
    seed(force=args.force, no_embed=args.no_embed)


if __name__ == "__main__":
    main()
