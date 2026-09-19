"""1940 HOI4 国策树出图：treesnap 布局 PNG。总览见 :1939。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from corpus import list_mods
from treesnap_render import derived_dir, ensure_full_tree_png, render_focus_trees_png

log = logging.getLogger("focus1940")
_STATIC = Path(__file__).resolve().parent / "static"
_SAFE_PNG = re.compile(r"^[A-Za-z0-9_.-]+\.png$")

app = FastAPI(title="Patchouli Focus Tree PNG")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        _STATIC / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"ok": "1", "source": "1940", "role": "png"}


@app.get("/api/mods")
def api_mods() -> JSONResponse:
    return JSONResponse({"ok": True, "mods": list_mods()})


@app.get("/api/focus-trees")
def api_focus_trees(
    mod: str = Query("TFR", description="模组 short / id / alias，或 vanilla"),
    tag: str = Query("PRC", description="国家 tag，如 PRC"),
    tree_id: str | None = Query(None, description="可选：只要某一 focus_tree id"),
    lang: str = Query("simp_chinese"),
    force: bool = Query(False, description="强制重渲 PNG"),
) -> JSONResponse:
    result = render_focus_trees_png(
        mod=mod, tag=tag, tree_id=tree_id, lang=lang, force=force
    )
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.get("/api/focus-tree-full")
def api_focus_tree_full(
    mod: str = Query(...),
    tag: str = Query(...),
    tree_id: str = Query(...),
    lang: str = Query("simp_chinese"),
    force: bool = Query(False),
    download: bool = Query(True),
) -> FileResponse:
    result = ensure_full_tree_png(
        mod=mod, tag=tag, tree_id=tree_id, lang=lang, force=force
    )
    if not result.get("ok"):
        raise HTTPException(400, detail=result)
    name = str(result["image"])
    path = Path(str(result["path"]))
    if not path.is_file():
        raise HTTPException(500, detail="full_png_missing")
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(
        path,
        media_type="image/png",
        filename=name if download else None,
        headers=headers,
    )


@app.get("/renders/{name}")
def renders(
    name: str,
    download: bool = Query(False, description="true 时作为附件下载"),
) -> FileResponse:
    if not _SAFE_PNG.match(name):
        raise HTTPException(404)
    path = derived_dir() / name
    if not path.is_file():
        raise HTTPException(404)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(
        path,
        media_type="image/png",
        filename=name if download else None,
        headers=headers,
    )


def serve() -> None:
    import uvicorn

    host = os.environ.get("FOCUS_HOST", "127.0.0.1")
    port = int(os.environ.get("FOCUS_PORT", "1940"))
    uvicorn.run("app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    serve()
