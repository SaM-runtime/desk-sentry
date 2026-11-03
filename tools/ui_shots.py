"""Render every UI surface with a synthetic scene to PNGs for design review.

No camera and no real image: the "office" is a drawn gradient with silhouettes.
Usage: python tools/ui_shots.py <out_dir>
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.sentry import Role, Status
from desk_sentry.stats import AlertStats
from desk_sentry.ui import theme
from desk_sentry.ui.curtain import CurtainGroup
from desk_sentry.ui.discreet import DiscreetCue
from desk_sentry.ui.history import AlertHistory, HistoryWindow
from desk_sentry.ui.main_window import MainWindow
from desk_sentry.ui.mini_mirror import MiniMirror
from desk_sentry.ui.overlays import MirrorPopup

W, H = 960, 540


def _load_ui_fonts() -> None:
    """Load installed UI fonts into this offscreen process only."""

    for path in (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/msjh.ttc")):
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


def _scene() -> np.ndarray:
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(0, 1, W)[None, :]
    base = np.stack(
        [70 + 40 * y + 10 * x, 76 + 34 * y + 6 * x, 84 + 24 * y + 0 * x], axis=-1
    ) * np.ones((H, W, 1))
    img = base.astype(np.float32)
    img[int(H * 0.62) :, :] *= 0.7  # floor
    for cx, top, w, shade in ((0.73, 0.22, 0.08, 0.55), (0.12, 0.30, 0.06, 0.6)):
        x0, x1 = int((cx - w / 2) * W), int((cx + w / 2) * W)
        img[int(top * H) :, x0:x1] *= shade
        hx = int(cx * W)
        rr = int(w * W * 0.45)
        yy, xx = np.ogrid[:H, :W]
        head = (yy - int((top - 0.06) * H)) ** 2 + (xx - hx) ** 2 <= rr**2
        img[head] *= shade
    # the user, big in the foreground
    yy, xx = np.ogrid[:H, :W]
    body = ((xx - W * 0.47) / (W * 0.2)) ** 2 + ((yy - H * 1.05) / (H * 0.55)) ** 2 <= 1
    img[body] = img[body] * 0.35 + np.array([40, 44, 52]) * 0.65
    head = ((xx - W * 0.47) / (W * 0.075)) ** 2 + ((yy - H * 0.42) / (H * 0.16)) ** 2 <= 1
    img[head] = img[head] * 0.3 + np.array([150, 120, 100]) * 0.7
    return np.clip(img, 0, 255).astype(np.uint8)


def _snap(status: Status, tracks: tuple[TrackView, ...], started: bool = False) -> Snapshot:
    return Snapshot(
        status=status,
        frame=_scene(),
        tracks=tracks,
        self_zone=(0.25, 0.22, 0.72, 1.0),
        intruders=sum(t.role in (Role.INTRUDER, Role.APPROACHING) for t in tracks),
        people=sum(t.role is not Role.SELF for t in tracks),
        camera_fps=7.2,
        latency_ms=24.0,
        alert_started=started,
    )


SELF = TrackView(1, (0.30, 0.25, 0.64, 1.0), Role.SELF, 0.9)
SCENES = {
    "clear": _snap(Status.CLEAR, (SELF,)),
    "watching": _snap(
        Status.WATCHING, (SELF, TrackView(3, (0.085, 0.2, 0.155, 1.0), Role.PASSING, 0.6))
    ),
    "alert": _snap(
        Status.ALERT,
        (
            SELF,
            TrackView(2, (0.685, 0.11, 0.775, 1.0), Role.INTRUDER, 0.8),
            TrackView(3, (0.085, 0.2, 0.155, 1.0), Role.PENDING, 0.5),
        ),
        started=True,
    ),
    "approaching": _snap(
        Status.APPROACHING,
        (SELF, TrackView(2, (0.67, 0.08, 0.79, 1.0), Role.APPROACHING, 0.85, "小王")),
        started=True,
    ),
    "collision": _snap(
        Status.ALERT,
        (TrackView(4, (0.82, 0.55, 0.92, 1.0), Role.INTRUDER, 0.9),),
        started=True,
    ),
}


def _save(widget: QWidget, path: Path) -> None:
    QApplication.processEvents()
    QTest.qWait(250)
    shot = (
        widget.grab()
        .toImage()
        .scaled(
            widget.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    shot.setDevicePixelRatio(1.0)
    if not shot.save(str(path)):
        raise RuntimeError(f"could not save {path}")


def _show_setting(settings: Settings, row_name: str, path: Path) -> None:
    window = MainWindow(settings, lambda _settings: None)
    window.resize(1180, 800)
    window.move(-4000, 0)
    window.show()
    settings_scroll = window.findChild(QScrollArea, "settingsScroll")
    if settings_scroll is None or settings_scroll.widget() is None:
        raise RuntimeError("settings scroll area not found")
    row = getattr(window, row_name)
    target_y = row.mapTo(settings_scroll.widget(), QPoint()).y()
    settings_scroll.verticalScrollBar().setValue(max(0, target_y - 220))
    _save(window, path)
    window.close()


def _discreet_shots(out: Path) -> None:
    _show_setting(Settings(discreet=True), "discreet_cue_row", out / "discreet-settings.png")
    window = MainWindow(Settings(discreet=True), lambda _settings: None)
    window.resize(1180, 800)
    window.move(-4000, 0)
    window.show()
    for status in ("watching", "alert"):
        window.show_snapshot(SCENES[status])
        _save(window, out / f"discreet-main-{status}.png")
    window.allow_close = True
    window.close()

    cue = DiscreetCue(cue="dot", corner="bottom-right")
    cue.update_status(Status.ALERT)
    QApplication.processEvents()
    if cue.window is None:
        raise RuntimeError("discreet dot was not created")
    dot = cue.window.grab()
    crop = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
    crop.fill(QColor(theme.STAGE))
    painter = QPainter(crop)
    painter.drawPixmap(16, 16, dot)
    painter.end()
    detail = crop.scaled(
        384, 384, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation
    )
    if not detail.save(str(out / "discreet-dot-detail.png")):
        raise RuntimeError("could not save discreet dot detail")
    cue.close()

    history = AlertHistory()
    for offset in range(4):
        history.add(_scene()[40:500, 150 + offset * 40 : 420 + offset * 40].copy())
    records = HistoryWindow(history, discreet=True)
    records.refresh()
    records.move(-4000, 0)
    records.show()
    _save(records, out / "discreet-history.png")
    records.close()


def _history_thirty_shot(out: Path) -> None:
    history = AlertHistory()
    for offset in range(30):
        history.add(_scene()[40:200, 150 + offset : 390 + offset].copy())
    records = HistoryWindow(history, discreet=True)
    records.open_near()
    _save(records, out / "history-30.png")
    records.close()


def _task07_shots(out: Path) -> None:
    window = MainWindow(
        Settings(
            discreet=True,
            cover_app="notepad.exe",
            action="minimize_app",
            target_apps=("chrome.exe",),
            restore_after_alert=True,
        ),
        lambda _settings: None,
        list_apps=lambda: [("notepad.exe", "工作筆記"), ("chrome.exe", "Browser")],
    )
    window.resize(1180, 800)
    window.move(-4000, 0)
    window.show()
    settings_scroll = window.findChild(QScrollArea, "settingsScroll")
    if settings_scroll is None or settings_scroll.widget() is None:
        raise RuntimeError("settings scroll area not found")
    target_y = window.cover_app_row.mapTo(settings_scroll.widget(), QPoint()).y()
    settings_scroll.verticalScrollBar().setValue(max(0, target_y - 70))
    _save(window, out / "evade-settings.png")
    window.set_pause_reason("schedule")
    window.show_snapshot(Snapshot(Status.PAUSED))
    _save(window, out / "main-auto-paused.png")
    window.allow_close = True
    window.close()

    cue = DiscreetCue(cue="dot", corner="bottom-right")
    cue.update_status(Status.NOTICE)
    QApplication.processEvents()
    if cue.window is None:
        raise RuntimeError("notice dot was not created")
    crop = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
    crop.fill(QColor(theme.STAGE))
    painter = QPainter(crop)
    painter.drawPixmap(16, 16, cue.window.grab())
    painter.end()
    if not crop.scaled(
        384, 384, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation
    ).save(str(out / "notice-dot-detail.png")):
        raise RuntimeError("could not save notice dot detail")
    cue.close()


def main(
    out: Path,
    *,
    discreet_only: bool = False,
    history_30_only: bool = False,
    task07_only: bool = False,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    _load_ui_fonts()
    app.setStyleSheet(theme.STYLESHEET)
    if task07_only:
        _task07_shots(out)
        print(f"wrote 3 task07 shots to {out}")
        return
    if history_30_only:
        _history_thirty_shot(out)
        print(f"wrote history-30.png to {out}")
        return
    if discreet_only:
        _discreet_shots(out)
        print(f"wrote 5 discreet shots to {out}")
        return

    window = MainWindow(Settings(armed=True), lambda s: None)
    window.resize(1180, 640)
    window.move(-4000, 0)
    window.show()
    for name in ("clear", "alert", "approaching", "collision"):
        window.show_snapshot(SCENES[name])
        _save(window, out / f"main-{name}.png")
    window.resize(820, 540)
    window.show_snapshot(SCENES["alert"])
    _save(window, out / "main-narrow.png")
    window.resize(700, 480)
    window.open_drawer()
    _save(window, out / "main-drawer.png")
    window.close_drawer()
    window.resize(1180, 640)
    window.set_armed(False)
    paused = Snapshot(Status.PAUSED)
    window.show_snapshot(paused)
    _save(window, out / "main-paused.png")
    window.close()

    target_apps = [
        ("chrome.exe", "Inbox"),
        ("line.exe", "LINE"),
        ("outlook.exe", "Outlook"),
        ("teams.exe", "Teams"),
        ("code.exe", "Visual Studio Code"),
        ("notepad.exe", "Notes"),
        ("calc.exe", "Calculator"),
        ("paint.exe", "Paint"),
    ]
    for count in (0, 3, 8):
        targets = tuple(exe for exe, _title in target_apps[:count])
        settings_window = MainWindow(
            Settings(armed=True, action="minimize_app", target_apps=targets),
            lambda _settings: None,
            list_apps=lambda: target_apps,
        )
        settings_window.resize(1180, 800)
        settings_window.move(-4000, 0)
        settings_window.show()
        settings_scroll = settings_window.findChild(QScrollArea, "settingsScroll")
        if settings_scroll is None:
            raise RuntimeError("settings scroll area not found")
        settings_content = settings_scroll.widget()
        if settings_content is None:
            raise RuntimeError("settings content not found")
        target_y = settings_window.target_app_row.mapTo(settings_content, QPoint()).y()
        settings_scroll.verticalScrollBar().setValue(max(0, target_y - 300))
        _save(settings_window, out / f"settings-targets-{count}.png")
        settings_window.close()

    _show_setting(
        Settings(
            away_action="minimize_app",
            away_minutes=8,
            target_apps=("chrome.exe", "line.exe"),
        ),
        "away_minutes_row",
        out / "away-settings.png",
    )
    _show_setting(
        Settings(
            schedule_enabled=True,
            schedule_start="08:30",
            schedule_end="18:30",
            schedule_days=(0, 1, 2, 3, 4),
        ),
        "schedule_fields_row",
        out / "schedule-settings.png",
    )

    curtain = CurtainGroup()
    curtain.show("Ctrl+Alt+H")
    if not curtain.windows:
        raise RuntimeError("curtain did not create a screen surface")
    _save(curtain.windows[0], out / "curtain.png")
    curtain.hide()

    mini = MiniMirror()
    mini.resize(384, 216)
    mini.move(-4000, 0)
    mini.show()
    for name in ("clear", "watching", "alert"):
        mini.set_snapshot(SCENES[name])
        _save(mini, out / f"mini-{name}.png")
    mini.close()

    popup = MirrorPopup()
    popup.update_snapshot(SCENES["alert"])
    QTest.qWait(200)
    popup.move(-4000, 0)
    _save(popup, out / "popup-alert.png")
    popup.close()

    history = AlertHistory()
    for i in range(4):
        history.add(
            _scene()[40:500, 150 + i * 40 : 420 + i * 40].copy(), name="小王" if i == 1 else None
        )
    hist = HistoryWindow(history)
    hist.refresh()
    hist.move(-4000, 0)
    hist.show()
    _save(hist, out / "history.png")
    history.clear()
    hist.refresh()
    _save(hist, out / "history-empty.png")
    hist.close()

    fixed_now = datetime(2026, 10, 12, 15, 30)
    stats_path = out.parent / "05-ui-stats.json"
    stats_path.unlink(missing_ok=True)
    stats = AlertStats(stats_path, clock=lambda: fixed_now)
    for _ in range(4):
        stats.record(alert_started=True, now=fixed_now.replace(day=11, hour=10))
    for hour, count in ((9, 1), (14, 3), (15, 2)):
        for _ in range(count):
            stats.record(alert_started=True, now=fixed_now.replace(hour=hour))
    summary = HistoryWindow(AlertHistory(), stats=stats)
    summary.refresh()
    summary.move(-4000, 0)
    summary.show()
    _save(summary, out / "history-summary.png")
    summary.close()

    empty_path = out.parent / "05-ui-stats-empty.json"
    empty_path.unlink(missing_ok=True)
    empty = HistoryWindow(
        AlertHistory(),
        stats=AlertStats(empty_path, clock=lambda: fixed_now),
    )
    empty.refresh()
    empty.move(-4000, 0)
    empty.show()
    _save(empty, out / "history-empty.png")
    empty.close()
    print(f"wrote {len(list(out.glob('*.png')))} shots to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", type=Path, default=Path("ui-shots"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--discreet-only", action="store_true")
    selection.add_argument("--history-30-only", action="store_true")
    selection.add_argument("--task07-only", action="store_true")
    args = parser.parse_args()
    main(
        args.out,
        discreet_only=args.discreet_only,
        history_30_only=args.history_30_only,
        task07_only=args.task07_only,
    )
