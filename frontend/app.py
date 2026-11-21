"""FastAPI application for the Senor Agent frontend."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from frontend import config_io, modality_flow, resources
from frontend.runner import manager

FRONTEND_DIR = Path(__file__).resolve().parent
STATIC_DIR = FRONTEND_DIR / "static"
TEMPLATES = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


def _asset_url(path: str) -> str:
    revision = (STATIC_DIR / path).stat().st_mtime_ns
    return f"/static/{path}?v={revision}"


TEMPLATES.env.globals["asset_url"] = _asset_url


class RunRequest(BaseModel):
    query: str = Field(..., min_length=1)
    tick_loop: bool = False


class StopRequest(BaseModel):
    run_id: Optional[str] = None


class ConfigUpdate(BaseModel):
    api_settings: Optional[dict] = None
    dataset_name: Optional[str] = None
    graph_settings: Optional[dict] = None
    execution_settings: Optional[dict] = None
    llm_virtual_env: Optional[dict] = None


class VirtualWorldUpdate(BaseModel):
    virtual_modality: Optional[bool] = None
    modalities: Optional[list] = None
    date_range: Optional[dict] = None
    har_labels: Optional[list] = None
    har_labels_by_modality: Optional[dict] = None
    dependencies: Optional[list] = None
    environment_info: Optional[str] = None
    events: Optional[list] = None
    use_cache: Optional[bool] = None
    rebuild: bool = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.execution.process_pool import close_pool, warmup_pool
    from src.graph.graph import build_graph

    build_graph()
    warmup_pool()
    try:
        yield
    finally:
        close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="Senor Agent", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def main_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request=request, name="main.html", context={"page": "main", "workspace": "main"},
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/browse")
    async def browse_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request=request, name="main.html", context={"page": "main", "workspace": "browse"},
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/health")
    async def health():
        return {"ok": True, "status": manager.status()}

    @app.get("/api/status")
    async def status():
        return manager.status()

    @app.get("/api/modality-flow/demo")
    def modality_demo():
        return modality_flow.demo_flow()

    @app.get("/api/config")
    async def get_config():
        return config_io.public_config()

    @app.put("/api/config")
    async def put_config(body: ConfigUpdate):
        return config_io.apply_updates(body.model_dump(exclude_none=True))

    @app.get("/api/llm-virtual-env")
    async def get_llm_virtual_env():
        return _virtual_view()

    @app.put("/api/llm-virtual-env")
    async def put_llm_virtual_env(body: VirtualWorldUpdate):
        payload = body.model_dump(exclude_none=True)
        want_rebuild = bool(payload.pop("rebuild", True))
        result = config_io.apply_virtual_world_updates(payload)
        extra = dict(result)
        if want_rebuild and extra.get("config", {}).get("virtual_modality"):
            extra.update(_start_catalog_job(force=False, only_if_needed=True, needs_rebuild=extra.get("needs_rebuild")))
        return _virtual_view(extra)

    @app.post("/api/llm-virtual-env/rebuild")
    async def rebuild_llm_virtual_env():
        from src.llm_virtual_env.config import load_virtual_env

        if not load_virtual_env().get("virtual_modality"):
            raise HTTPException(status_code=400, detail="虚拟模态已关闭，无法生成 vocabulary/grammar")
        extra = _start_catalog_job(force=True, only_if_needed=False)
        return _virtual_view(extra)

    @app.get("/api/llm-virtual-env/job")
    async def llm_virtual_env_job():
        return _virtual_view()

    @app.get("/api/browse/virtual")
    async def browse_virtual():
        return _virtual_view()

    @app.post("/api/llm-virtual-env/cache/clear")
    async def clear_virtual_cache():
        from src.llm_virtual_env.cache import clear_cache, list_entries

        clear_cache()
        return {"ok": True, "cache_cleared": True, "cache": list_entries()}

    @app.post("/api/run")
    async def start_run(body: RunRequest):
        try:
            session = manager.start(body.query, body.tick_loop, asyncio.get_running_loop())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": session.run_id}

    @app.post("/api/run/stop")
    async def stop_run(body: StopRequest = StopRequest()):
        try:
            return manager.cancel(body.run_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/run/{run_id}/stop")
    async def stop_run_by_id(run_id: str):
        try:
            return manager.cancel(run_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/run/{run_id}/events")
    async def run_events(run_id: str):
        session = manager.get(run_id)
        if session is None:
            raise HTTPException(status_code=404, detail="run not found")

        async def generate():
            history, queue = session.subscribe()
            try:
                for item in history:
                    yield _sse(item["event"], item["data"])
                    if item["event"] in {"done", "run_error", "cancelled"}:
                        return
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        if session.done:
                            while True:
                                try:
                                    item = queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                                yield _sse(item["event"], item["data"])
                            break
                        continue
                    yield _sse(item["event"], item["data"])
                    if item["event"] in {"done", "run_error", "cancelled"}:
                        break
            finally:
                session.unsubscribe(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/browse/filters")
    async def browse_filters():
        return resources.browse_filters()

    @app.get("/api/browse/sensor")
    async def browse_sensor(
        modality: Optional[str] = None,
        date: Optional[str] = None,
    ):
        return resources.list_sensor_items(modality=modality, date=date)

    @app.get("/api/browse/cache")
    async def browse_cache(
        modality: Optional[str] = None,
        date: Optional[str] = None,
        tool: Optional[str] = None,
    ):
        execution = resources.list_execution_cache(modality=modality, date=date, tool=tool)
        others = resources.list_other_caches()
        return {"execution": execution, **others}

    @app.get("/api/browse/intermediate")
    async def browse_intermediate(
        modality: Optional[str] = None,
        date: Optional[str] = None,
    ):
        return resources.list_intermediate(modality=modality, date=date)

    @app.get("/api/browse/tools")
    async def browse_tools():
        return resources.list_tools_and_models()

    @app.get("/api/media")
    async def media(path: str = Query(...)):
        decoded = unquote(path)
        if not resources.is_allowed_path(decoded):
            raise HTTPException(status_code=403, detail="path is not allowed")
        resolved = resources.resolve_path(decoded)
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(resolved)

    @app.get("/api/thumbnail")
    async def thumbnail(path: str = Query(...)):
        decoded = unquote(path)
        payload = resources.first_frame_jpeg(decoded)
        if payload is None:
            raise HTTPException(status_code=404, detail="thumbnail unavailable")
        return Response(content=payload, media_type="image/jpeg")

    return app


def _start_catalog_job(*, force: bool, only_if_needed: bool, needs_rebuild: bool | None = None) -> dict:
    from src.llm_virtual_env.generator import catalog_needs_rebuild
    from src.llm_virtual_env.jobs import start

    if only_if_needed:
        required = catalog_needs_rebuild() if needs_rebuild is None else bool(needs_rebuild)
        if not required:
            return {
                "rebuild_started": False,
                "rebuild": {"ok": True, "skipped": True, "reason": "unchanged"},
                "rebuild_job": {
                    "status": "skipped",
                    "error": None,
                    "result": {"ok": True, "skipped": True, "reason": "unchanged"},
                    "force": False,
                    "started_at": None,
                    "finished_at": None,
                },
            }
    job, started = start(force=force)
    return {
        "rebuild_started": started,
        "rebuild_job": job,
        "already_running": (not started) and job.get("status") == "running",
    }


def _virtual_view(extra: dict | None = None) -> dict:
    from src.llm_virtual_env.cache import list_entries
    from src.llm_virtual_env.catalog import catalog_summary
    from src.llm_virtual_env.jobs import snapshot

    payload = {
        "ok": True,
        "config": config_io.public_virtual_env(),
        "catalog": catalog_summary(),
        "cache": list_entries(),
        "rebuild_job": snapshot(),
    }
    if extra:
        payload.update(extra)
        payload["rebuild_job"] = extra.get("rebuild_job") or snapshot()
    return payload


def _sse(event: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
