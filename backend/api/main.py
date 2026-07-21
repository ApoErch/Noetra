from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api.auth import router as auth_router
from api.repos import router as repos_router
from core.config import get_settings

app = FastAPI(title="Noetra API", version="0.1.0")
app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(repos_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Liveness probe: returns ok so infra (and the frontend) can confirm the API is up."""
    return {"status": "ok"}
