from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.deps import get_owned_repository
from core.celery_app import celery_app
from core.db import get_db
from core.github import parse_repo_slug
from core.models import File, Metric, Repository, RepositoryStatus, User
from core.redis_client import acquire_index_lock, is_index_locked

router = APIRouter(prefix="/api/v1/repos", tags=["repos"])

# Stages a repo can be picked up from without re-cloning: the pipeline's tail has not
# finished, but everything before it has, so re-running just that part loses nothing.
RESUMABLE_STATUSES = {RepositoryStatus.EMBEDDING, RepositoryStatus.METRICS}


class RepositoryCreate(BaseModel):
    """Request body for importing a repo: just the GitHub URL, validated at the API boundary."""

    github_url: str

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        """Reject anything that isn't a github.com/owner/repo URL, and strip any trailing slash."""
        parse_repo_slug(value)
        return value.rstrip("/")


@router.get("")
def list_repositories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, str | bool | None]]:
    """List every repo the current user has imported, most recently created first.

    `is_indexing` comes from the Redis index lock, not from `status`. Status records the
    furthest stage a repo *reached*; it says nothing about whether a job is still running, so
    a repo that died mid-embedding looks identical to one that is embedding right now. The
    lock is the difference, and it is what lets the UI offer Resume only when it would work.
    """
    repos = db.query(Repository).filter(Repository.user_id == user.id).order_by(Repository.created_at.desc()).all()
    return [
        {
            "id": str(r.id),
            "github_url": r.github_url,
            "name": r.name,
            "status": r.status.value,
            "error_message": r.error_message,
            "is_indexing": is_index_locked(str(r.id)),
        }
        for r in repos
    ]


@router.post("", status_code=201)
def create_repository(
    payload: RepositoryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Import a repo for the user: reject duplicates, persist it as `queued`, then enqueue the background clone/index job."""
    existing = (
        db.query(Repository)
        .filter(Repository.user_id == user.id, Repository.github_url == payload.github_url)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Repository already imported")

    repo = Repository(
        user_id=user.id,
        github_url=payload.github_url,
        name=parse_repo_slug(payload.github_url),
    )
    db.add(repo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Repository already imported")
    db.refresh(repo)

    if not acquire_index_lock(str(repo.id)):
        raise HTTPException(status_code=409, detail="Repository is already being indexed")

    celery_app.send_task("worker.tasks.clone_repository", args=[str(repo.id)])

    return {
        "id": str(repo.id),
        "github_url": repo.github_url,
        "name": repo.name,
        "status": repo.status.value,
    }


@router.post("/{repository_id}/retry")
def retry_repository(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Restart a repo that has stalled — a full re-index if it failed, or a resume of the pipeline's tail if it merely stopped short.

    The two paths are deliberately different. A FAILED repo is broken, so it is wiped back to
    `queued` and re-cloned. A repo parked at `embedding`/`metrics` is *fine* — its files,
    symbols, graph and chunks are all correct and only the tail is missing — so it keeps
    everything and re-runs just that part. Pointing the wiping path at a merely-stalled repo
    would delete a working index over what is usually a transient provider error.
    """
    repo = get_owned_repository(repository_id, user, db)
    if repo.status != RepositoryStatus.FAILED and repo.status not in RESUMABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="This repository has nothing to retry — it is either still indexing or already ready",
        )

    if not acquire_index_lock(str(repo.id)):
        raise HTTPException(status_code=409, detail="Repository is already being indexed")

    if repo.status == RepositoryStatus.FAILED:
        db.query(File).filter(File.repository_id == repo.id).delete()
        repo.status = RepositoryStatus.QUEUED
        repo.error_message = None
        db.commit()
        celery_app.send_task("worker.tasks.clone_repository", args=[str(repo.id)])
    else:
        celery_app.send_task("worker.tasks.resume_indexing", args=[str(repo.id)])

    return {"id": str(repo.id), "status": repo.status.value}


@router.delete("/{repository_id}", status_code=204)
def delete_repository(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Permanently remove a repo and its files (cascades via the FK) — lets the user clean up failed or unwanted imports."""
    repo = get_owned_repository(repository_id, user, db)
    db.delete(repo)
    db.commit()

    # The on-disk clone (which can be multiple GB for a large repo) isn't part
    # of the DB cascade — clean it up in the background rather than blocking
    # this request on a potentially large `rmtree`.
    celery_app.send_task("worker.tasks.delete_repository_clone", args=[repository_id])


@router.get("/{repository_id}/metrics")
def get_repository_metrics(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the repo's dashboard aggregates, keyed by metric name — computed once by the pipeline, not on this request.

    409 rather than an empty object while indexing: the rows genuinely do not exist yet, and
    zeros would read as a real (and wrong) answer.
    """
    repo = get_owned_repository(repository_id, user, db)
    if repo.status != RepositoryStatus.READY:
        raise HTTPException(status_code=409, detail="Metrics are not available until indexing finishes")

    metrics = db.query(Metric).filter(Metric.repository_id == repo.id).all()
    return {metric.key: metric.value for metric in metrics}


@router.get("/{repository_id}/files")
def list_files(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, str | bool]]:
    """Return every tracked file's path + binary flag for a repo, for the client to build the file tree (no content — cheap at scale)."""
    get_owned_repository(repository_id, user, db)
    files = db.query(File).filter(File.repository_id == repository_id).all()
    return [{"id": str(f.id), "path": f.path, "is_binary": f.is_binary} for f in files]


@router.get("/{repository_id}/files/{file_id}")
def get_file(
    repository_id: str,
    file_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | bool | None]:
    """Return a single file's content for lazy-loading into Monaco when clicked in the file tree."""
    get_owned_repository(repository_id, user, db)
    file = db.query(File).filter(File.id == file_id, File.repository_id == repository_id).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {"id": str(file.id), "path": file.path, "content": file.content, "is_binary": file.is_binary}
