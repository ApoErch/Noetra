from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.auth import get_current_user
from core.celery_app import celery_app
from core.db import get_db
from core.github import parse_repo_slug
from core.models import File, Repository, RepositoryStatus, User

router = APIRouter(prefix="/api/v1/repos", tags=["repos"])


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
) -> list[dict[str, str | None]]:
    """List every repo the current user has imported, most recently created first."""
    repos = db.query(Repository).filter(Repository.user_id == user.id).order_by(Repository.created_at.desc()).all()
    return [
        {
            "id": str(r.id),
            "github_url": r.github_url,
            "name": r.name,
            "status": r.status.value,
            "error_message": r.error_message,
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
    db.commit()
    db.refresh(repo)

    celery_app.send_task("worker.tasks.clone_repository", args=[str(repo.id)])

    return {
        "id": str(repo.id),
        "github_url": repo.github_url,
        "name": repo.name,
        "status": repo.status.value,
    }


def _get_owned_repository(repository_id: str, user: User, db: Session) -> Repository:
    """Look up a `Repository` by id and 404 unless it exists and belongs to `user` — shared ownership check for every repo-scoped endpoint."""
    repo = (
        db.query(Repository)
        .filter(Repository.id == repository_id, Repository.user_id == user.id)
        .first()
    )
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.post("/{repository_id}/retry")
def retry_repository(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Re-enqueue the clone job for a failed repo: clears the error and any partial `File` rows, resets to `queued`."""
    repo = _get_owned_repository(repository_id, user, db)
    if repo.status != RepositoryStatus.FAILED:
        raise HTTPException(status_code=409, detail="Only a failed repository can be retried")

    db.query(File).filter(File.repository_id == repo.id).delete()
    repo.status = RepositoryStatus.QUEUED
    repo.error_message = None
    db.commit()

    celery_app.send_task("worker.tasks.clone_repository", args=[str(repo.id)])

    return {"id": str(repo.id), "status": repo.status.value}


@router.delete("/{repository_id}", status_code=204)
def delete_repository(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Permanently remove a repo and its files (cascades via the FK) — lets the user clean up failed or unwanted imports."""
    repo = _get_owned_repository(repository_id, user, db)
    db.delete(repo)
    db.commit()


@router.get("/{repository_id}/files")
def list_files(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, str | bool]]:
    """Return every tracked file's path + binary flag for a repo, for the client to build the file tree (no content — cheap at scale)."""
    _get_owned_repository(repository_id, user, db)
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
    _get_owned_repository(repository_id, user, db)
    file = db.query(File).filter(File.id == file_id, File.repository_id == repository_id).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {"id": str(file.id), "path": file.path, "content": file.content, "is_binary": file.is_binary}
