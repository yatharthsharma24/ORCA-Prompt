"""FastAPI service: streaming /api/ask, run history, catalog, health and the web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orca import __version__
from orca.config import Settings, get_settings
from orca.data.store import MarineStore, QueryError
from orca.knowledge import KnowledgeBase
from orca.llm import LLM, LLMError, OllamaLLM
from orca.orchestrator import Orchestrator
from orca.runs import RunStore

log = logging.getLogger("orca.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

EXAMPLES = [
    "Did weak monsoon upwelling off Kerala affect oil sardine landings in the following year?",
    "How severe was coral heat stress at Lakshadweep reefs in 2016 and 2024, and what do the DHW values mean for bleaching?",
    "Is sea surface temperature in the Andaman Sea rising, and how fast per decade?",
    "Which months after 2013 had marine-heatwave-like SST in the Lakshadweep Sea?",
    "How have olive ridley nesting counts in the NW Bay of Bengal changed, and what is the species' IUCN status?",
    "How many OBIS occurrence records exist for whale sharks in the NE Arabian Sea since 2000?",
]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@dataclass
class AppState:
    settings: Settings
    llm: LLM
    store: MarineStore
    kb: KnowledgeBase
    runs: RunStore
    http: httpx.AsyncClient
    orchestrator: Orchestrator
    gate: asyncio.Semaphore
    waiting: int = 0


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        own_llm = llm is None
        the_llm = llm or OllamaLLM(
            settings.ollama_base_url, temperature=settings.temperature, num_ctx=settings.num_ctx,
            think=settings.think, timeout_s=settings.llm_timeout_s, keep_alive=settings.keep_alive,
        )
        http = httpx.AsyncClient(timeout=settings.obis_timeout_s, headers={"User-Agent": f"ORCA-Prompt/{__version__}"})
        store = MarineStore(settings.data_db, row_limit=settings.sql_row_limit)
        kb = KnowledgeBase(settings.knowledge_dir)
        app.state.orca = AppState(
            settings=settings, llm=the_llm, store=store, kb=kb, runs=RunStore(settings.runs_db), http=http,
            orchestrator=Orchestrator(settings, the_llm, store, kb, http),
            gate=asyncio.Semaphore(settings.max_concurrent_runs),
        )
        log.info("ORCA ready: model=%s ollama=%s datasets=%d notes=%d", settings.model,
                 settings.ollama_base_url, len(store.datasets), len(kb.passages))
        yield
        await http.aclose()
        if own_llm and isinstance(the_llm, OllamaLLM):
            await the_llm.aclose()

    app = FastAPI(title="ORCA-Prompt", version=__version__, lifespan=lifespan,
                  description="Multi-agent ReAct assistant for marine ecosystem reasoning.")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "X-API-Key"])

    def state(request: Request) -> AppState:
        return request.app.state.orca

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if settings.api_key and not (x_api_key and secrets.compare_digest(x_api_key, settings.api_key)):
            raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header.")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/ready")
    async def ready(st: AppState = Depends(state)) -> JSONResponse:
        llm_health = await st.llm.health(settings.model) if hasattr(st.llm, "health") else {"reachable": True, "model_available": True}
        ok = bool(llm_health.get("reachable") and llm_health.get("model_available"))
        body = {"ready": ok, "llm": llm_health, "datasets": len(st.store.datasets), "knowledge_passages": len(st.kb.passages)}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/api/config")
    async def config(st: AppState = Depends(state)) -> dict[str, Any]:
        return {
            "version": __version__, "model": settings.model, "obis_enabled": settings.enable_obis,
            "llm_critic": settings.llm_critic, "auth_required": bool(settings.api_key),
            "max_plan_steps": settings.max_plan_steps, "examples": EXAMPLES,
        }

    @app.get("/api/datasets")
    async def datasets(st: AppState = Depends(state)) -> list[dict[str, Any]]:
        return [d.to_dict() for d in st.store.datasets]

    @app.get("/api/datasets/{dataset_id}")
    async def dataset(dataset_id: str, st: AppState = Depends(state)) -> dict[str, Any]:
        try:
            return st.store.dataset(dataset_id).to_dict()
        except QueryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs")
    async def list_runs(limit: int = 20, st: AppState = Depends(state)) -> list[dict[str, Any]]:
        return st.runs.list(max(1, min(limit, 100)))

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, st: AppState = Depends(state)) -> dict[str, Any]:
        run = st.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run

    @app.post("/api/ask", dependencies=[Depends(require_key)])
    async def ask(body: AskRequest, request: Request, stream: bool = True, st: AppState = Depends(state)):
        if st.waiting >= settings.max_queued_runs:
            raise HTTPException(status_code=429, detail="Too many questions queued; try again shortly.")
        run_id = uuid.uuid4().hex[:12]
        question = body.question.strip()
        if not stream:
            st.waiting += 1
            entered = False
            try:
                async with st.gate:
                    st.waiting -= 1
                    entered = True
                    return await _run(st, run_id, question)
            finally:
                if not entered:
                    st.waiting -= 1
        return StreamingResponse(_sse(st, run_id, question, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run_id})

    async def _run(st: AppState, run_id: str, question: str, emit=None) -> dict[str, Any]:
        st.runs.start(run_id, question)
        try:
            result = await st.orchestrator.run(question, emit or _noop, run_id=run_id)
        except LLMError as exc:
            st.runs.fail(run_id, str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except asyncio.CancelledError:
            st.runs.fail(run_id, "client disconnected", status="cancelled")
            raise
        except Exception as exc:
            log.exception("run %s failed", run_id)
            st.runs.fail(run_id, f"{type(exc).__name__}: {exc}")
            raise
        st.runs.finish(run_id, result)
        return result

    async def _sse(st: AppState, run_id: str, question: str, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            await queue.put((kind, payload))

        async def worker() -> None:
            st.waiting += 1
            entered = False
            try:
                if st.gate.locked():
                    await emit("queued", {"run_id": run_id, "position": st.waiting})
                async with st.gate:
                    st.waiting -= 1
                    entered = True
                    await _run(st, run_id, question, emit)
            except HTTPException as exc:
                await emit("error", {"run_id": run_id, "message": exc.detail})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await emit("error", {"run_id": run_id, "message": f"{type(exc).__name__}: {exc}"})
            finally:
                if not entered:
                    st.waiting -= 1
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            yield _event("accepted", {"run_id": run_id})
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _event(*item)
        finally:
            if not task.done():
                task.cancel()

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


async def _noop(_kind: str, _payload: dict[str, Any]) -> None:
    return None


def _event(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
