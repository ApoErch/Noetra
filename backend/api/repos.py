from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.auth import get_current_user
from core.celery_app import celery_app
from core.db import get_db
from core.github import parse_repo_slug
from core.models import Repository, User

router = APIRouter(prefix="/api/v1/repos", tags=["repos"])


class RepositoryCreate(BaseModel):
    github_url: str

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        parse_repo_slug(value)
        return value.rstrip("/")


@router.post("", status_code=201)
def create_repository(
    payload: RepositoryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
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
