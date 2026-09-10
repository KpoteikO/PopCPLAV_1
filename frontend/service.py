"""Minimal health-check example, not an agent-generated project."""
from fastapi import FastAPI

app = FastAPI(title="PopCat Preview", version="1.0.0")

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "popcat-preview"}
