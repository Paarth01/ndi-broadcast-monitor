# NDI Broadcast Signal Monitor

A real-time signal health monitor and basic switcher for live NDI video sources, built to extend an earlier simulated broadcast-signal project ([IP Broadcast Signal Health Monitor](#)) with genuine, hands-on signal ingestion — not synthetic data.

## What this actually does

- Discovers and connects to real **NDI** video sources on the local network (via OBS Studio + the DistroAV plugin, which outputs standard NDI from any webcam/screen source)
- Runs a **continuous background ingest worker** (a plain Python thread, not asyncio, since `cyndilib`'s native capture calls are blocking) that discovers sources, captures frames, and classifies faults on a repeating cycle
- Classifies faults using an **ETSI TR 101 290-style Priority 1/2/3 severity model**, with **signal faults** (source loss, frame drop rate, resolution instability) and **timing faults** (frame interval jitter) classified separately, then merged into an overall severity — mirroring the "dedicated sync-health classifier isolating timing faults from signal faults" design from the original simulated project
- Exposes live status via REST endpoints and a Server-Sent Events stream, plus basic **PGM/PVW switcher control**
- Persists fault history to **SQLite**; live per-source state and tally state live in **Redis**
- Ships with a dependency-free single-file dashboard (`dashboard.html` — React via CDN, no build step) showing live source tiles, severity badges, switcher controls, and fault history

## Why this exists

The earlier project implemented the same Priority 1/2/3 fault model against **simulated** SMPTE ST 2110 / PTP data. This project deliberately extends that work to **real, live video signals** — genuine NDI sources, genuine network conditions, genuine timing behavior — to validate that the fault-classification design holds up against real-world signal noise, not just injected synthetic faults. It was built specifically to close the "this is all simulated" gap in a broadcast engineering internship application.

## Architecture

```
┌──────────────────────────┐     ┌───────────────────────────────┐
│  OBS Source #1           │     │  OBS Source #2                │
│  (Webcam → DistroAV      │     │  (Screen Capture → DistroAV   │
│   Main Output)           │     │   NDI Filter, distinct name)  │
└────────────┬─────────────┘     └─────────────────┬─────────────┘
             │                                     │
             └───────────────────┬─────────────────┘
                                 │      (LAN, NDI protocol)
                                 ▼
         ┌──────────────────────────────────┐
         │  ingest_worker.py                │
         │  (continuous background thread,  │
         │   started by FastAPI on startup) │
         │  → CyndilibSourceProvider        │
         │    (ingest_batch.py)             │
         └─────────────────┬────────────────┘
                           │
                           ▼
         ┌──────────────────────────────────┐
         │  ingest_core.py                  │
         │  build_health_report()           │
         │        │                         │
         │        ▼                         │
         │  fault_classifier.py             │
         │  (Priority 1/2/3, signal vs      │
         │   timing faults, kept separate   │
         │   then merged)                   │
         └─────────────────┬────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
   ┌──────────────────┐       ┌──────────────────┐
   │  Redis           │       │  SQLite (db.py)  │
   │  (live state,    │       │  (fault history, │
   │   tally state)   │       │  source records) │
   └────────┬─────────┘       └────────┬─────────┘
            │                          │
            └────────────┬─────────────┘
                          ▼
         ┌──────────────────────────────────┐
         │  main.py (FastAPI)               │
         │  - REST: /sources, /tally        │
         │  - SSE: /stream                  │
         │  - Switcher control (PGM/PVW)    │
         └────────────────┬─────────────────┘
                           │
                           ▼
         ┌──────────────────────────────────┐
         │  dashboard.html                  │
         │  (React via CDN, no build step)  │
         │  - Live source tiles             │
         │  - PGM/PVW switcher UI           │
         │  - Fault log + severity badges   │
         └──────────────────────────────────┘
```

## Project structure

```
fault_classifier.py       # Priority 1/2/3 classification -- pure logic, no NDI dependency
ingest_core.py             # SourceProvider interface, health report building, Redis writes -- no NDI dependency
ingest_batch.py            # Real NDI capture (CyndilibSourceProvider) + one-off CLI script
ingest_worker.py           # Continuous background thread version, used by the FastAPI app
fixture_source_provider.py # Synthetic SourceProvider for tests/CI -- no NDI dependency
db.py                      # SQLite persistence (fault history, source records)
main.py                    # FastAPI app: REST + SSE + switcher control + CORS
dashboard.html             # Single-file live dashboard (React via CDN)
test_fault_classifier.py   # Unit tests, synthetic data, no services needed
test_ingest_pipeline.py    # Integration tests, real Redis + real SQLite, fixture-based frames
requirements.txt
Dockerfile                 # Linux-only path -- see Docker section below
docker-compose.yml         # Redis only, deliberately
.github/workflows/ci.yml   # Runs the two test files above on every push
resume_bullet_update.md    # Suggested resume language for this project
interview_talking_points.md # Structured notes on the real bugs found/fixed, for interview use
```

## Setup

### Prerequisites
- Python 3.11
- [OBS Studio](https://obsproject.com/) + [DistroAV](https://github.com/DistroAV/DistroAV) plugin (NDI 6 Runtime required)
- Redis — via [Memurai](https://www.memurai.com/get-memurai) on Windows, a native Redis install on macOS/Linux, or `docker compose up redis` (see Docker section)

### Install
```bash
pip install -r requirements.txt
```
(SQLite ships with Python — no separate install needed.)

### Get real NDI sources running
1. Open OBS Studio.
2. Add a webcam source, enable DistroAV's **Main Output** (Tools → NDI Output settings).
3. For a second source, add an **NDI Filter** to a different OBS source (e.g. Display Capture) with a distinct name.
4. Confirm both are visible and showing real video in **NDI Studio Monitor** before running anything in this repo.

### Run
```bash
uvicorn main:app --reload
```
This starts the FastAPI app, which automatically starts the continuous background ingest worker and initializes the SQLite database on first run. Then open `dashboard.html` directly in a browser (no server needed for the dashboard itself — it talks to the API over HTTP).

## Testing

Two test suites, deliberately separated by hardware dependency:

```bash
pytest test_fault_classifier.py -v   # pure logic, synthetic data, no services -- 14 tests
pytest test_ingest_pipeline.py -v    # real Redis + real SQLite, fixture-based frames -- 5 tests
```

**Neither test file requires `cyndilib` or a real NDI source.** This is a deliberate architectural choice, not an oversight: GitHub-hosted CI runners have no local network with NDI devices on it, so live discovery/capture genuinely cannot run there. The `SourceProvider` interface (`ingest_core.py`) is implemented twice — once for real hardware (`CyndilibSourceProvider` in `ingest_batch.py`) and once for tests (`FixtureSourceProvider`) — so the fault-classification and persistence logic can be fully verified in CI against `.github/workflows/ci.yml`, which spins up a Redis service container but never touches NDI.

All 19 tests have been verified passing in two independent environments: a clean Linux sandbox with no NDI runtime installed at all, and the actual Windows development machine — confirming the CI approach is genuinely portable, not just theoretically sound.

## Docker

```bash
docker compose up redis
```

Only Redis is containerized, deliberately. The FastAPI app depends on `cyndilib`/NDI discovery, which needs direct access to the host's LAN for mDNS-based source discovery — Docker's network isolation (especially on Docker Desktop for Windows) actively interferes with this. Containerizing the ingest worker would trade a working NDI connection for a demo that looks more "containerized" but doesn't actually discover sources. A `Dockerfile` is included for Linux deployment, where `network_mode: host` (commented out in `docker-compose.yml`) works natively — on Windows/Mac, run the app directly with `uvicorn` on the host and use Docker only for Redis.

## CI

`.github/workflows/ci.yml` runs on every push to `main`: spins up a Redis service container, installs dependencies from `requirements.txt` minus `cyndilib` (not needed for these tests), and runs both test files above. No NDI hardware or `cyndilib` install involved at any point.

## Notable debugging along the way

This project didn't work cleanly on the first attempt, and the fixes are worth documenting because they reflect real signal-engineering problems, not typos:

- **Native access-violation crash**: calling `capture_video()` immediately after constructing a `Receiver` with a source crashed with `STATUS_ACCESS_VIOLATION` (exit code `-1073741819`) — a native-level fault with no Python traceback. Fixed by constructing the receiver empty, explicitly registering a `VideoFrameSync`, then calling `set_source()` and waiting for `is_connected()` — matching `cyndilib`'s own documented examples rather than assuming the constructor's `source=` argument was equivalent.
- **False-perfect frame timing**: initial captures showed a suspicious `0.0ms` average interval — not because timing was perfect, but because captures were running faster than the source's real frame rate and repeatedly re-reading the same buffered frame. Fixed by pacing captures against the source's actual `get_frame_rate()`.
- **Under-tiered severity**: a single jitter ratio threshold classified a 200ms hiccup and a 153-second process stall identically as P2. Live testing surfaced this directly — a real multi-minute stall (most likely the dev machine briefly sleeping) showed up in the fault log at the same severity as routine jitter. Fixed by adding a tiered/absolute threshold (`JITTER_RATIO_P1` + `JITTER_ABSOLUTE_P1_MS`) so multi-second stalls correctly escalate to P1, confirmed by `test_multi_second_stall_is_p1_not_p2`.
- **CI-import coupling**: the original `ingest_batch.py` imported `cyndilib` at the top level, which meant any test importing shared logic from it would transitively require `cyndilib` just to import — breaking in any CI environment with no NDI runtime. Fixed by splitting the file into `ingest_core.py` (zero NDI dependency: `SourceProvider` interface, health report building, Redis writes) and a thin `ingest_batch.py` containing only the real `CyndilibSourceProvider`.

## Known limitations

- The continuous ingest loop runs in a single background thread — sources are captured sequentially, not in parallel. Fine for 2-3 sources, would need a rework (one thread per source, or async where possible) to scale further.
- No authentication on the API or dashboard — this is a local development/demo tool, not a deployed service.
- The dashboard is intentionally a single dependency-free HTML file rather than a full Vite/React/TypeScript build, given how much of this project's time went into hardware/signal debugging rather than frontend tooling.
- The app itself isn't containerized on Windows/Mac for the NDI-networking reasons described in the Docker section above.
