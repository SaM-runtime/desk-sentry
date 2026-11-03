import json
import os
import re
from pathlib import Path

from desk_sentry.config import Settings
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.sentry import Status
from tests_support import make_engine


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def read(folder: Path) -> list[dict]:
    import gzip

    lines = []
    for f in sorted(folder.glob("*.jsonl.gz")):
        text = gzip.decompress(f.read_bytes()).decode("utf-8")
        lines += [json.loads(line) for line in text.splitlines()]
    return lines


def test_quiet_frames_are_thinned_but_interesting_ones_are_not(tmp_path: Path) -> None:
    clock = Clock()
    log = DiagnosticsLog(tmp_path, clock=clock)
    for _ in range(20):  # 2 s of self-only frames at 10 Hz
        log.analysis(status="clear", level="quiet", alert_started=False)
        clock.t += 0.1
    for _ in range(20):  # 2 s of a passer-by: 2 Hz
        log.analysis(status="watching", level="people", alert_started=False)
        clock.t += 0.1
    for _ in range(10):  # 1 s with someone alerting: every analysis
        log.analysis(status="alert", level="full", alert_started=False)
        clock.t += 0.1
    log.flush()
    records = read(tmp_path)
    assert sum(r["status"] == "clear" for r in records) <= 3
    assert 3 <= sum(r["status"] == "watching" for r in records) <= 5
    assert sum(r["status"] == "alert" for r in records) == 10


def test_folder_is_capped_oldest_first(tmp_path: Path, monkeypatch) -> None:
    from desk_sentry import diagnostics

    monkeypatch.setattr(diagnostics, "MAX_FOLDER_BYTES", 3000)
    monkeypatch.setattr(diagnostics, "MAX_FILE_BYTES", 1000)
    for day in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"):
        (tmp_path / f"{day}.jsonl").write_text("x" * 900, encoding="utf-8")
    log = DiagnosticsLog(tmp_path)  # real clock: the files above are fresh
    log.write("marker", marker="x")
    log.flush()
    names = sorted(p.name for p in tmp_path.glob("*.jsonl"))
    assert "2026-09-01.jsonl" not in names and "2026-09-02.jsonl" not in names
    assert "2026-09-04.jsonl" in names and len(names) == 2
    assert len(list(tmp_path.glob("*.jsonl.gz"))) == 1


def test_unwritable_folder_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    DiagnosticsLog(blocker / "sub").write("marker", marker="false_alert")


def test_engine_writes_numbers_only_records(tmp_path: Path) -> None:
    engine, rec, _ = make_engine(enter_after=2.5)
    engine.diagnostics = DiagnosticsLog(tmp_path)
    engine.start()
    try:
        rec.wait_for(lambda s: s.alert_started, timeout=6)
    finally:
        engine.stop()
    records = read(tmp_path)
    kinds = {r["kind"] for r in records}
    assert {"armed", "camera_ok", "analysis"} <= kinds
    started = [r for r in records if r["kind"] == "analysis" and r["alert_started"]]
    assert len(started) == 1
    track = started[0]["tracks"][0]
    assert set(track) >= {"box", "score", "role", "motion", "disp", "growth"}
    # Only small numbers per record: nothing that could be pixels.
    assert max(len(json.dumps(r)) for r in records) < 4000


def test_engine_logs_errors_with_traceback(tmp_path: Path) -> None:
    engine, rec, _ = make_engine(enter_after=99, fail_first=1)
    engine.diagnostics = DiagnosticsLog(tmp_path)
    engine.start()
    try:
        rec.wait_for(lambda s: s.status is Status.ERROR)
    finally:
        engine.stop()
    errors = [r for r in read(tmp_path) if r["kind"] == "error"]
    assert errors and errors[0]["error"] == "RuntimeError"
    assert "inference blew up" in errors[0]["trace"]


def test_error_trace_removes_absolute_paths_but_keeps_locations(tmp_path: Path) -> None:
    engine, _, _ = make_engine()
    engine.diagnostics = DiagnosticsLog(tmp_path)
    source = "def fail():\n    raise ValueError('cannot open ' + location)\nfail()"
    namespace = {"location": os.path.join(os.path.expanduser("~"), "private", "input.dat")}
    filename = str(tmp_path / "desk_sentry" / "fixture.py")
    try:
        exec(compile(source, filename, "exec"), namespace)
    except ValueError as exc:
        engine._record_error(exc)
    engine.diagnostics.flush()
    record = next(r for r in read(tmp_path) if r["kind"] == "error")
    trace = record["trace"]
    assert os.path.expanduser("~") not in trace
    assert str(tmp_path) not in trace
    assert not re.search(r"[A-Za-z]:[/\\]", trace)
    assert "desk_sentry/fixture.py:2" in trace
    assert "ValueError" in trace and "cannot open" in trace and "input.dat" in trace


def test_settings_default_is_off() -> None:
    assert Settings().diagnostics is False


def test_records_survive_as_concatenated_gzip_members(tmp_path: Path) -> None:
    from desk_sentry import diagnostics

    log = DiagnosticsLog(tmp_path, clock=Clock())
    for i in range(diagnostics.FLUSH_LINES * 2 + 7):
        log.write("marker", n=i)
    log.flush()
    assert [r["n"] for r in read(tmp_path)] == list(range(diagnostics.FLUSH_LINES * 2 + 7))


def test_unflushed_records_wait_in_memory_until_flush(tmp_path: Path) -> None:
    log = DiagnosticsLog(tmp_path, clock=Clock())
    log.write("marker", n=1)
    assert not list(tmp_path.glob("*.gz"))
    log.flush()
    assert [r["n"] for r in read(tmp_path)] == [1]
