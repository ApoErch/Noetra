from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from api.auth import router as auth_router
from core.config import get_settings

app = FastAPI(title="Noetra API", version="0.1.0")
app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret)
app.include_router(auth_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
