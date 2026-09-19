"""Interwar 官网 HTTP：:1910；只读 derived JSON/PNG。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from paths import derived_dir

log = logging.getLogger("interwar")
_STATIC = Path(__file__).resolve().parent / "static"
_SITE: dict[str, Any] | None = None
_COUNTRIES: dict[str, Any] | None = None


def _load() -> None:
    global _SITE, _COUNTRIES
    root = derived_dir()
    site_path = root / "site.json"
    countries_path = root / "countries.json"
    if site_path.is_file():
        _SITE = json.loads(site_path.read_text(encoding="utf-8"))
    else:
        _SITE = None
        log.warning("missing %s", site_path)
    if countries_path.is_file():
        _COUNTRIES = json.loads(countries_path.read_text(encoding="utf-8"))
    else:
        _COUNTRIES = {}


app = FastAPI(title="Interwar 1910-1960")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(level=logging.INFO)
    _load()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    _load()
    root = derived_dir()
    return {
        "ok": _SITE is not None,
        "derived": str(root),
        "countries": len(_COUNTRIES or {}),
        "has_map": (root / "map" / "ownership.png").is_file(),
    }


@app.get("/api/site.json")
def site() -> JSONResponse:
    _load()
    if _SITE is None:
        return JSONResponse({"error": "derived missing"}, status_code=503)
    return JSONResponse(_SITE)


@app.get("/api/countries.json")
def countries() -> JSONResponse:
    _load()
    return JSONResponse(_COUNTRIES or {})


@app.get("/api/country/{tag}")
def country(tag: str) -> JSONResponse:
    _load()
    data = (_COUNTRIES or {}).get(tag.upper())
    if not data:
        return JSONResponse({"error": "unknown tag"}, status_code=404)
    return JSONResponse(data)


@app.get("/map/{name}")
def map_file(name: str) -> FileResponse:
    allowed = {
        "ownership.png",
        "provinces.png",
        "provinces.json",
        "countries.json",
        "thumbnail.png",
    }
    if name not in allowed:
        raise HTTPException(404)
    path = derived_dir() / "thumbnail.png" if name == "thumbnail.png" else derived_dir() / "map" / name
    if not path.is_file():
        raise HTTPException(404)
    media = "image/png" if name.endswith(".png") else "application/json"
    return FileResponse(path, media_type=media)


def serve() -> None:
    import uvicorn

    host = os.environ.get("INTERWAR_HOST", "127.0.0.1")
    port = int(os.environ.get("INTERWAR_PORT", "1910"))
    uvicorn.run("app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    serve()
