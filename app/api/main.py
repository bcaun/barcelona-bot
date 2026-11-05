"""FastAPI for the mini app (Phase 3). Phase 0: health check only."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="barcelona.ru game API", docs_url=None, redoc_url=None)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
