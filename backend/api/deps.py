"""Helpers shared by every repo-scoped router."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.models import Repository, User


def get_owned_repository(repository_id: str, user: User, db: Session) -> Repository:
    """Look up a `Repository` by id and 404 unless it exists and belongs to `user`.

    404 rather than 403 so the API never confirms that a repo id exists for someone else.
    """
    repo = (
        db.query(Repository)
        .filter(Repository.id == repository_id, Repository.user_id == user.id)
        .first()
    )
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo
