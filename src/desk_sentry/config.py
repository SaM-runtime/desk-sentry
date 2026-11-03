"""User settings persisted as a small JSON file (no images, no biometrics)."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path
from typing import Any

from desk_sentry.hotkey import normalize as hotkey_normalize
from desk_sentry.hotkey import parse as parse_hotkey

SENSITIVITY = {
    # name: (min detector score, ignore people shorter than this,
    #        people at least this tall alert on any movement; smaller ones only
    #        when they come towards the camera). Heights are fractions of the
    #        frame; ~0.3 is roughly 5 m from a typical laptop webcam.
    "low": (0.50, 0.14, 0.40),
    "medium": (0.40, 0.09, 0.30),
    "high": (0.32, 0.05, 0.20),
}
ACTIONS = ("none", "minimize", "minimize_app", "lock", "curtain")
AWAY_ACTIONS = ("none", "lock", "minimize_app")
HIDE_HOTKEY_STYLES = ("minimize", "curtain")
DISCREET_CUES = ("dot", "line", "none")
DEFAULT_SCHEDULE_START = "08:30"
DEFAULT_SCHEDULE_END = "18:30"
DEFAULT_SCHEDULE_DAYS = (0, 1, 2, 3, 4)
CORNERS = ("top-right", "top-left", "bottom-right", "bottom-left")
MAX_TARGET_APPS = 8
MAX_EXE_LENGTH = 260
DEFAULT_HIDE_HOTKEY = "Ctrl+Alt+H"
CURRENT_SCHEMA_VERSION = 1


def normalize_schedule_time(value: object, default: str) -> str:
    if isinstance(value, str) and len(value) == 5 and value[2] == ":":
        try:
            return time.fromisoformat(value).strftime("%H:%M")
        except ValueError:
            pass
    return default


def normalize_schedule_days(value: object) -> tuple[int, ...]:
    if not isinstance(value, (tuple, list)):
        return DEFAULT_SCHEDULE_DAYS
    return tuple(sorted({d for d in value if type(d) is int and 0 <= d <= 6}))


def normalize_target_apps(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        exe = item.strip().lower()[:MAX_EXE_LENGTH]
        if exe and exe not in result:
            result.append(exe)
        if len(result) == MAX_TARGET_APPS:
            break
    return tuple(result)


@dataclass(slots=True)
class Settings:
    schema_version: int = CURRENT_SCHEMA_VERSION
    camera_index: int = 0
    armed: bool = True
    sensitivity: str = "medium"
    exclude_self: bool = True
    # Normalized (x1, y1, x2, y2) of the user's own seat; None = auto-calibrate.
    self_zone: tuple[float, float, float, float] | None = None
    # Mute a person who has stayed still this long (0 = never mute).
    stationary_mute_seconds: float = 10.0
    # Also alert for distant people walking past (not towards you).
    alert_far_passersby: bool = False
    # Alert level ("high" / "medium") for people nobody has labelled.
    unknown_level: str = "high"
    show_mirror: bool = True
    edge_flash: bool = True
    sound: bool = True
    discreet: bool = False
    discreet_cue: str = "dot"
    discreet_corner: str = "top-right"
    discreet_screen_mode: str = "all"
    discreet_screens: tuple[dict[str, str], ...] = ()
    action: str = "none"
    cover_app: str = ""
    mark_false_hotkey: str = ""
    mark_real_hotkey: str = ""
    # Executable names shared by alert minimization and the hide-window hotkey.
    target_apps: tuple[str, ...] = ()
    restore_after_alert: bool = False
    away_action: str = "none"
    away_minutes: int = 5
    mirror_corner: str = "top-right"
    mirror_width: int = 360
    # Numbers-only diagnostics log (boxes, scores, verdicts); never images.
    diagnostics: bool = False
    # Global hotkey that shows / hides the mini mirror.
    mirror_hotkey: str = "Ctrl+Alt+M"
    hide_hotkey: str = DEFAULT_HIDE_HOTKEY
    hide_hotkey_style: str = "minimize"
    pause_when_locked: bool = True
    schedule_enabled: bool = False
    schedule_start: str = DEFAULT_SCHEDULE_START
    schedule_end: str = DEFAULT_SCHEDULE_END
    schedule_days: tuple[int, ...] = DEFAULT_SCHEDULE_DAYS
    # Mini mirror mode: only a small always-on-top camera window is shown.
    compact_mode: bool = False
    compact_geometry: tuple[int, int, int, int] | None = None  # x, y, w, h
    update_check_enabled: bool = False
    update_last_checked: float = 0.0
    update_ignored_version: str = ""
    update_last_status: str = "never"
    update_latest_version: str = ""
    update_release_url: str = ""
    _extra_settings: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def normalized(self) -> Settings:
        s = Settings(**{f.name: getattr(self, f.name) for f in fields(self)})
        if type(s.schema_version) is not int or s.schema_version < 1:
            s.schema_version = CURRENT_SCHEMA_VERSION
        s.camera_index = min(max(int(s.camera_index), 0), 9)
        if s.sensitivity not in SENSITIVITY:
            s.sensitivity = "medium"
        if s.action not in ACTIONS:
            s.action = "none"
        s.discreet = s.discreet if isinstance(s.discreet, bool) else False
        if s.discreet_cue not in DISCREET_CUES:
            s.discreet_cue = "dot"
        if s.discreet_corner not in CORNERS:
            s.discreet_corner = "top-right"
        if s.discreet_screen_mode not in ("all", "primary", "selected"):
            s.discreet_screen_mode = "all"
        references: list[dict[str, str]] = []
        if isinstance(s.discreet_screens, (tuple, list)):
            for reference in s.discreet_screens:
                if isinstance(reference, dict):
                    clean = {
                        k: v
                        for k, v in reference.items()
                        if k in ("serial", "model", "manufacturer", "name", "geometry", "number")
                        and isinstance(v, str)
                    }
                    if clean and clean not in references:
                        references.append(clean)
        s.discreet_screens = tuple(references)
        if s.away_action not in AWAY_ACTIONS:
            s.away_action = "none"
        try:
            s.away_minutes = min(60, max(1, int(s.away_minutes)))
        except (ValueError, TypeError):
            s.away_minutes = 5
        if s.hide_hotkey_style not in HIDE_HOTKEY_STYLES:
            s.hide_hotkey_style = "minimize"
        s.pause_when_locked = bool(s.pause_when_locked)
        s.schedule_enabled = bool(s.schedule_enabled)
        s.schedule_start = normalize_schedule_time(s.schedule_start, DEFAULT_SCHEDULE_START)
        s.schedule_end = normalize_schedule_time(s.schedule_end, DEFAULT_SCHEDULE_END)
        s.schedule_days = normalize_schedule_days(s.schedule_days)
        if s.mirror_corner not in CORNERS:
            s.mirror_corner = "top-right"
        s.mirror_width = min(max(int(s.mirror_width), 240), 960)
        s.target_apps = normalize_target_apps(s.target_apps)
        cover = normalize_target_apps((s.cover_app,))
        s.cover_app = cover[0] if cover else ""
        if s.unknown_level not in ("high", "medium"):
            s.unknown_level = "high"
        s.mirror_hotkey = hotkey_normalize(str(s.mirror_hotkey or ""))
        hide_text = str(s.hide_hotkey or "").strip()
        hide_chord = parse_hotkey(hide_text)
        s.hide_hotkey = hide_chord.text if hide_chord else DEFAULT_HIDE_HOTKEY if hide_text else ""
        if s.hide_hotkey == s.mirror_hotkey:
            s.hide_hotkey = ""  # Reject a duplicate without changing the existing mirror shortcut.
        used_hotkeys = {s.mirror_hotkey, s.hide_hotkey}
        for field_name in ("mark_false_hotkey", "mark_real_hotkey"):
            parsed = parse_hotkey(str(getattr(s, field_name) or ""))
            chord = parsed.text if parsed is not None else ""
            if chord in used_hotkeys:
                chord = ""
            setattr(s, field_name, chord)
            if chord:
                used_hotkeys.add(chord)
        s.stationary_mute_seconds = min(max(float(s.stationary_mute_seconds), 0.0), 600.0)
        if s.compact_geometry is not None:
            try:
                x, y, w, h = (int(v) for v in s.compact_geometry)
                s.compact_geometry = (x, y, w, h) if w >= 120 and h >= 80 else None
            except (TypeError, ValueError):
                s.compact_geometry = None
        if s.self_zone is not None:
            try:
                x1, y1, x2, y2 = (min(max(float(v), 0.0), 1.0) for v in s.self_zone)
                s.self_zone = (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None
            except (TypeError, ValueError):
                s.self_zone = None
        s.update_check_enabled = (
            s.update_check_enabled if isinstance(s.update_check_enabled, bool) else False
        )
        if (
            isinstance(s.update_last_checked, bool)
            or not isinstance(s.update_last_checked, (int, float))
            or not math.isfinite(s.update_last_checked)
            or s.update_last_checked < 0
        ):
            s.update_last_checked = 0.0
        else:
            s.update_last_checked = float(s.update_last_checked)
        if not isinstance(s.update_ignored_version, str):
            s.update_ignored_version = ""
        if s.update_last_status not in (
            "never",
            "latest",
            "available",
            "ignored",
            "error",
            "development",
        ):
            s.update_last_status = "never"
        for name in ("update_latest_version", "update_release_url"):
            if not isinstance(getattr(s, name), str):
                setattr(s, name, "")
        return s


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "DeskSentry" / "settings-v2.json"


def diagnostics_dir(settings_path: Path) -> Path:
    return settings_path.parent / "diagnostics"


def load(path: Path) -> Settings:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    known = {f.name for f in fields(Settings) if not f.name.startswith("_")}
    values = {k: v for k, v in raw.items() if k in known}
    values["_extra_settings"] = {
        k: v for k, v in raw.items() if k not in known and k != "target_app"
    }
    targets = normalize_target_apps(values.get("target_apps", ()))
    if not targets and isinstance(raw.get("target_app"), str):
        targets = normalize_target_apps((raw["target_app"],))
    values["target_apps"] = targets
    for key in ("self_zone", "compact_geometry", "schedule_days"):
        if isinstance(values.get(key), list):
            values[key] = tuple(values[key])
    schema_version = raw.get("schema_version", CURRENT_SCHEMA_VERSION)
    if type(schema_version) is not int or schema_version < 1:
        schema_version = CURRENT_SCHEMA_VERSION
    values["schema_version"] = schema_version
    try:
        return Settings(**values).normalized()
    except (TypeError, ValueError):
        return Settings(
            schema_version=schema_version,
            _extra_settings=values["_extra_settings"],
        )


def save(settings: Settings, path: Path) -> None:
    # A newer app owns future schemas; a downgrade must leave its bytes untouched.
    if settings.schema_version > CURRENT_SCHEMA_VERSION:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = dict(settings._extra_settings)
    payload.update(
        {f.name: getattr(settings, f.name) for f in fields(settings) if not f.name.startswith("_")}
    )
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
