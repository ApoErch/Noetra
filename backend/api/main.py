from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api.auth import router as auth_router
from api.chat import router as chat_router
from api.repos import router as repos_router
from core.config import get_settings

app = FastAPI(title="Noetra API", version="0.1.0")
# Every session option is passed explicitly rather than inherited from Starlette's defaults.
# `same_site="lax"` is the default and is what the OAuth callback needs (GitHub redirects the
# browser back with a top-level GET, which "strict" would strip the cookie from); `https_only`
# has to be True wherever the app is served over TLS, which is why it is configuration and
# not a constant.
app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret,
    max_age=get_settings().session_max_age_seconds,
    same_site="lax",
    https_only=get_settings().session_https_only,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(repos_router)
app.include_router(chat_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Liveness probe: returns ok so infra (and the frontend) can confirm the API is up."""
    return {"status": "ok"}
