"""
Unit tests for fault_classifier.py.

All synthetic data -- no NDI source, no hardware, no network needed.
This is exactly the kind of test that CAN run in GitHub Actions CI,
unlike the real ingest layer.

Run:
    pip install pytest
    pytest test_fault_classifier.py -v
"""

from fault_classifier import (
    FrameMetrics,
    Severity,
    classify_signal_faults,
    classify_timing_faults,
    classify_source,
    worst_severity,
)


def make_frames(count: int, start_time: float = 1000.0,
                 interval: float = 0.033, width: int = 1280,
                 height: int = 720) -> list[FrameMetrics]:
    """Helper: build a clean, evenly-paced list of frames for a baseline."""
    return [
        FrameMetrics(timestamp=start_time + i * interval, width=width, height=height)
        for i in range(count)
    ]


# --- Signal fault tests -----------------------------------------------

def test_no_frames_is_source_loss_p1():
    faults = classify_signal_faults("cam1", frames=[], frames_attempted=10)
    assert len(faults) == 1
    assert faults[0].fault_type == "source_loss"
    assert faults[0].severity == Severity.P1


def test_zero_attempted_produces_no_faults():
    faults = classify_signal_faults("cam1", frames=[], frames_attempted=0)
    assert faults == []


def test_healthy_capture_produces_no_signal_faults():
    frames = make_frames(30)
    faults = classify_signal_faults("cam1", frames, frames_attempted=30)
    assert faults == []


def test_severe_drop_rate_is_p1():
    frames = make_frames(10)  # 10 out of 30 attempted = 67% drop
    faults = classify_signal_faults("cam1", frames, frames_attempted=30)
    drop_faults = [f for f in faults if f.fault_type == "frame_drop"]
    assert len(drop_faults) == 1
    assert drop_faults[0].severity == Severity.P1


def test_moderate_drop_rate_is_p2():
    frames = make_frames(24)  # 24 out of 30 attempted = 20% drop
    faults = classify_signal_faults("cam1", frames, frames_attempted=30)
    drop_faults = [f for f in faults if f.fault_type == "frame_drop"]
    assert len(drop_faults) == 1
    assert drop_faults[0].severity == Severity.P2


def test_minor_drop_rate_is_p3():
    frames = make_frames(29)  # 29 out of 30 attempted = ~3.3% drop
    faults = classify_signal_faults("cam1", frames, frames_attempted=30)
    drop_faults = [f for f in faults if f.fault_type == "frame_drop"]
    assert len(drop_faults) == 1
    assert drop_faults[0].severity == Severity.P3


def test_resolution_change_mid_capture_is_p2():
    frames = make_frames(15, width=1280, height=720) + make_frames(
        15, start_time=1001.0, width=1920, height=1080
    )
    faults = classify_signal_faults("cam1", frames, frames_attempted=30)
    res_faults = [f for f in faults if f.fault_type == "resolution_mismatch"]
    assert len(res_faults) == 1
    assert res_faults[0].severity == Severity.P2


# --- Timing fault tests -------------------------------------------------

def test_evenly_paced_frames_produce_no_timing_faults():
    frames = make_frames(30, interval=0.033)
    faults = classify_timing_faults("cam1", frames)
    assert faults == []


def test_too_few_frames_produces_no_timing_faults():
    frames = make_frames(2)
    faults = classify_timing_faults("cam1", frames)
    assert faults == []


def test_large_single_gap_is_p2_jitter():
    frames = make_frames(10, interval=0.033)
    # Inject one large gap partway through.
    frames[5] = FrameMetrics(
        timestamp=frames[4].timestamp + 0.5,  # huge gap vs ~33ms median
        width=frames[5].width,
        height=frames[5].height,
    )
    faults = classify_timing_faults("cam1", frames)
    jitter_faults = [f for f in faults if f.fault_type == "timing_jitter"]
    assert len(jitter_faults) == 1
    assert jitter_faults[0].severity == Severity.P2


def test_multi_second_stall_is_p1_not_p2():
    frames = make_frames(10, interval=0.033)
    # Simulate a genuine stall -- e.g. the process was briefly
    # suspended -- not just a routine hiccup.
    frames[5] = FrameMetrics(
        timestamp=frames[4].timestamp + 5.0,  # 5 second gap
        width=frames[5].width,
        height=frames[5].height,
    )
    faults = classify_timing_faults("cam1", frames)
    jitter_faults = [f for f in faults if f.fault_type == "timing_jitter"]
    assert len(jitter_faults) == 1
    assert jitter_faults[0].severity == Severity.P1


# --- Combined classification + worst_severity ---------------------------

def test_classify_source_combines_signal_and_timing_faults():
    frames = make_frames(10)  # severe drop (10/30) AND resolution stable
    faults = classify_source("cam1", frames, frames_attempted=30)
    fault_types = {f.fault_type for f in faults}
    assert "frame_drop" in fault_types


def test_worst_severity_picks_p1_over_p2_and_p3():
    faults = classify_source("cam1", frames=[], frames_attempted=10)  # source_loss = P1
    assert worst_severity(faults) == Severity.P1


def test_worst_severity_returns_none_when_no_faults():
    frames = make_frames(30)
    faults = classify_source("cam1", frames, frames_attempted=30)
    assert worst_severity(faults) is None
