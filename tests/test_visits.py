"""離座紀錄: encrypted storage, expiry, the engine photo step and the window."""

import os
import time

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from desk_sentry import engine as engine_module
from desk_sentry.away_guard import VisitRequest
from desk_sentry.detector import Box
from desk_sentry.ui.visits import VisitWindow, visit_caption, visits_summary
from desk_sentry.visits import MAX_VISITS, RETENTION_DAYS, Visit, VisitLog


def _protect(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)[::-1]


def _unprotect(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data[::-1])


def _image(value: int = 120) -> np.ndarray:
    image = np.zeros((90, 120, 3), dtype=np.uint8)
    image[:, :, 0] = value
    return image


def _log(directory, clock=time.time) -> VisitLog:
    return VisitLog(directory, protect=_protect, unprotect=_unprotect, clock=clock)


def test_photos_survive_a_restart_and_are_not_stored_in_plain_text(tmp_path) -> None:
    log = _log(tmp_path)
    log.add(1, _image(), seated=True, taken_at=time.time())
    files = list(tmp_path.glob("*.dsv"))
    assert len(files) == 1
    payload = files[0].read_bytes()
    assert b"\xff\xd8" not in payload  # no readable JPEG marker
    assert b"taken_at" not in payload

    restarted = _log(tmp_path)
    restarted.load()
    [visit] = restarted.items()
    assert visit.seated and visit.image.shape == (90, 120, 3)


def test_a_closer_photo_of_the_same_visit_replaces_the_first(tmp_path) -> None:
    log = _log(tmp_path)
    first = time.time()
    log.add(5, _image(10), seated=False, taken_at=first)
    log.add(5, _image(200), seated=True, taken_at=first + 3)
    [visit] = log.items()
    assert visit.taken_at == first  # when the visit started
    assert visit.seated
    assert len(list(tmp_path.glob("*.dsv"))) == 1


def test_expired_unreadable_and_excess_photos_are_dropped(tmp_path) -> None:
    now = time.time()
    log = _log(tmp_path, clock=lambda: now)
    old = now - (RETENTION_DAYS + 1) * 86400
    log.add(1, _image(), seated=False, taken_at=old)
    old_file = next(tmp_path.glob("*.dsv"))
    os.utime(old_file, (old, old))
    (tmp_path / "broken.dsv").write_bytes(b"DSV1 not encrypted by us")

    restarted = _log(tmp_path, clock=lambda: now)
    restarted.load()
    assert len(restarted) == 0
    assert not old_file.exists()
    assert (tmp_path / "broken.dsv").exists()  # skipped, never fatal

    for index in range(MAX_VISITS + 3):
        restarted.add(100 + index, _image(), seated=False, taken_at=now + index)
    assert len(restarted) == MAX_VISITS
    assert restarted.items()[0].taken_at == now + MAX_VISITS + 2


def test_unseen_counts_only_photos_after_the_last_look(tmp_path) -> None:
    log = _log(tmp_path)
    log.add(1, _image(), seated=False, taken_at=100.0)
    log.add(2, _image(), seated=False, taken_at=200.0)
    assert log.unseen(0.0) == 2
    assert log.unseen(150.0) == 1


def test_without_dpapi_the_log_stays_in_memory(tmp_path) -> None:
    log = VisitLog(tmp_path, protect=None, unprotect=None)
    log.add(1, _image(), seated=False, taken_at=time.time())
    assert not log.persistent and len(log) == 1
    assert list(tmp_path.iterdir()) == []


def test_clear_removes_every_file(tmp_path) -> None:
    log = _log(tmp_path)
    log.add(1, _image(), seated=False, taken_at=time.time())
    changes = []
    log.on_change = lambda: changes.append(len(log))
    log.clear()
    assert len(log) == 0 and changes == [0]
    assert list(tmp_path.glob("*.dsv")) == []


def test_engine_takes_one_mirrored_photo_per_snapshot() -> None:
    state = engine_module._LoopState()
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:, :320] = (255, 0, 0)  # left half blue in BGR
    box = Box(0, 0, 640, 360)
    state.visit_requests = engine_module._latest_requests(
        [VisitRequest(1, box, False), VisitRequest(2, box, False), VisitRequest(1, box, True)]
    )
    assert [(r.visit_id, r.seated) for r in state.visit_requests] == [(2, False), (1, True)]

    shot = engine_module._take_visit_photo(frame, state)
    assert shot is not None and shot.visit_id == 2
    width = shot.image.shape[1]
    assert width <= engine_module.VISIT_PHOTO_MAX_SIZE[0]
    # Mirrored like the preview, and converted to RGB: blue ends up on the right.
    assert tuple(shot.image[5, width - 5]) == (0, 0, 255)
    assert len(state.visit_requests) == 1


def test_visit_captions_are_plain_language() -> None:
    now = time.mktime((2026, 11, 27, 18, 0, 0, 0, 0, -1))
    today = Visit("a", now - 3600, True, _image())
    earlier = Visit("b", now - 3 * 86400, False, _image())
    assert visit_caption(today, now=now) == "今天 17:00 · 坐到你的座位"
    assert visit_caption(earlier, now=now).endswith("· 靠近你的電腦")
    assert visits_summary(0) == "你不在座位時沒有人靠近"
    assert visits_summary(3) == "你不在座位時，有 3 次有人靠近"


def test_visit_window_shows_photos_and_clears_after_confirmation(
    qtbot: QtBot, tmp_path, monkeypatch
) -> None:
    log = _log(tmp_path)
    window = VisitWindow(log)
    qtbot.addWidget(window)
    assert not window.empty.isHidden() and not window.clear_button.isEnabled()

    log.add(1, _image(), seated=False, taken_at=time.time())
    log.add(2, _image(), seated=True, taken_at=time.time())
    window.refresh()
    assert len(window.cards) == 2
    assert window.summary.text() == "你不在座位時，有 2 次有人靠近"
    assert "加密" in window.note.text()

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No
    )
    window._confirm_clear()
    assert len(log) == 2  # declined: nothing deleted

    cleared = []
    window.on_cleared = lambda: cleared.append(True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    window._confirm_clear()
    assert len(log) == 0 and cleared == [True]
    assert window.cards == [] and not window.empty.isHidden()


@pytest.mark.parametrize("persistent", [True, False])
def test_visit_window_says_where_photos_live(qtbot: QtBot, tmp_path, persistent: bool) -> None:
    log = _log(tmp_path) if persistent else VisitLog(None, protect=None, unprotect=None)
    window = VisitWindow(log)
    qtbot.addWidget(window)
    assert ("加密" in window.note.text()) is persistent
