"""
Phase 3: FastAPI backend.

Ties together: the continuous background ingest worker (Redis live
state), SQLite (fault history), REST endpoints, an SSE stream for
live updates, and basic PGM/PVW switcher control.

Setup:
    pip install fastapi uvicorn redis
    (Redis via Memurai -- see project README. SQLite needs no install.)

Run:
    uvicorn main:app --reload
"""

import asyncio
import json
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
from ingest_worker import IngestWorker
from ingest_batch import REDIS_HOST, REDIS_PORT, REDIS_KEY_PREFIX


worker = IngestWorker()
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

TALLY_PGM_KEY = "tally:pgm"
TALLY_PVW_KEY = "tally:pvw"
SSE_POLL_INTERVAL_SECONDS = 1.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="NDI Broadcast Signal Monitor", lifespan=lifespan)

# Dev-only CORS: the dashboard is a separate static file/origin from this
# API, so browsers block fetch()/EventSource without this. Wide open here
# because this never leaves your own machine for this project -- don't
# carry allow_origins=["*"] into anything actually deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TallyUpdate(BaseModel):
    pgm: str | None = None
    pvw: str | None = None


def _list_source_names() -> list[str]:
    keys = redis_client.keys(f"{REDIS_KEY_PREFIX}:*")
    # Exclude the ":faults" list keys -- only the summary hashes are sources.
    return sorted(
        k.split(":", 1)[1]
        for k in keys
        if not k.endswith(":faults")
    )


def _get_source_status(name: str) -> dict:
    key = f"{REDIS_KEY_PREFIX}:{name}"
    data = redis_client.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail=f"No status found for source '{name}'")
    return {"source_name": name, **data}


@app.get("/sources")
def list_sources():
    """List all known sources with their current live status."""
    return [_get_source_status(name) for name in _list_source_names()]


@app.get("/sources/{name}/status")
def get_source_status(name: str):
    """Live status for one source, straight from Redis."""
    return _get_source_status(name)


@app.get("/sources/{name}/faults")
def get_source_faults(name: str, limit: int = 50):
    """Persisted fault history for one source, from SQLite."""
    return db.get_recent_faults(name, limit=limit)


@app.get("/tally")
def get_tally():
    """Current PGM/PVW selection."""
    return {
        "pgm": redis_client.get(TALLY_PGM_KEY),
        "pvw": redis_client.get(TALLY_PVW_KEY),
    }


@app.post("/tally")
def set_tally(update: TallyUpdate):
    """
    Switcher control: set which source is PGM (live) and/or PVW (preview).
    Only the fields provided are updated -- sending just {"pgm": "..."}
    leaves PVW untouched.
    """
    known_sources = set(_list_source_names())

    if update.pgm is not None:
        if update.pgm not in known_sources:
            raise HTTPException(status_code=404, detail=f"Unknown source '{update.pgm}'")
        redis_client.set(TALLY_PGM_KEY, update.pgm)

    if update.pvw is not None:
        if update.pvw not in known_sources:
            raise HTTPException(status_code=404, detail=f"Unknown source '{update.pvw}'")
        redis_client.set(TALLY_PVW_KEY, update.pvw)

    return get_tally()


async def _sse_event_stream():
    while True:
        sources = [_get_source_status(name) for name in _list_source_names()]
        tally = {
            "pgm": redis_client.get(TALLY_PGM_KEY),
            "pvw": redis_client.get(TALLY_PVW_KEY),
        }
        payload = {"sources": sources, "tally": tally}
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)


@app.get("/stream")
async def stream():
    """
    Server-Sent Events endpoint: pushes source status + tally state
    every SSE_POLL_INTERVAL_SECONDS. The dashboard (Phase 4) connects
    here for live updates instead of polling REST endpoints itself.
    """
    return StreamingResponse(_sse_event_stream(), media_type="text/event-stream")


@app.get("/health")
def health():
    """Basic liveness check for the API itself (not the NDI sources)."""
    return {"status": "ok"}
