import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db import get_db
from core.github import build_authorize_url, exchange_code_for_token, fetch_github_profile
from core.models import User
from core.security import decrypt_token, encrypt_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    """Start GitHub OAuth: mint a CSRF `state`, save it in the session, and redirect to GitHub's consent screen."""
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    return RedirectResponse(build_authorize_url(state))


@router.get("/callback")
def callback(request: Request, code: str, state: str, db: Session = Depends(get_db)) -> RedirectResponse:
    """Handle GitHub's post-consent redirect: verify `state`, swap the code for a token, upsert the user, and log them in."""
    expected_state = request.session.pop("oauth_state", None)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    access_token = exchange_code_for_token(code)
    profile = fetch_github_profile(access_token)

    user = db.query(User).filter(User.github_id == profile.github_id).first()
    if user is None:
        user = User(
            github_id=profile.github_id,
            username=profile.username,
            avatar_url=profile.avatar_url,
            access_token=encrypt_token(access_token),
        )
        db.add(user)
    else:
        user.username = profile.username
        user.avatar_url = profile.avatar_url
        user.access_token = encrypt_token(access_token)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(get_settings().frontend_url)


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    """Clear the session cookie, ending the user's login."""
    request.session.clear()
    return {"status": "ok"}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: resolve the logged-in `User` from the session cookie, or raise 401 if not authenticated."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, str | None]:
    """Return the current user's public profile (id, github id, username, avatar)."""
    return {
        "id": str(user.id),
        "github_id": str(user.github_id),
        "username": user.username,
        "avatar_url": user.avatar_url,
    }
