"""Application entry point: wires engine, windows, tray and alert responses."""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from desk_sentry import __version__, actions, config, meeting, schedule, startup
from desk_sentry.build_info import get_build_info
from desk_sentry.config import Settings
from desk_sentry.detector import Box, PersonDetector
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine, Snapshot
from desk_sentry.faces import FaceEmbedder
from desk_sentry.health import HealthState
from desk_sentry.hotkey import HIDE_HOTKEY_ID, GlobalHotkey, conflicts
from desk_sentry.hotkey import parse as parse_hotkey
from desk_sentry.lifecycle import EndSessionFilter, SingleInstanceMutex
from desk_sentry.people import Level, PeopleBook
from desk_sentry.sentry import Role, Status, Zone
from desk_sentry.stats import AlertStats
from desk_sentry.ui import theme
from desk_sentry.ui.curtain import CurtainGroup
from desk_sentry.ui.discreet import DiscreetCue
from desk_sentry.ui.history import AlertHistory, HistoryItem, HistoryWindow, thumbnail_image
from desk_sentry.ui.main_window import MainWindow
from desk_sentry.ui.mini_mirror import MiniMirror
from desk_sentry.ui.overlays import EdgeFlashGroup, MirrorPopup
from desk_sentry.ui.people import LabelDialog
from desk_sentry.update_check import (
    AvailableRelease,
    UpdateCheckResult,
    check_for_update,
    is_newer_version,
)

MODEL_NAME = "object_detection_nanodet_2022nov.onnx"
INSTANCE_NAME = "desk-sentry-v2-instance"
WM_TIMECHANGE = 0x001E
SCHEDULE_RESYNC_MS = 300_000
TRAY_MENU_STYLE = f"""
QMenu {{ background: {theme.SURFACE}; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; }}
QMenu::item {{ padding: {theme.D["space_8"]}px {theme.D["space_14"]}px; }}
QMenu::item:selected {{ background: {theme.RAISED}; }}
QMenu::item:disabled {{ color: {theme.TEXT_3}; }}
"""
_NEW_SETTINGS_OUTSIDE_DIAGNOSTICS = frozenset(
    {
        "away_action",
        "away_minutes",
        "camera_mode",
        "auto_yield_camera",
        "hide_hotkey_style",
        "pause_when_locked",
        "schedule_enabled",
        "schedule_start",
        "schedule_end",
        "schedule_days",
        "discreet",
        "discreet_cue",
        "discreet_corner",
        "discreet_screen_mode",
        "discreet_screens",
        "cover_app",
        "mark_false_hotkey",
        "mark_real_hotkey",
        "schema_version",
        "update_check_enabled",
        "update_last_checked",
        "update_ignored_version",
        "update_last_status",
        "update_latest_version",
        "update_release_url",
        "_extra_settings",
    }
)


def _diagnostics_settings(settings: Settings) -> dict[str, object]:
    """Keep the existing settings-log schema; only paused.reason is added by task 05."""
    return {k: v for k, v in asdict(settings).items() if k not in _NEW_SETTINGS_OUTSIDE_DIAGNOSTICS}


def model_path() -> Path:
    """Resolve models beside the source/app directory, independent of the working directory."""

    return Path(__file__).resolve().parents[2] / "models" / MODEL_NAME


class _Bridge(QObject):
    """Carries engine callbacks (background thread) onto the Qt thread."""

    snapshot = Signal(object)
    zone = Signal(object)
    meeting = Signal(bool)
    update_result = Signal(object)


class _MonitoringFilter(schedule._SessionFilter):
    """Use the session-notification entry point for wall-clock changes as well."""

    def __init__(
        self, hwnd: int, on_locked: Callable[[bool], None], on_time_change: Callable[[], None]
    ) -> None:
        super().__init__(hwnd, on_locked)
        self._on_time_change = on_time_change

    def nativeEventFilter(self, event_type: object, message: int) -> tuple[bool, int]:  # noqa: N802
        try:
            address = int(message)
        except (TypeError, ValueError):
            return False, 0
        if not address:
            return False, 0
        msg = schedule._Message.from_address(address)
        if int(msg.hwnd or 0) == self._hwnd and msg.message == WM_TIMECHANGE:
            self._on_time_change()
            return False, 0  # Also allow Qt to process its own clock-change notification.
        return super().nativeEventFilter(event_type, message)


class _MonitoringNotifications(schedule.SessionNotifications):
    """Extend the registered WTS filter locally, leaving schedule.py unchanged."""

    def __init__(
        self,
        on_locked: Callable[[bool], None],
        on_time_change: Callable[[], None],
        backend: schedule.SessionBackend | None = None,
    ) -> None:
        super().__init__(on_locked, backend)
        self._on_time_change = on_time_change

    def install(self, app: QCoreApplication, hwnd: int) -> bool:
        if not super().install(app, hwnd):
            return False
        if isinstance(self._filter, _MonitoringFilter):
            return True
        original = self._filter
        assert original is not None
        event_filter = _MonitoringFilter(hwnd, self._on_locked, self._on_time_change)
        app.removeNativeEventFilter(original)
        try:
            app.installNativeEventFilter(event_filter)
        except Exception:
            app.installNativeEventFilter(original)
            raise
        self._filter = event_filter
        return True


class _ObservedEvadePlan(actions.EvadePlan):
    """Reconfirm interrupted pending alerts without resetting executed/cooldown state."""

    def __init__(self) -> None:
        super().__init__()
        self._interrupted_pending = False

    def update(
        self, raw_status: str, now: float, *, alert_started: bool = False, eligible: bool = False
    ) -> bool:
        if raw_status in (Status.NO_CAMERA, Status.ERROR):
            self._interrupted_pending |= self._pending_since is not None
            return False
        if raw_status in (Status.ALERT, Status.APPROACHING) and self._interrupted_pending:
            if not alert_started:
                self._pending_since = now
            self._interrupted_pending = False
        elif raw_status in (Status.CLEAR, Status.AWAY, Status.PAUSED):
            self._interrupted_pending = False
        # WATCHING may be the first restored frame (a new track awaiting its
        # second hit). Keep recovery eligibility for the ensuing continuation.
        return super().update(raw_status, now, alert_started=alert_started, eligible=eligible)

    def cancel(self) -> None:
        self._interrupted_pending = False
        super().cancel()


class DeskSentryApp:
    def __init__(self, app: QApplication, settings_path: Path, *, start_hidden: bool) -> None:
        self._app = app
        self._path = settings_path
        self._build_info = get_build_info()
        self._available_update: AvailableRelease | None = None
        self._update_busy = False
        self._updates_enabled = False
        self._quitting = False
        self._shutdown_started = False
        settings = config.load(settings_path)
        if settings.update_last_status in ("available", "ignored"):
            try:
                current = self._build_info.display_version.split("+", 1)[0]
                if not is_newer_version(settings.update_latest_version, current):
                    settings = replace(settings, update_last_status="latest")
                    config.save(settings, settings_path)
            except (ValueError, OSError) as error:
                logging.getLogger(__name__).info("Cannot reconcile cached update state: %s", error)
        self._wall_now = datetime.now
        self._locked = False
        self._meeting_active = False
        self._manual_yield = False
        self._camera_mode = settings.camera_mode
        self._camera_backend = "pending" if settings.camera_mode == "auto" else "exclusive"
        self._effective_armed = settings.armed
        self._pause_reason: str | None = None
        self._pause_ack_reason: str | None = None
        self._pause_request = 0
        self._away_plan = actions.AwayPlan()
        self.stats = AlertStats(settings_path.parent / "alert-stats.json", clock=self._wall_now)
        self._stats_day = self._wall_now().date()
        self._stats_hour = self._wall_now().hour
        self._stats_error_logged = False
        self._health_state = HealthState.OK
        self._last_alert_ts: float | None = None
        self._evade_plan = _ObservedEvadePlan()
        self._foreground_plan = actions.ForegroundPlan()
        self._manual_cover_previous: int | None = None
        self._cover_failures_logged: set[tuple[str, str]] = set()
        self._latest_snapshot = Snapshot(Status.PAUSED)
        self._discreet_active = settings.discreet
        self._mini_before_discreet = False
        self.discreet_cue = DiscreetCue(
            cue="none",
            on_fallback=lambda: self._log(
                "error", error="Discreet cue screens unavailable; using primary screen"
            ),
        )
        self._bridge = _Bridge()
        self.people = PeopleBook(settings_path.parent / "people.bin")
        self.people.load()
        self.window = MainWindow(
            settings,
            self._on_settings,
            screen_catalog=self.discreet_cue.catalog,
            autostart=(startup.is_enabled, startup.set_enabled),
            list_apps=lambda: [(w.exe, w.title) for w in actions.list_app_windows()],
            people=self.people,
            display_version=self._build_info.display_version,
        )
        self.window.on_check_updates = lambda: self.check_updates(manual=True)
        self.window.on_open_update = self._open_update
        self.window.on_skip_update = self._skip_update
        if (
            settings.update_last_status == "available"
            and settings.update_latest_version != settings.update_ignored_version
            and "+" not in self._build_info.display_version
        ):
            from desk_sentry.project import GITHUB_REPOSITORY

            if settings.update_release_url.startswith(
                f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/"
            ):
                self._available_update = AvailableRelease(
                    settings.update_latest_version, settings.update_release_url
                )
        self.window.on_people_changed = self._people_changed
        self.popup = MirrorPopup()
        self.mini = MiniMirror()
        self._curtain_mini_was_visible = False
        self.curtain = CurtainGroup(mirror=self.mini, on_hidden=self._curtain_hidden)
        self.mini.on_expand = self.exit_compact
        self.mini.on_toggle_armed = self.window.toggle_armed
        self.mini.on_close = self._mini_to_tray
        self.mini.on_geometry = lambda g: self.window.update_settings(compact_geometry=g)
        self.window.on_compact = self.enter_compact
        self.window.on_history = lambda: self.open_history(self.window)
        self.mini.on_history = lambda: self.open_history(self.mini)
        # Alert thumbnails: memory only, dropped on exit.
        self.history = AlertHistory()
        self.history.on_change = self._history_changed
        self._history_window: HistoryWindow | None = None  # created on first open
        self._escalated = False
        self.hotkey = GlobalHotkey(self.toggle_mini)
        self.hotkey.install(app)
        self.hotkey.set(settings.mirror_hotkey)
        self.window.on_hotkey = self.hotkey.set
        self._hide_toggle = actions.HideToggle()
        self._restore_plan = actions.RestorePlan()
        self.hide_hotkey = GlobalHotkey(self.toggle_hidden_windows, registration_id=HIDE_HOTKEY_ID)
        self.hide_hotkey.install(app)
        if not self._set_hide_hotkey(settings.hide_hotkey):
            self.window.hide_hotkey_hint.setText("快捷鍵已被其他程式使用")
        self.window.on_hide_hotkey = self._set_hide_hotkey
        self.mark_false_hotkey = GlobalHotkey(
            lambda: self.mark("false_alarm"), registration_id=HIDE_HOTKEY_ID + 1
        )
        self.mark_real_hotkey = GlobalHotkey(
            lambda: self.mark("real"), registration_id=HIDE_HOTKEY_ID + 2
        )
        for kind, text in (
            ("false_alarm", settings.mark_false_hotkey),
            ("real", settings.mark_real_hotkey),
        ):
            if not self._set_mark_hotkey(kind, text):
                self.window.set_mark_hotkey_unavailable(kind)
        self.window.on_mark_hotkey = self._set_mark_hotkey
        self.flash = EdgeFlashGroup()
        self._apply_popup(settings)
        self._last_status: Status | None = None
        self._diagnostics = DiagnosticsLog(config.diagnostics_dir(settings_path))
        try:
            self.stats.prune()
        except Exception as error:
            self._log_stats_error(error)
        self.window.on_open_diagnostics = self.open_diagnostics
        self.popup.on_dismissed = lambda: self._log("popup_dismissed")

        self.tray = QSystemTrayIcon(theme.app_icon(), app)
        menu = QMenu()
        menu.setStyleSheet(TRAY_MENU_STYLE)
        menu.addAction("顯示設定視窗", self.exit_compact)
        self._compact_action = menu.addAction("小鏡子模式", self.enter_compact)
        menu.addAction("最近提醒", lambda: self.open_history(None))
        self._arm_action = menu.addAction("", self.window.toggle_armed)
        self._yield_camera_action = menu.addAction("", self.toggle_camera_yield)
        self._pause_info = menu.addAction("")
        self._pause_info.setEnabled(False)
        self._pause_info.setVisible(False)
        menu.addSeparator()
        menu.addAction("標記：剛才有人經過卻沒提醒", lambda: self.mark("missed_alert"))
        menu.addAction("標記：剛才沒人卻提醒了", lambda: self.mark("false_alert"))
        menu.addSeparator()
        self._update_action = menu.addAction("", self._open_update)
        self._skip_update_action = menu.addAction("略過此版本", self._skip_update)
        self._update_action.setVisible(False)
        self._skip_update_action.setVisible(False)
        self._quit_action = menu.addAction("結束 Desk Sentry", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()
        self._menu = menu
        self.window.on_preview_cue = self.preview_cue
        self._hint_shown = False
        self.window.on_hide_to_tray = self._hidden_to_tray
        self._render_tray(settings, Status.PAUSED)
        if self.people.load_error:
            self._notify(
                "Desk Sentry",
                "「認得的人」資料無法讀取（可能換了 Windows 帳號），目前從空白開始。",
                theme.app_icon(),
                5000,
            )

        self._bridge.snapshot.connect(self._on_snapshot)
        self._bridge.zone.connect(self._on_zone)
        self._bridge.meeting.connect(self._meeting_changed)
        models = model_path().parent
        self.engine = Engine(
            PersonDetector(model_path()),
            settings,
            on_snapshot=self._bridge.snapshot.emit,
            on_zone_calibrated=self._bridge.zone.emit,
            faces_loader=lambda: FaceEmbedder(models) if FaceEmbedder.available(models) else None,
        )
        self.engine.set_people(self.people.snapshot())
        self._schedule_timer = QTimer(app)
        self._schedule_timer.setSingleShot(True)
        self._schedule_timer.timeout.connect(self._apply_monitoring)
        self._schedule_resync_timer = QTimer(app)
        self._schedule_resync_timer.setInterval(SCHEDULE_RESYNC_MS)
        self._schedule_resync_timer.timeout.connect(self._apply_monitoring)
        self._schedule_resync_timer.start()
        self._session_notifications = _MonitoringNotifications(
            self._session_changed, self._apply_monitoring
        )
        self._session_notifications.install(app, int(self.window.winId()))
        self._apply_monitoring()
        self._apply_diagnostics(settings)
        self._sync_discreet_presentation(settings)
        self.engine.start()
        self._meeting_monitor = meeting.MeetingMonitor(self._bridge.meeting.emit)
        self._meeting_monitor.start()
        app.aboutToQuit.connect(self.shutdown_now)
        app.aboutToQuit.connect(self._meeting_monitor.stop)
        app.aboutToQuit.connect(self._session_notifications.close)
        app.aboutToQuit.connect(self._schedule_resync_timer.stop)
        app.aboutToQuit.connect(self.discreet_cue.close)
        self._bridge.update_result.connect(self._update_finished)
        self._update_timer = QTimer(self.window)
        self._update_timer.setInterval(24 * 60 * 60 * 1000)
        self._update_timer.timeout.connect(self.check_updates)
        self._configure_updates()
        self._refresh_update_prompt()
        if not start_hidden and not settings.discreet:
            self.show_current()

    # ------------------------------------------------------------ callbacks
    def _on_settings(self, settings: Settings) -> None:
        previous_camera_mode = getattr(self, "_camera_mode", settings.camera_mode)
        self._camera_mode = settings.camera_mode
        if settings.camera_mode != previous_camera_mode:
            self._camera_backend = "pending" if settings.camera_mode == "auto" else "exclusive"
        config.save(settings, self._path)
        self._apply_diagnostics(settings)
        self._log("settings", **_diagnostics_settings(settings))
        self._apply_monitoring()
        self._apply_popup(settings)
        self._sync_discreet_presentation(settings)
        if hasattr(self, "mark_false_hotkey"):
            self._set_mark_hotkey("false_alarm", settings.mark_false_hotkey)
            self._set_mark_hotkey("real", settings.mark_real_hotkey)
        self._render_tray(settings, self._last_status or Status.PAUSED)
        if hasattr(self, "_update_timer"):
            self._configure_updates()

    def _configure_updates(self) -> None:
        enabled = self.window.settings.update_check_enabled
        previous, self._updates_enabled = self._updates_enabled, enabled
        if not enabled:
            self._update_timer.stop()
            self._refresh_update_prompt()
        elif not previous:
            self._update_timer.start()
            self.check_updates()

    def check_updates(self, *, manual: bool = False) -> None:
        settings = self.window.settings
        if (
            (not manual and not settings.update_check_enabled)
            or self._update_busy
            or self._quitting
        ):
            return
        self._update_busy = True
        self.window.set_update_checking(True)
        version = self._build_info.display_version

        def work() -> None:
            result = check_for_update(
                version,
                self._updates_enabled,
                settings.update_last_checked,
                settings.update_ignored_version,
                manual=manual,
            )
            if not self._quitting:
                self._bridge.update_result.emit(result)

        threading.Thread(target=work, name="DeskSentry-update", daemon=True).start()

    def _update_finished(self, result: UpdateCheckResult) -> None:
        self._update_busy = False
        if self._quitting:
            return
        self.window.set_update_checking(False)
        if result.attempted:
            settings = self.window.settings
            latest = result.latest or result.available
            status = result.status
            self._available_update = result.available
            if result.available and result.available.version == settings.update_ignored_version:
                status, self._available_update = "ignored", None
            self.window.update_settings(
                update_last_checked=result.checked_at,
                update_last_status=status,
                update_latest_version=latest.version if latest else "",
                update_release_url=latest.url if latest else "",
            )
        self._refresh_update_prompt()

    def _refresh_update_prompt(self) -> None:
        release = self._available_update
        self._update_action.setVisible(release is not None)
        self._skip_update_action.setVisible(release is not None)
        self._render_tray(self.window.settings, self._last_status or Status.PAUSED)

    def _open_update(self) -> None:
        if self._available_update is not None:
            QDesktopServices.openUrl(QUrl(self._available_update.url))

    def _skip_update(self) -> None:
        if self._available_update is not None:
            version = self._available_update.version
            self._available_update = None
            self.window.update_settings(
                update_ignored_version=version, update_last_status="ignored"
            )
            self._refresh_update_prompt()

    def _sync_discreet_presentation(self, settings: Settings) -> None:
        if hasattr(self, "_compact_action"):
            self._compact_action.setVisible(not settings.discreet)
        previous = getattr(self, "_discreet_active", False)
        if not settings.discreet and not previous:
            return
        if settings.discreet:
            if not previous:
                self._mini_before_discreet = self.mini.isVisible()
                try:
                    actions.stop_alert_sound()
                except RuntimeError as error:
                    # A failing audio driver must not prevent hiding the camera surfaces.
                    logging.getLogger(__name__).warning(
                        "Could not stop pending alert tone: %s", error
                    )
            self.mini.hide()
            self.popup.hide()
            self.flash.hide()
            self.engine.preview_wanted = False
            self.discreet_cue.configure(
                settings.discreet_cue,
                settings.discreet_corner,
                settings.discreet_screen_mode,
                settings.discreet_screens,
            )
            self.discreet_cue.update_status(self._latest_snapshot.status)
        else:
            self.discreet_cue.hide()
            if self._mini_before_discreet:
                self.mini.restore(settings.compact_geometry)
                self.mini.armed = settings.armed
                self.mini.set_snapshot(self._latest_snapshot)
                self.mini.show()
                self.mini.raise_()
        self._discreet_active = settings.discreet
        self.curtain.set_discreet(settings.discreet)
        if self._history_window is not None:
            self._history_window.set_discreet(settings.discreet)

    def preview_cue(self, identify: bool = False) -> None:
        settings = self.window.settings
        self.discreet_cue.configure(
            settings.discreet_cue,
            settings.discreet_corner,
            settings.discreet_screen_mode,
            settings.discreet_screens,
        )
        self.discreet_cue.preview(3000 if identify else 1500, identify=identify)

    # ----------------------------------------------------------- diagnostics
    def _apply_diagnostics(self, settings: Settings) -> None:
        was_on = self.engine.diagnostics is not None
        self.engine.diagnostics = self._diagnostics if settings.diagnostics else None
        if settings.diagnostics and not was_on:
            self._diagnostics.write(
                "diagnostics_on", version=__version__, **_diagnostics_settings(settings)
            )

    def _log(self, kind: str, **fields: object) -> None:
        if self.window.settings.diagnostics:
            self._diagnostics.write(kind, **fields)

    def _notify(self, title: str, text: str, icon: QIcon, timeout_ms: int) -> None:
        if not self.window.settings.discreet:
            self.tray.showMessage(title, text, icon, timeout_ms)

    def mark(self, kind: str) -> None:
        if kind in ("false_alarm", "real"):
            if self.window.settings.diagnostics:
                alert_ts = getattr(self, "_last_alert_ts", None)
                now = getattr(self, "_wall_now", datetime.now)().timestamp()
                self._diagnostics.write(
                    "marker",
                    marker=kind,
                    status=str(self._last_status),
                    alert_ts=alert_ts,
                    age_s=max(0.0, now - alert_ts) if alert_ts is not None else None,
                )
                if self.window.settings.discreet:
                    self.discreet_cue.pulse_feedback()
            return
        if not self.window.settings.diagnostics:
            self._notify(
                "Desk Sentry",
                "請先在設定的「系統」開啟診斷記錄，標記才會被記下。",
                theme.app_icon(),
                4000,
            )
            return
        self._diagnostics.write("marker", marker=kind, status=str(self._last_status))
        self._notify("Desk Sentry", "已標記，感謝回報。", theme.app_icon(), 2000)

    def open_diagnostics(self) -> None:
        folder = self._diagnostics.folder
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opens Explorer on our own folder

    def _on_zone(self, zone: Zone) -> None:
        self._log("self_calibrated", self_zone=list(zone))
        self.window.set_self_zone(zone)
        config.save(self.window.settings, self._path)
        self._apply_monitoring()

    def _session_changed(self, locked: bool) -> None:
        self._locked = locked
        self._apply_monitoring()

    def _meeting_changed(self, active: bool) -> None:
        self._meeting_active = active
        self._apply_monitoring()

    def toggle_camera_yield(self) -> None:
        """Keep the camera released until the user explicitly restores it."""

        self._manual_yield = not getattr(self, "_manual_yield", False)
        self._apply_monitoring()

    def _apply_monitoring(self) -> None:
        settings = self.window.settings
        now = self._wall_now()
        in_schedule = not settings.schedule_enabled or schedule.in_window(
            now,
            settings.schedule_start,
            settings.schedule_end,
            settings.schedule_days,
        )
        previous_reason = getattr(self, "_pause_reason", None)
        camera_backend = getattr(self, "_camera_backend", "exclusive")
        if not settings.armed:
            reason = "manual"
        elif getattr(self, "_manual_yield", False):
            reason = "manual_yield"
        elif self._locked and settings.pause_when_locked:
            reason = "locked"
        elif not in_schedule:
            reason = "schedule"
        elif (
            getattr(self, "_meeting_active", False)
            and getattr(settings, "auto_yield_camera", True)
            and camera_backend == "exclusive"
        ):
            reason = "meeting"
        else:
            reason = None
        self._pause_reason = reason
        self._effective_armed = reason is None
        if reason != previous_reason:
            self._pause_ack_reason = None
        if not self._effective_armed:
            self._health_state = HealthState.OK
            evade_plan = getattr(self, "_evade_plan", None)
            if evade_plan is not None:
                evade_plan.cancel()
        pause_request = self.engine.apply(
            replace(settings, armed=self._effective_armed),
            pause_reason=self._pause_reason or "manual",
        )
        self._pause_request = pause_request if type(pause_request) is int else 0
        self._schedule_timer.stop()
        boundary: datetime | None = None
        if settings.schedule_enabled:
            boundary = schedule.next_boundary(
                now,
                settings.schedule_start,
                settings.schedule_end,
                settings.schedule_days,
            )
            if boundary is not None:
                self._schedule_timer.start(
                    max(1, math.ceil((boundary - now).total_seconds() * 1000))
                )
        self.window.set_pause_reason(
            self._pause_reason,
            now=now,
            next_start=boundary if self._pause_reason == "schedule" else None,
            acknowledged=(
                self._pause_reason not in ("meeting", "manual_yield")
                or getattr(self, "_pause_ack_reason", None) == self._pause_reason
            ),
        )
        if settings.discreet and not self._effective_armed:
            self.discreet_cue.update_status(Status.PAUSED)
        status = (self._last_status or Status.PAUSED) if self._effective_armed else Status.PAUSED
        self._render_tray(settings, status)

    def _record_stats(self, snap: Snapshot) -> None:
        stats = getattr(self, "stats", None)
        if stats is None:
            return
        now = self._wall_now()
        day_changed = self._stats_day != now.date()
        hour_changed = getattr(self, "_stats_hour", None) != now.hour
        if day_changed:
            stats.prune(now=now)
            self._stats_day = now.date()
        self._stats_hour = now.hour
        changed = stats.record(alert_started=snap.alert_started, continued=snap.continued, now=now)
        if (changed or day_changed or hour_changed) and self._history_window is not None:
            self._history_window.refresh()

    def _log_stats_error(self, error: Exception) -> None:
        if not getattr(self, "_stats_error_logged", False):
            self._stats_error_logged = True
            self._log("stats_error", error=str(error))

    def _on_snapshot(self, snap: Snapshot) -> None:
        settings = self.window.settings
        backend = getattr(snap, "camera_backend", "exclusive")
        if backend not in ("pending", "shared", "exclusive"):
            backend = "pending"
        backend_changed = backend != getattr(self, "_camera_backend", "exclusive")
        self._camera_backend = backend
        if backend_changed and getattr(self, "_meeting_active", False):
            self._apply_monitoring()
            settings = self.window.settings
        acknowledged_reason = getattr(snap, "pause_reason", None)
        acknowledged_request = getattr(snap, "pause_request", 0)
        if (
            snap.status is Status.PAUSED
            and acknowledged_reason is not None
            and acknowledged_reason == getattr(self, "_pause_reason", None)
            and acknowledged_request == getattr(self, "_pause_request", 0)
        ):
            self._pause_ack_reason = acknowledged_reason
            self.window.set_pause_reason(acknowledged_reason, acknowledged=True)
            self._render_tray(settings, Status.PAUSED)
        if snap.status is Status.NOTICE and not settings.discreet:
            snap = replace(snap, status=Status.WATCHING)
        effective = getattr(self, "_effective_armed", settings.armed)
        if snap.status is Status.PAUSED and effective:
            # A stale "paused" frame emitted just before re-arming.
            return
        if not effective and snap.status is not Status.PAUSED:
            return  # queued analysis from just before an automatic/manual pause
        now = time.monotonic()
        health_changed = snap.health_state is not getattr(self, "_health_state", HealthState.OK)
        self._health_state = snap.health_state
        if snap.alert_started:
            self._last_alert_ts = (
                snap.alert_ts
                if snap.alert_ts is not None
                else getattr(self, "_wall_now", datetime.now)().timestamp()
            )
        if not hasattr(self, "_evade_plan"):
            self._evade_plan = _ObservedEvadePlan()
        if not hasattr(self, "_foreground_plan"):
            self._foreground_plan = actions.ForegroundPlan()
        try:
            self._record_stats(snap)
        except Exception as error:
            # Optional counters must never prevent the privacy alert/action.
            self._log_stats_error(error)
        if settings.restore_after_alert and settings.action == "minimize_app":
            ready = self._restore_plan.update(snap.raw_status or snap.status, now)
            if ready:
                actions.restore_windows(ready)
        else:
            self._restore_plan.clear()
        if settings.restore_after_alert:
            foreground = self._foreground_plan.update(snap.raw_status or snap.status, now)
            if foreground is not None:
                actions.restore_foreground(foreground)
        else:
            self._foreground_plan.clear()
        self._latest_snapshot = snap
        away_plan = getattr(self, "_away_plan", None)
        if away_plan is not None:
            away_action = away_plan.update(
                snap.raw_status or snap.status,
                time.monotonic(),
                action=settings.away_action,
                minutes=settings.away_minutes,
                target_apps=settings.target_apps,
                last_input_age=actions.last_input_age,
            )
            if away_action is not None:
                actions.run_action(away_action, settings.target_apps)
        display = (
            replace(
                snap, frame=None, tracks=(), self_zone=None, alert_thumbnail=None, alert_face=None
            )
            if settings.discreet
            else snap
        )
        self.window.show_snapshot(display)
        if settings.discreet:
            self.mini.hide()
            self.popup.hide()
            self.discreet_cue.update_status(snap.status)
        mini_shown = not settings.discreet and self.mini.isVisible()
        if mini_shown:
            self.mini.armed = settings.armed
            self.mini.set_snapshot(snap)
        if not settings.discreet and settings.show_mirror and not mini_shown:
            self.popup.update_snapshot(snap)
        elif self.popup.isVisible():
            self.popup.hide()
        self.engine.preview_wanted = not settings.discreet and (
            mini_shown
            or self.popup.isVisible()
            or (self.window.isVisible() and not self.window.isMinimized())
        )

        if snap.alert_thumbnail is not None:
            self.history.add(
                snap.alert_thumbnail,
                shot_id=snap.alert_shot_id,
                face=snap.alert_face,
                name=snap.alert_name,
            )
        if snap.alert_started:
            self._escalated = False
        # Only people at the "high" alert level (or unlabelled people, by default)
        # get the flash / sound / action; "medium" only turns the mirror red.
        full = snap.alert_level is Level.HIGH
        # Escalation re-flashes once per alert chain, not on every flicker.
        upgraded = (
            full
            and snap.status is Status.APPROACHING
            and self._last_status is Status.ALERT
            and not self._escalated
        )
        if upgraded:
            self._escalated = True
        if (snap.alert_started and full) or snap.level_raised or upgraded:
            color = theme.STATUS_COLOR[snap.status]
            if settings.edge_flash and not settings.discreet:
                self.flash.flash(color)
            if settings.sound and not settings.discreet:
                actions.play_alert_sound()
        near_height = (
            config.SENSITIVITY[settings.sensitivity][2] * actions.EVADE_NEAR_HEIGHT_MULTIPLIER
        )
        near = snap.evade_near or any(
            track.seen
            and (
                track.role is Role.APPROACHING
                or (track.role is Role.INTRUDER and track.box[3] - track.box[1] >= near_height)
            )
            for track in snap.tracks
        )
        raw_status = snap.raw_status or snap.status
        should_execute = self._evade_plan.update(
            raw_status,
            now,
            alert_started=snap.alert_started,
            eligible=full and near and (settings.action != "none" or bool(settings.cover_app)),
        )
        if should_execute:
            self._execute_evade(settings)
        if snap.status is not self._last_status or health_changed:
            self._last_status = snap.status
            self._render_tray(settings, snap.status)

    # ------------------------------------------------------------------ tray
    def _render_tray(self, settings: Settings, status: Status) -> None:
        degraded = getattr(self, "_health_state", HealthState.OK) is HealthState.DEGRADED
        # Discreet mode keeps the recognizable app icon (a swapped icon confused
        # testers) but in its fixed colour, so the tray does not reveal alerts.
        icon = (
            theme.app_icon()
            if settings.discreet
            else theme.app_icon(theme.STATUS_COLOR[Status.PAUSED if degraded else status])
        )
        if getattr(self, "_available_update", None) is not None:
            pixmap = icon.pixmap(32, 32)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor(theme.STAGE))
            painter.setBrush(QColor(theme.TEXT_2 if settings.discreet else theme.ACCENT))
            if settings.discreet:
                painter.drawEllipse(24, 1, 6, 6)
            else:
                painter.drawEllipse(21, 1, 10, 10)
            painter.end()
            icon = QIcon(pixmap)
        self.tray.setIcon(icon)
        release = getattr(self, "_available_update", None)
        if hasattr(self, "_update_action"):
            self._update_action.setText(
                "有可用更新…"
                if settings.discreet
                else f"下載新版 {release.version}…"
                if release
                else ""
            )
            self._skip_update_action.setText("略過此更新" if settings.discreet else "略過此版本")
        if hasattr(self, "_quit_action"):
            self._quit_action.setText("結束 Desk Sentry")
        reason = getattr(self, "_pause_reason", None)
        reason_text = {
            "locked": "已暫停 · 電腦鎖定中",
            "schedule": "已暫停 · 非監看時段",
        }.get(reason or "")
        if reason in ("meeting", "manual_yield"):
            if getattr(self, "_pause_ack_reason", None) != reason:
                reason_text = "正在讓出攝影機"
            elif reason == "meeting":
                reason_text = "已暫停 · 會議中（已讓出攝影機）"
            else:
                reason_text = "已暫停 · 已讓出攝影機"
        tooltip = (
            # Name stays visible so non-technical users can find it; status stays hidden.
            ("Desk Sentry（低調模式・偵測中斷）" if degraded else "Desk Sentry（低調模式）")
            if settings.discreet
            else (
                f"Desk Sentry · {reason_text or theme.STATUS_TEXT[status][0]}"
                + ("（偵測中斷）" if degraded else "")
            )
        )
        self.tray.setToolTip(tooltip)
        if hasattr(self, "_pause_info"):
            self._pause_info.setText(reason_text or "")
            self._pause_info.setVisible(reason_text is not None)
        if hasattr(self, "_yield_camera_action"):
            yielding = getattr(self, "_manual_yield", False)
            self._yield_camera_action.setText("恢復使用攝影機" if yielding else "暫時讓出攝影機")
            self._yield_camera_action.setEnabled(settings.armed or yielding)
        self._arm_action.setText("暫停監看" if settings.armed else "開始監看")

    def _on_tray(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_current()

    # ----------------------------------------------------------- mini mirror
    def show_current(self) -> None:
        """Bring back whichever mode the user left the app in."""

        if self.window.settings.discreet:
            self.show_window()
        elif self.window.settings.compact_mode:
            self.enter_compact()
        else:
            self.show_window()

    def enter_compact(self) -> None:
        if self.window.settings.discreet:
            self.show_window()
            return
        self.window.hide()
        self.popup.hide()
        if not self.mini.isVisible():
            self.mini.restore(self.window.settings.compact_geometry)
            self.mini.show()
        self.mini.raise_()
        if not self.window.settings.compact_mode:
            self.window.update_settings(compact_mode=True)

    def exit_compact(self) -> None:
        self.mini.hide()
        if self.window.settings.discreet:
            self.show_window()
            return
        if self.window.settings.compact_mode:
            self.window.update_settings(compact_mode=False)
        self.show_window()

    def toggle_mini(self) -> None:
        """Global hotkey: hide the mini mirror if shown, otherwise bring it up."""

        if self.window.settings.discreet:
            self.mini.hide()
            self.popup.hide()
            if self.window.isVisible():
                self.window.hide()
            else:
                self.show_window()
            return
        if self.mini.isVisible():
            self.mini.hide()
        else:
            self.enter_compact()

    def _set_hide_hotkey(self, text: str) -> bool:
        if not text:
            self.hide_hotkey.disable()
            return True
        return self.hide_hotkey.set(text)

    def _set_mark_hotkey(self, kind: str, text: str) -> bool:
        settings = self.window.settings
        others = [
            settings.mirror_hotkey,
            settings.hide_hotkey,
            settings.mark_real_hotkey if kind == "false_alarm" else settings.mark_false_hotkey,
        ]
        if text and (parse_hotkey(text) is None or any(conflicts(text, other) for other in others)):
            return False
        key = self.mark_false_hotkey if kind == "false_alarm" else self.mark_real_hotkey
        if not text:
            key.disable()
            return True
        key.install(self._app)
        return key.set(text)

    def _activate_cover(self, settings: Settings) -> actions.CoverResult:
        if not settings.cover_app:
            return actions.CoverResult()
        result = actions.bring_cover(settings.cover_app)
        reason = (
            "cover_missing" if not result.found else "cover_denied" if not result.activated else ""
        )
        if reason:
            if not hasattr(self, "_cover_failures_logged"):
                self._cover_failures_logged = set()
            key = (settings.cover_app, reason)
            if key not in self._cover_failures_logged:
                self._cover_failures_logged.add(key)
                self._log(reason)
        return result

    def _execute_evade(self, settings: Settings) -> None:
        cover = self._activate_cover(settings)
        if settings.restore_after_alert:
            self._foreground_plan.record(cover.previous)
        if settings.action == "minimize_app":
            minimized = actions.minimize_apps(settings.target_apps)
            if settings.restore_after_alert:
                self._restore_plan.record(minimized)
        elif settings.action == "curtain":
            self._show_curtain()
        elif settings.action != "none":
            actions.run_action(settings.action)

    def _restore_manual_cover(self) -> None:
        previous = getattr(self, "_manual_cover_previous", None)
        self._manual_cover_previous = None
        if previous is not None:
            actions.restore_foreground(previous)

    def toggle_hidden_windows(self) -> None:
        curtain = getattr(self, "curtain", None)
        if curtain is not None and curtain.visible:
            curtain.hide()
            self._restore_manual_cover()
            return
        hide_toggle = getattr(self, "_hide_toggle", None)
        stored = hide_toggle.stored if hide_toggle is not None else {}
        if stored or getattr(self, "_manual_cover_previous", None) is not None:
            if stored and hide_toggle is not None:
                hide_toggle.toggle(self.window.settings.target_apps)
            self._restore_manual_cover()
            return
        cover = self._activate_cover(self.window.settings)
        self._manual_cover_previous = cover.previous
        if self.window.settings.hide_hotkey_style == "curtain":
            self._show_curtain()
            return
        self._hide_toggle.toggle(self.window.settings.target_apps)

    def _show_curtain(self) -> None:
        if self.curtain.visible:
            return
        self._curtain_mini_was_visible = self.mini.isVisible()
        if self.window.settings.discreet:
            self._curtain_mini_was_visible = False
            self.mini.hide()
            self.engine.preview_wanted = False
            self.curtain.set_discreet(True)
            self.curtain.show(self.window.settings.hide_hotkey)
            return
        if not self._curtain_mini_was_visible:
            self.mini.restore(self.window.settings.compact_geometry)
            self.mini.armed = self.window.settings.armed
            self.mini.set_snapshot(self._latest_snapshot)
            self.mini.show()
        self.engine.preview_wanted = True
        self.curtain.show(self.window.settings.hide_hotkey)

    def _curtain_hidden(self) -> None:
        if not self._curtain_mini_was_visible:
            self.mini.hide()
        self._restore_manual_cover()

    def open_history(self, anchor: object) -> None:
        if self._history_window is None:
            self._history_window = HistoryWindow(
                self.history, stats=self.stats, discreet=self.window.settings.discreet
            )
            self._history_window.on_label = self.label_person
        self._history_window.open_near(anchor if isinstance(anchor, QWidget) else None)

    def label_person(self, item: HistoryItem) -> None:
        if item.face is None:
            return
        dialog = LabelDialog(
            thumbnail_image(item.thumbnail),
            [p.name for p in self.people.people],
            item.name,
            level_of=lambda name: found.level if (found := self.people.find_name(name)) else None,
        )
        if not dialog.exec():
            return
        try:
            person = self.people.label(dialog.result_name, dialog.result_level, item.face)
        except ValueError:
            return
        item.name = person.name
        self._people_changed()
        self.window.refresh_people()

    def _people_changed(self) -> None:
        self.people.save()
        self.engine.set_people(self.people.snapshot())

    def _history_changed(self) -> None:
        self.mini.history_count = len(self.history)
        self.mini.view.set_unseen(self.history.unseen)
        self.window.set_unseen(self.history.unseen)

    def _mini_to_tray(self) -> None:
        self.mini.hide()
        self._hidden_to_tray()

    def _hidden_to_tray(self) -> None:
        if not self._hint_shown:
            self._hint_shown = True
            self._notify(
                "Desk Sentry 仍在背景監看",
                "有人從身後靠近時會彈出後照鏡。右鍵系統匣圖示可暫停或結束。",
                theme.app_icon(),
                4000,
            )

    def _apply_popup(self, settings: Settings) -> None:
        self.popup.corner = settings.mirror_corner
        self.popup.mirror_width = settings.mirror_width

    def show_window(self) -> None:
        self.window.show()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def shutdown_now(self) -> None:
        """Finish persistent state before Windows can terminate this process."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._quitting = True
        self.engine.stop()
        # Engine.stop already flushes its active logger. A disabled logger may
        # still hold app-side records, so it must also be flushed in that case.
        if self.engine.diagnostics is not self._diagnostics:
            self._diagnostics.flush()
        try:
            config.save(self.window.settings, self._path)
        except OSError as error:
            logging.getLogger(__name__).info("Cannot save settings at shutdown: %s", error)

    def quit(self) -> None:
        self._quitting = True
        if hasattr(self, "_update_timer"):
            self._update_timer.stop()
        self.discreet_cue.close()
        self._schedule_timer.stop()
        self._schedule_resync_timer.stop()
        self._session_notifications.close()
        self.curtain.hide()
        self.hotkey.close()
        self.hide_hotkey.close()
        self.mark_false_hotkey.close()
        self.mark_real_hotkey.close()
        self.history.clear()
        self.window.allow_close = True
        self.tray.hide()
        self._app.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="desk-sentry")
    parser.add_argument("--minimized", action="store_true", help="start in the system tray")
    parser.add_argument(
        "--quit", action="store_true", help="ask the running instance to exit cleanly"
    )
    parser.add_argument("--settings", type=Path, default=None, help="settings JSON path")
    parser.add_argument(
        "--smoke-test", action="store_true", help="load the model and exit without opening a camera"
    )
    args = parser.parse_args(argv)

    if args.quit:
        # No model, settings, mutex acquisition or new application instance for a quit request.
        core = QCoreApplication.instance() or QCoreApplication(["desk-sentry", "--quit"])
        _signal_running_instance(b"quit")
        del core
        return 0

    if args.smoke_test:
        import numpy as np

        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        PersonDetector(model_path()).detect(blank)
        models = model_path().parent
        if not FaceEmbedder.available(models):
            print("face models missing", file=sys.stderr)
            return 3
        FaceEmbedder(models).embed(blank, Box(100, 50, 400, 470))
        print("smoke test ok")
        return 0

    app = QApplication(sys.argv if argv is None else ["desk-sentry", *argv])
    app.setApplicationName("Desk Sentry")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(theme.app_icon())

    if _signal_running_instance():
        return 0
    mutex = SingleInstanceMutex()
    if not mutex.acquire():
        # The first process may still be loading its model before opening its IPC server.
        for _ in range(10):
            if _signal_running_instance():
                break
            time.sleep(0.1)
        return 0
    if not model_path().is_file():
        QMessageBox.critical(
            None,
            "Desk Sentry",
            f"找不到偵測模型：\n{model_path()}\n\n請重新解壓完整的 DeskSentry 資料夾，"
            "或在原始碼目錄執行 tools/fetch_model.py。",
        )
        mutex.close()
        return 2

    try:
        _configure_logging()
        instance = DeskSentryApp(
            app,
            args.settings or config.default_path(),
            start_hidden=args.minimized,
        )
        server = _listen_for_other_instances(instance)
        shutdown = EndSessionFilter(
            int(instance.window.winId()), instance.quit, shutdown_now=instance.shutdown_now
        )
        app.installNativeEventFilter(shutdown)
        try:
            return app.exec()
        finally:
            app.removeNativeEventFilter(shutdown)
            server.close()
    finally:
        mutex.close()


def _configure_logging() -> None:
    logger = logging.getLogger("desk_sentry")
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return
    folder = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "DeskSentry"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            folder / "app.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _signal_running_instance(command: bytes = b"show") -> bool:
    """Ask an already running Desk Sentry to show its window. True if one answered."""

    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_NAME)
    if not socket.waitForConnected(300):
        return False
    socket.write(command + b"\n")
    socket.waitForBytesWritten(300)
    # Keep the named pipe alive until the server consumes the command. In
    # particular, a short-lived --quit client must not discard its unread write.
    socket.waitForReadyRead(700)
    socket.disconnectFromServer()
    return True


def _listen_for_other_instances(instance: DeskSentryApp) -> QLocalServer:
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(INSTANCE_NAME)  # stale name after a crash
    if not server.listen(INSTANCE_NAME):
        raise RuntimeError("Cannot listen for another DeskSentry instance")
    connections: set[QLocalSocket] = set()

    def on_connection() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is None:
                continue
            connections.add(connection)
            connection.disconnected.connect(
                lambda connection=connection: connections.discard(connection)
            )
            connection.disconnected.connect(connection.deleteLater)

            def read_command(connection: QLocalSocket = connection) -> None:
                if connection.bytesAvailable() < 4:
                    return
                command = bytes(connection.readAll().data()).strip()
                connection.write(b"ok\n")
                connection.waitForBytesWritten(100)
                if command == b"quit":
                    instance.quit()
                elif command == b"show":
                    instance.show_current()
                connection.disconnectFromServer()

            connection.readyRead.connect(read_command)
            read_command()

    server.newConnection.connect(on_connection)
    return server


if __name__ == "__main__":
    raise SystemExit(main())
