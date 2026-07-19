from fastapi import FastAPI

app = FastAPI(title="Noetra API", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
