import sys
import time
from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from desk_sentry import dpapi
from desk_sentry.config import Settings
from desk_sentry.detector import Box, Detection
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.faces import Face
from desk_sentry.people import MAX_SAMPLES, Level, PeopleBook, normalize
from desk_sentry.sentry import AlertEngine, Policy, Role, Status
from desk_sentry.tracker import Tracker
from desk_sentry.ui.history import AlertHistory, HistoryWindow
from desk_sentry.ui.people import LabelDialog, PeopleEditor
from tests_support import FakeCamera, Recorder, ScriptedDetector

RNG = np.random.default_rng(7)


def face(seed: int, noise: float = 0.0) -> np.ndarray:
    base = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    v = base + RNG.normal(scale=noise, size=128).astype(np.float32)
    out = normalize(v)
    assert out is not None
    return out


def xor(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


def book(tmp_path: Path | None = None) -> PeopleBook:
    return PeopleBook(tmp_path / "people.bin" if tmp_path else None, protect=xor, unprotect=xor)


# ------------------------------------------------------------------ people book
def test_match_same_person_with_noise_and_reject_strangers() -> None:
    b = book()
    b.label("阿明", Level.LOW, face(1))
    b.label("小華", Level.HIGH, face(2))
    snap = b.snapshot()
    match = snap.match(face(1, noise=0.25))
    assert match is not None and match.name == "阿明" and match.level is Level.LOW
    assert snap.match(face(99)) is None
    assert snap.has_quiet_people


def test_ambiguous_match_is_unknown() -> None:
    b = book()
    shared = face(5)
    b.label("A", Level.LOW, shared)
    b.label("B", Level.HIGH, normalize(shared + 0.05 * face(6)))  # near-identical
    assert b.snapshot().match(shared) is None


def test_label_existing_name_adds_samples_and_updates_level() -> None:
    b = book()
    for _ in range(MAX_SAMPLES + 3):
        b.label("阿明", Level.HIGH, face(1, noise=0.1))
    b.label(" 阿明 ", Level.MEDIUM, face(1))
    assert len(b.people) == 1
    assert len(b.people[0].samples) == MAX_SAMPLES
    assert b.people[0].level is Level.MEDIUM
    with pytest.raises(ValueError):
        b.label("  ", Level.HIGH, face(1))


def test_encrypted_roundtrip_and_wrong_key(tmp_path: Path) -> None:
    b = book(tmp_path)
    b.label("阿明", Level.LOW, face(1))
    b.save()
    raw = (tmp_path / "people.bin").read_bytes()
    assert "阿明".encode() not in raw  # stored encrypted, not as plain text
    again = book(tmp_path)
    again.load()
    assert [p.name for p in again.people] == ["阿明"] and again.people[0].level is Level.LOW
    assert again.snapshot().match(face(1)) is not None

    def broken(_data: bytes) -> bytes:
        raise OSError("other Windows user")

    other = PeopleBook(tmp_path / "people.bin", protect=xor, unprotect=broken)
    other.load()
    assert other.people == [] and other.load_error
    assert (tmp_path / "people.bin").exists()  # not destroyed by a failed read


def test_clearing_everyone_deletes_the_file(tmp_path: Path) -> None:
    b = book(tmp_path)
    b.label("阿明", Level.LOW, face(1))
    b.save()
    b.clear()
    b.save()
    assert not (tmp_path / "people.bin").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_real_dpapi_roundtrip() -> None:
    secret = "臉部特徵".encode()
    sealed = dpapi.protect(secret)
    assert sealed != secret and dpapi.unprotect(sealed) == secret


# ------------------------------------------------------------- alert levels
W, H = 1280, 720
USER = Box(400, 250, 880, 720)
ZONE = (0.25, 0.25, 0.75, 1.0)


def walker(t: float) -> Box:
    x = 100 + 300 * t
    return Box(x, 150, x + 90, 450)


def run(engine, tracker, frames, identities=None, awaiting=None):
    out = []
    for now, dets in frames:
        tracks = tracker.update([Detection(b, 0.8) for b in dets], now)
        ids = identities(tracks) if identities else None
        wait = awaiting(tracks, now) if awaiting else None
        out.append(engine.assess(tracks, now, (W, H), identities=ids, awaiting=wait))
    return out


def armed(**policy) -> AlertEngine:
    engine = AlertEngine(Policy(self_zone=ZONE, **policy))
    engine.assess([], -3.0, (W, H))
    return engine


def walker_ids(level: Level):
    return lambda tracks: {t.track_id: level for t in tracks if t.box.height < 400}


def test_low_level_person_is_named_but_never_alerts() -> None:
    engine, tracker = armed(), Tracker()
    frames = [(i * 0.1, [USER, walker(i * 0.1)]) for i in range(10)]
    results = run(engine, tracker, frames, identities=walker_ids(Level.LOW))
    assert all(r.intruders == 0 and not r.alert_started for r in results)
    assert Role.KNOWN in results[-1].roles.values()
    assert results[-1].status is Status.WATCHING


def test_medium_level_alert_is_marked_medium() -> None:
    engine, tracker = armed(), Tracker()
    frames = [(i * 0.1, [USER, walker(i * 0.1)]) for i in range(4)]
    results = run(engine, tracker, frames, identities=walker_ids(Level.MEDIUM))
    started = next(r for r in results if r.alert_started)
    assert started.alert_level is Level.MEDIUM


def test_unknown_level_setting_applies_to_strangers() -> None:
    engine, tracker = armed(unknown_level=Level.MEDIUM), Tracker()
    frames = [(i * 0.1, [USER, walker(i * 0.1)]) for i in range(4)]
    started = next(r for r in run(engine, tracker, frames) if r.alert_started)
    assert started.alert_level is Level.MEDIUM


def test_level_raised_when_high_person_joins_medium_alert() -> None:
    engine, tracker = armed(), Tracker()
    second = lambda t: Box(1000 - 300 * t, 150, 1090 - 300 * t, 450)  # noqa: E731

    def ids(tracks):
        return {t.track_id: (Level.MEDIUM if t.track_id == 2 else Level.HIGH) for t in tracks}

    frames = [(i * 0.1, [USER, walker(i * 0.1)]) for i in range(4)]
    frames += [(0.4 + i * 0.1, [USER, walker(0.4 + i * 0.1), second(i * 0.1)]) for i in range(4)]
    results = run(engine, tracker, frames, identities=ids)
    assert next(r for r in results if r.alert_started).alert_level is Level.MEDIUM
    raised = [r for r in results if r.level_raised]
    assert len(raised) == 1 and raised[0].alert_level is Level.HIGH


def test_awaiting_identity_delays_alert_only_briefly() -> None:
    from desk_sentry.sentry import IDENTIFY_GRACE_SECONDS

    engine, tracker = armed(), Tracker()
    frames = [(i * 0.1, [USER, walker(i * 0.1)]) for i in range(12)]
    results = run(
        engine,
        tracker,
        frames,
        awaiting=lambda tracks, now: {t.track_id for t in tracks if t.box.height < 400},
    )
    first = next(i for i, r in enumerate(results) if r.alert_started)
    assert first * 0.1 >= IDENTIFY_GRACE_SECONDS - 1e-6
    assert first * 0.1 <= IDENTIFY_GRACE_SECONDS + 0.15


# ------------------------------------------------------------------ engine
class FakeFaces:
    """Always the same face, except the first ``fail_first`` lookups find none."""

    def __init__(self, vector: np.ndarray, fail_first: int = 0) -> None:
        self.vector = vector
        self.fail_first = fail_first
        self.calls = 0

    def embed(self, frame, person: Box):
        self.calls += 1
        if self.calls <= self.fail_first:
            return None
        return Face(self.vector, Box(person.x1, person.y1, person.x1 + 30, person.y1 + 30), 0.9)


def make_engine(faces, people_book=None, enter_after=2.5):
    FakeCamera.instances.clear()
    rec = Recorder()
    engine = Engine(
        ScriptedDetector(enter_after=enter_after),
        Settings(exclude_self=False),
        rec,
        camera_factory=lambda index: FakeCamera(index),
        faces=faces,  # type: ignore[arg-type]
    )
    if people_book is not None:
        engine.set_people(people_book.snapshot())
    return engine, rec


def test_engine_names_a_known_low_person_and_does_not_alert(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now[0])
    b = book()
    b.label("阿明", Level.LOW, face(1))
    engine, rec = make_engine(FakeFaces(face(1, noise=0.1)), b)
    state = _LoopState()
    try:
        for step in range(32):
            now[0] = step * 0.125
            engine._step(state)
    finally:
        engine.stop()
    assert any(t.name == "阿明" for snapshot in rec.items for t in snapshot.tracks)
    assert not any(s.alert_started for s in rec.items)


def test_engine_alert_carries_face_for_labelling() -> None:
    engine, rec = make_engine(FakeFaces(face(3)))
    engine.start()
    try:
        snap: Snapshot = rec.wait_for(lambda s: s.alert_thumbnail is not None, timeout=6)
    finally:
        engine.stop()
    assert snap.alert_face is not None and snap.alert_shot_id > 0


def test_thumbnail_is_upgraded_once_the_face_shows(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now[0])
    faces = FakeFaces(face(4), fail_first=1)  # no face at the alert moment, then visible
    engine, rec = make_engine(faces)
    state = _LoopState()
    try:
        for step in range(40):
            now[0] = step * 0.125
            engine._step(state)
    finally:
        engine.stop()
    first = next(snapshot for snapshot in rec.items if snapshot.alert_thumbnail is not None)
    later = next(
        snapshot
        for snapshot in rec.items
        if snapshot.alert_thumbnail is not None and snapshot.alert_face is not None
    )
    assert first.alert_face is None
    assert later.alert_shot_id == first.alert_shot_id  # replaces the same history item
    history = AlertHistory()
    history.add(first.alert_thumbnail, shot_id=first.alert_shot_id)
    history.add(later.alert_thumbnail, shot_id=later.alert_shot_id, face=later.alert_face)
    assert len(history) == 1 and history.items()[0].face is not None


def test_no_face_work_when_nobody_is_labelled_and_no_alert() -> None:
    faces = FakeFaces(face(1))
    engine, rec = make_engine(faces, enter_after=99)
    engine.start()
    try:
        time.sleep(1.0)
    finally:
        engine.stop()
    assert faces.calls == 0


# ---------------------------------------------------------------------- UI
def test_label_dialog_picks_known_level(qtbot: QtBot) -> None:
    levels = {"阿明": Level.LOW}
    dialog = LabelDialog(None, ["阿明"], None, level_of=levels.get)
    qtbot.addWidget(dialog)
    assert not dialog.save_button.isEnabled()
    dialog.name.setCurrentText("阿明")
    assert dialog.result_level is Level.LOW and dialog.save_button.isEnabled()
    dialog.name.setCurrentText("新朋友")
    assert dialog.result_name == "新朋友"


def test_people_editor_edits_and_two_step_clear(qtbot: QtBot) -> None:
    b = book()
    b.label("阿明", Level.LOW, face(1))
    b.label("小華", Level.HIGH, face(2))
    changes: list[bool] = []
    editor = PeopleEditor(b, lambda: changes.append(True))
    qtbot.addWidget(editor)
    editor.clear_button.click()
    assert len(b.people) == 2  # first click only asks for confirmation
    editor.clear_button.click()
    assert b.people == [] and changes


def test_history_only_faces_are_labelable(qtbot: QtBot) -> None:
    history = AlertHistory()
    history.add(np.zeros((100, 60, 3), np.uint8), shot_id=1)
    history.add(np.zeros((100, 60, 3), np.uint8), shot_id=2, face=face(1))
    window = HistoryWindow(history)
    qtbot.addWidget(window)
    clicked: list[int] = []
    window.on_label = lambda item: clicked.append(item.shot_id)
    window.refresh()
    from desk_sentry.ui.history import _Thumb

    thumbs = window.findChildren(_Thumb)
    assert len(thumbs) == 2
    for thumb in thumbs:
        qtbot.mouseClick(thumb, __import__("PySide6.QtCore").QtCore.Qt.MouseButton.LeftButton)
    assert clicked == [2]


def test_face_models_load_lazily_only_when_needed() -> None:
    loads: list[bool] = []

    def loader():
        loads.append(True)
        return FakeFaces(face(1))

    FakeCamera.instances.clear()
    rec = Recorder()
    engine = Engine(
        ScriptedDetector(enter_after=99),
        Settings(exclude_self=False),
        rec,
        camera_factory=lambda index: FakeCamera(index),
        faces_loader=loader,  # type: ignore[arg-type]
    )
    engine.start()
    try:
        time.sleep(1.0)
    finally:
        engine.stop()
    assert loads == []  # nobody labelled, no alert: models never loaded
