"""
Phase 5: integration tests for the full ingest pipeline.

Unlike test_fault_classifier.py (pure logic, no external services),
these tests exercise the REAL Redis and REAL SQLite code paths --
just fed by FixtureSourceProvider instead of real NDI hardware.

This needs a real Redis server reachable at localhost:6379. Locally
that's your Memurai install; in CI, a redis service container (see
.github/workflows/ci.yml) -- either way, no NDI/cyndilib involved.

Run:
    pytest test_ingest_pipeline.py -v
"""

import os
import tempfile

import pytest
import redis

import db
from ingest_core import build_health_report, write_to_redis, REDIS_KEY_PREFIX
from fixture_source_provider import (
    FixtureSourceProvider,
    make_healthy_frames,
    make_degraded_frames,
)


@pytest.fixture
def redis_client():
    client = redis.Redis(host="localhost", port=6379, decode_responses=True)
    try:
        client.ping()
    except redis.exceptions.ConnectionError:
        pytest.skip("No Redis server reachable at localhost:6379 -- skipping.")
    yield client
    # Clean up any keys this test run may have written.
    for key in client.keys(f"{REDIS_KEY_PREFIX}:test_*"):
        client.delete(key)


@pytest.fixture
def temp_db(monkeypatch):
    """Points db.py at a throwaway SQLite file for this test only."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    yield
    os.remove(path)


def test_healthy_fixture_source_reports_healthy_in_redis(redis_client):
    provider = FixtureSourceProvider({
        "test_cam_healthy": make_healthy_frames(30),
    })

    source_names = provider.discover_sources()
    assert source_names == ["test_cam_healthy"]

    frames = provider.capture_batch("test_cam_healthy", 30)
    report = build_health_report("test_cam_healthy", frames, frames_attempted=30)
    write_to_redis(redis_client, report)

    stored = redis_client.hgetall(f"{REDIS_KEY_PREFIX}:test_cam_healthy")
    assert stored["status"] == "healthy"
    assert stored["frames_captured"] == "30"
    assert stored["resolution"] == "1280x720"


def test_degraded_fixture_source_reports_fault_in_redis(redis_client):
    provider = FixtureSourceProvider({
        "test_cam_degraded": make_degraded_frames(count=20),  # 20 of 30 attempted
    })

    frames = provider.capture_batch("test_cam_degraded", 30)
    report = build_health_report("test_cam_degraded", frames, frames_attempted=30)
    write_to_redis(redis_client, report)

    stored = redis_client.hgetall(f"{REDIS_KEY_PREFIX}:test_cam_degraded")
    assert stored["status"] in ("P1", "P2", "P3")  # some fault, not healthy
    assert int(stored["fault_count"]) > 0

    faults_key = f"{REDIS_KEY_PREFIX}:test_cam_degraded:faults"
    fault_entries = redis_client.lrange(faults_key, 0, -1)
    assert len(fault_entries) > 0
    assert any("frame_drop" in entry for entry in fault_entries)


def test_source_loss_scenario_end_to_end(redis_client):
    provider = FixtureSourceProvider({
        "test_cam_lost": [],  # no frames at all -- total source loss
    })

    frames = provider.capture_batch("test_cam_lost", 30)
    report = build_health_report("test_cam_lost", frames, frames_attempted=30)
    write_to_redis(redis_client, report)

    stored = redis_client.hgetall(f"{REDIS_KEY_PREFIX}:test_cam_lost")
    assert stored["status"] == "P1"
    assert stored["resolution"] == "unknown"


def test_fault_events_persist_to_sqlite(temp_db):
    provider = FixtureSourceProvider({
        "test_cam_db": make_degraded_frames(count=10),  # severe drop
    })

    frames = provider.capture_batch("test_cam_db", 30)
    report = build_health_report("test_cam_db", frames, frames_attempted=30)

    db.upsert_source("test_cam_db", status="P1")
    db.insert_fault_events("test_cam_db", report.faults)

    history = db.get_recent_faults("test_cam_db", limit=10)
    assert len(history) > 0
    assert history[0]["fault_type"] in ("frame_drop", "source_loss", "timing_jitter")


def test_no_faults_means_nothing_written_to_sqlite(temp_db):
    """Confirms insert_fault_events is a no-op for a clean, healthy source."""
    provider = FixtureSourceProvider({
        "test_cam_clean": make_healthy_frames(30),
    })

    frames = provider.capture_batch("test_cam_clean", 30)
    report = build_health_report("test_cam_clean", frames, frames_attempted=30)

    db.upsert_source("test_cam_clean", status="healthy")
    db.insert_fault_events("test_cam_clean", report.faults)

    history = db.get_recent_faults("test_cam_clean", limit=10)
    assert history == []
