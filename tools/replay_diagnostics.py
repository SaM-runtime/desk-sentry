"""Replay logged detections through the current tracker + alert rules.

Lets alert-rule changes be measured on real recordings (numbers only, no video).
Usage: python tools/replay_diagnostics.py [folder-or-file ...] [--verbose]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
import zlib
from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from itertools import product
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_diagnostics import Record, load, percentile  # noqa: E402

from desk_sentry import actions, sentry  # noqa: E402
from desk_sentry.actions import EVADE_NEAR_HEIGHT_MULTIPLIER, EvadePlan  # noqa: E402
from desk_sentry.config import SENSITIVITY  # noqa: E402
from desk_sentry.detector import Box, Detection  # noqa: E402
from desk_sentry.people import Level  # noqa: E402
from desk_sentry.sentry import (  # noqa: E402
    AlertEngine,
    Policy,
    Role,  # noqa: E402
)
from desk_sentry.tracker import Tracker  # noqa: E402


def _tier_rows(records, *, engine_factory=AlertEngine, policy_factory=Policy):
    tracker, engine = Tracker(), engine_factory()
    last_mono = None
    for record in records:
        if record["kind"] in ("armed", "settings", "diagnostics_on"):
            zone = engine.policy.self_zone
            source = policy_from(record)
            engine.policy = policy_factory(
                sensitivity=source.sensitivity,
                exclude_self=source.exclude_self,
                self_zone=source.self_zone or zone,
                stationary_mute_seconds=source.stationary_mute_seconds,
                alert_far_passersby=source.alert_far_passersby,
                unknown_level=Level(record.get("unknown_level", "high")),
            )
            if record["kind"] == "armed":
                tracker.reset()
                engine.reset()
            continue
        if record["kind"] != "analysis":
            continue
        width, height = record["frame"]
        mono = record["mono"]
        if last_mono is not None:
            if mono < last_mono:
                tracker.reset()
                engine.reset()
            elif mono - last_mono > 5.0:
                tracker.reset()
        last_mono = mono
        if record.get("self_zone") and engine.policy.self_zone is None:
            engine.policy.self_zone = tuple(record["self_zone"])
        logged = [
            (
                track,
                Box(
                    track["box"][0] * width,
                    track["box"][1] * height,
                    track["box"][2] * width,
                    track["box"][3] * height,
                ),
            )
            for track in record["tracks"]
        ]

        def motion(box, logged=logged):
            matched = max(logged, key=lambda item: item[1].iou(box), default=None)
            return matched[0].get("motion", 0.0) if matched and matched[1].iou(box) > 0.3 else 0.0

        raw = [d[:5] for d in record["dets"]]
        if record.get("dets_v") == 2:
            raw += [
                [*track["box"], track["score"]] for track in record["tracks"] if track.get("seen")
            ]
        detections = [
            Detection(Box(d[0] * width, d[1] * height, d[2] * width, d[3] * height), d[4])
            for d in raw
        ]
        tracks = tracker.update(detections, mono)
        identities = {}
        for track in tracks:
            matched = max(logged, key=lambda item: item[1].iou(track.box), default=None)
            if (
                matched
                and matched[1].iou(track.box) > 0.3
                and matched[0].get("known") in ("low", "medium", "high")
            ):
                identities[track.track_id] = Level(matched[0]["known"])
        result = engine.assess(tracks, mono, (width, height), motion=motion, identities=identities)
        yield record, tracks, result, engine.policy


def replay_tiers(
    records: list[Record],
    baseline_alert_starts: list[float] | None = None,
    *,
    engine_factory=AlertEngine,
) -> Record:
    """Count tier/action decisions without touching windows; do not guess a missing baseline."""
    starts, leads = [], []
    first_notice = {}
    noticed = False
    notice_count = action_count = confirmed_count = 0
    observed_seconds = 0.0
    previous_mono = None
    previous_away = False
    pending_start = None
    pending_confirmed = False
    pending_full = False
    full_confirmed_count = 0
    plan = EvadePlan()
    for record, tracks, result, policy in _tier_rows(records, engine_factory=engine_factory):
        mono, status = record["mono"], result.status.value
        if previous_mono is not None:
            gap = mono - previous_mono
            if gap < 0:
                plan = EvadePlan()
                first_notice.clear()
                noticed = False
                pending_start = None
                pending_confirmed = pending_full = False
            if 0 <= gap <= 5.0 and not previous_away and status != "away":
                observed_seconds += gap
        previous_mono, previous_away = mono, status == "away"
        live = {track.track_id for track in tracks}
        first_notice = {key: value for key, value in first_notice.items() if key in live}
        if status == "notice":
            if not noticed:
                notice_count += 1
            for tid in result.notice_track_ids:
                first_notice.setdefault(tid, mono)
        noticed = status == "notice"
        if result.alert_started:
            starts.append(record["ts"])
            pending_start, pending_confirmed, pending_full = mono, False, False
            leading = [
                first_notice[tid]
                for tid, role in result.roles.items()
                if role in (Role.INTRUDER, Role.APPROACHING) and tid in first_notice
            ]
            if leading:
                leads.append(mono - min(leading))
            first_notice.clear()
        elif status not in ("alert", "approaching"):
            pending_start = None
        near = any(
            track.last_seen == mono
            and (
                result.roles.get(track.track_id) is Role.APPROACHING
                or (
                    result.roles.get(track.track_id) is Role.INTRUDER
                    and track.box.height / record["frame"][1]
                    >= SENSITIVITY[policy.sensitivity][2] * EVADE_NEAR_HEIGHT_MULTIPLIER
                )
            )
            for track in tracks
        )
        eligible = near and result.alert_level is Level.HIGH
        if (
            pending_start is not None
            and near
            and mono >= pending_start + actions.EVADE_DWELL_SECONDS
        ):
            if not pending_confirmed:
                confirmed_count += 1
                pending_confirmed = True
            if eligible and not pending_full:
                full_confirmed_count += 1
                pending_full = True
        if plan.update(status, mono, alert_started=result.alert_started, eligible=eligible):
            action_count += 1
    hours = observed_seconds / 3600.0
    filtered = len(starts) - confirmed_count
    result = {
        "baseline_available": int(baseline_alert_starts is not None),
        "baseline_alert_count": len(baseline_alert_starts)
        if baseline_alert_starts is not None
        else None,
        "alert_count": len(starts),
        "alert_start_symmetric_difference": len(set(starts) ^ set(baseline_alert_starts))
        if baseline_alert_starts is not None
        else None,
        "observed_non_away_hours": hours,
        "notice_count": notice_count,
        "notice_per_hour": notice_count / hours if hours else None,
        "lead_median_s": percentile(leads, 0.5),
        "lead_p10_s": percentile(leads, 0.1),
        "lead_p90_s": percentile(leads, 0.9),
        "alerts_without_lead": len(starts) - len(leads),
        "automatic_action_count": action_count,
        "automatic_actions_per_hour": action_count / hours if hours else None,
        "confirmation_filtered_alerts": filtered,
        "confirmation_filtered_ratio": filtered / len(starts) if starts else 0.0,
        "policy_level_filtered_alerts": confirmed_count - full_confirmed_count,
        "cooldown_filtered_alerts": full_confirmed_count - action_count,
        "alert_start_times": starts,
    }
    return result


class NoticeVariantEngine(AlertEngine):
    """Override only the post-ALERT notice tier; entry changes must not touch ALERT rules."""

    entry_window = sentry.ENTRY_WINDOW_SECONDS
    growth = sentry.NOTICE_APPROACH_GROWTH

    def _notice_tracks(self, tracks, roles, identities, now, width):
        score = SENSITIVITY[self.policy.sensitivity][0]
        zone = self.policy.self_zone
        seat_x = (zone[0] + zone[2]) / 2.0 if zone is not None else 0.5
        noticed = []
        for track in tracks:
            role = roles[track.track_id]
            if (
                role not in (Role.FAR, Role.PENDING, Role.PASSING)
                or track.hits < sentry.CONFIRM_HITS
                or track.mean_score < score
                or track.last_seen != now
                or identities.get(track.track_id) is Level.LOW
            ):
                continue
            # AlertEngine assigns PASSING only below near_height * frame_height.
            inward = False
            if track.history and now - track.first_seen <= self.entry_window:
                first_x = track.history[0][1].center[0] / width
                current_x = track.box.center[0] / width
                inward = (
                    (first_x < sentry.NOTICE_SIDE_EDGE or first_x > 1 - sentry.NOTICE_SIDE_EDGE)
                    and (current_x - first_x) * (seat_x - first_x) > 0
                    and abs(current_x - first_x) >= sentry.NOTICE_INWARD_DISPLACEMENT
                    and abs(current_x - seat_x) < abs(first_x - seat_x)
                )
            approaching = role is not Role.PASSING and (
                track.growth(1.0) >= self.growth
                and track.width_growth(1.0) >= sentry._APPROACH_WIDTH_GROWTH
                and track.growth(0.5) >= sentry._APPROACH_RECENT_GROWTH
                and track.displacement(1.0) <= sentry._APPROACH_MAX_SIDEWAYS
            )
            if inward or approaching:
                noticed.append(track.track_id)
        return tuple(noticed)


def replay_variants(records: list[Record]) -> list[Record]:
    """Eight bounded experiments; context-managed overrides restore even on failure."""
    baseline = replay_tiers(records)
    results = []
    # Numeric IDs: 0=N0, 1=N1, 2=N2, 3=N3, 4=A1, 5=A2, 6=A3, 7=A4.
    for variant in range(8):
        with ExitStack() as overrides:
            factory = NoticeVariantEngine if variant in (1, 2, 3) else AlertEngine
            if variant == 2:
                overrides.enter_context(patch.object(NoticeVariantEngine, "entry_window", 1.5))
            if variant == 3:
                overrides.enter_context(
                    patch.object(
                        NoticeVariantEngine, "growth", 1 + (sentry.NOTICE_APPROACH_GROWTH - 1) / 2
                    )
                )
            dwell = (
                0.6 if variant == 4 else 0.8 if variant in (5, 7) else actions.EVADE_DWELL_SECONDS
            )
            overrides.enter_context(patch.object(actions, "EVADE_DWELL_SECONDS", dwell))
            multiplier = 1.3 if variant in (6, 7) else EVADE_NEAR_HEIGHT_MULTIPLIER
            overrides.enter_context(
                patch.object(sys.modules[__name__], "EVADE_NEAR_HEIGHT_MULTIPLIER", multiplier)
            )
            result = replay_tiers(records, baseline["alert_start_times"], engine_factory=factory)
        result.pop("alert_start_times")
        result.update(
            variant=variant,
            alerts_with_lead=result["alert_count"] - result["alerts_without_lead"],
            denominator_non_away_analysis=1,
            denominator_max_gap_s=5.0,
            rate_quiet_s=1.0,
            rate_people_s=0.5,
            lead_sampling_is_coarse=1,
        )
        results.append(result)
    return results


def variants_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Numeric variants; lead estimates are sampled/coarse"
    )
    parser.add_argument("--variants", action="store_true")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    results = replay_variants(load(args.paths))
    for result in results:
        print(json.dumps(result, sort_keys=True))
    return 0 if all(result["alert_start_symmetric_difference"] == 0 for result in results) else 2


def _legacy_rules(path: Path):
    path = path.resolve()
    manifest = path.parents[2] / "manifest.json"
    expected = json.loads(manifest.read_text(encoding="utf-8-sig"))["src/desk_sentry/sentry.py"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("baseline source hash differs")
    spec = importlib.util.spec_from_file_location("_tier_legacy_sentry", path)
    if spec is None or spec.loader is None:
        raise ValueError("baseline is not a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tiers_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Numbers-only notice/action replay with exact legacy comparison"
    )
    parser.add_argument("--tiers", action="store_true")
    parser.add_argument(
        "--baseline-rules",
        type=Path,
        default=Path("baseline-rules.py"),
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    records = load(args.paths)
    baseline = None
    try:
        legacy = _legacy_rules(args.baseline_rules)
        baseline = [
            record["ts"]
            for record, _tracks, result, _policy in _tier_rows(
                records, engine_factory=legacy.AlertEngine, policy_factory=legacy.Policy
            )
            if result.alert_started
        ]
    except (OSError, ValueError, KeyError, ImportError):
        pass
    result = replay_tiers(records, baseline)
    result.pop("alert_start_times")
    print(json.dumps(result, sort_keys=True))
    return 0 if baseline is not None and result["alert_start_symmetric_difference"] == 0 else 2


def policy_from(record: Record) -> Policy:
    return Policy(
        sensitivity=record.get("sensitivity", "medium"),
        exclude_self=record.get("exclude_self", True),
        self_zone=tuple(record["self_zone"]) if record.get("self_zone") else None,
        stationary_mute_seconds=record.get("stationary_mute_seconds", 10.0),
        alert_far_passersby=record.get("alert_far_passersby", False),
    )


def replay(
    records: list[Record], verbose: bool = False, continued: list[float] | None = None
) -> list[tuple[float, str, str]]:
    continued = continued if continued is not None else []
    tracker = Tracker()
    engine = AlertEngine()
    alerts: list[tuple[float, str, str]] = []
    last_mono = None
    for rec in records:
        if rec["kind"] in ("armed", "settings", "diagnostics_on"):
            zone = engine.policy.self_zone
            engine.policy = policy_from(rec)
            if engine.policy.self_zone is None:
                engine.policy.self_zone = zone
            if rec["kind"] == "armed":
                tracker.reset()
                engine.reset()
            continue
        if rec["kind"] != "analysis":
            continue
        width, height = rec["frame"]
        mono = rec["mono"]
        if last_mono is not None and mono - last_mono > 5.0:
            tracker.reset()  # logging gap (paused / camera loss)
        last_mono = mono
        if rec.get("self_zone") and engine.policy.self_zone is None:
            engine.policy.self_zone = tuple(rec["self_zone"])
        logged = [
            (
                Box(
                    t["box"][0] * width,
                    t["box"][1] * height,
                    t["box"][2] * width,
                    t["box"][3] * height,
                ),
                t["motion"],
            )
            for t in rec["tracks"]
        ]

        def motion(box: Box, logged: list[tuple[Box, float]] = logged) -> float:
            best = max(logged, key=lambda item: item[0].iou(box), default=None)
            return best[1] if best is not None and best[0].iou(box) > 0.3 else 0.0

        raw = [d[:5] for d in rec["dets"]]
        if rec.get("dets_v") == 2:
            # v2 logs omit detections identical to a matched track's box.
            raw += [[*t["box"], t["score"]] for t in rec["tracks"] if t.get("seen")]
        dets = [
            Detection(Box(d[0] * width, d[1] * height, d[2] * width, d[3] * height), d[4])
            for d in raw
        ]
        tracks = tracker.update(dets, mono)
        result = engine.assess(tracks, mono, (width, height), motion=motion)
        if result.continued:
            continued.append(rec["ts"])
        if result.alert_started:
            roles = Counter(r.value for r in result.roles.values())
            alerts.append((rec["ts"], result.status.value, str(dict(roles))))
            if verbose:
                when = datetime.fromtimestamp(rec["ts"]).strftime("%m-%d %H:%M:%S")
                print(when, result.status.value, dict(roles))
                for t in tracks:
                    role = result.roles.get(t.track_id)
                    if role is not None and role.value in ("intruder", "approaching", "self"):
                        print(
                            f"    {role.value:<11} score={t.score:.2f} "
                            f"h={t.box.height / height:.2f} cx={t.box.center[0] / width:.2f} "
                            f"growth={t.growth(1.0):.2f} g05={t.growth(0.5):.2f} "
                            f"disp={t.displacement(1.0):.2f} age={mono - t.first_seen:.1f} "
                            f"hits={t.hits} motion={motion(t.box):.2f}"
                        )
    return alerts


def main(argv: list[str]) -> int:
    if "--limb-sweep" in argv:
        return limb_sweep_main(argv)
    if "--variants" in argv:
        return variants_main(argv)
    if "--tiers" in argv:
        return tiers_main(argv)
    verbose = "--verbose" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    import os

    default = Path(os.environ.get("APPDATA", ".")) / "DeskSentry" / "diagnostics"
    records = load(paths or [default])
    continued: list[float] = []
    alerts = replay(records, verbose, continued)
    hours = Counter(datetime.fromtimestamp(ts).strftime("%m-%d %H") for ts, _, _ in alerts)
    print(
        f"replayed {len(records)} records -> {len(alerts)} alerts "
        f"(+{len(continued)} merged continuations, no flash/sound)"
    )
    print("per hour:", dict(sorted(hours.items())))
    print("statuses:", dict(Counter(s for _, s, _ in alerts)))
    return 0


def _track_signature(tracks):
    return tuple(
        (
            t.track_id,
            t.box,
            t.score,
            t.first_seen,
            t.last_seen,
            t.hits,
            tuple(t.history),
            tuple(t.scores),
        )
        for t in tracks
    )


def _limb_event(record, tracks, result, baseline):
    width, height = record["frame"]
    users = [
        t
        for t in tracks
        if baseline.roles[t.track_id].value == "self" and t.last_seen == record["mono"]
    ]
    intruders = [
        t for t in tracks if baseline.roles[t.track_id].value in ("intruder", "approaching")
    ]

    def normalized(t):
        b = t.box.scaled(1 / width, 1 / height)
        return [b.x1, b.y1, b.x2, b.y2]

    def rough(t):
        return (
            t.score < 0.55
            and record["mono"] - t.first_seen < 1
            and t.box.y2 / height > 0.85
            and any(t.box.x1 <= u.box.x2 and t.box.x2 >= u.box.x1 for u in users)
        )

    return {
        "ts": record["ts"],
        "time": datetime.fromtimestamp(record["ts"]).isoformat(),
        "rough_hand": bool(intruders) and all(rough(t) for t in intruders),
        "self_boxes": [normalized(t) for t in users],
        "intruders": [
            {
                "id": t.track_id,
                "box": normalized(t),
                "score": t.score,
                "height": t.box.height / height,
                "age": record["mono"] - t.first_seen,
                "after_role": result.roles[t.track_id].value,
            }
            for t in intruders
        ],
    }


def _distribution(values):
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "p10": percentile(values, 0.1),
        "median": percentile(values, 0.5),
        "p90": percentile(values, 0.9),
        "max": max(values) if values else None,
    }


def sweep_limbs(records, baseline_module, *, parameters=None):
    """Shared unmodified tracker input; independent engines for every parameter tuple."""
    fixed = parameters is not None  # loops below reuse the name `parameters`
    combinations = (
        [tuple(parameters)]
        if parameters is not None
        else list(product((0.05, 0.08, 0.10, 0.15), (0.04, 0.08), (0.8, 0.9)))
    )
    variants = [AlertEngine() for _ in combinations]
    baseline_starts, variant_starts = {}, [{} for _ in combinations]
    blocked_scores, blocked_heights = [[] for _ in combinations], [[] for _ in combinations]
    analysis_count = 0
    parity_checks = 0

    class Engines:
        def __init__(self):
            self.base = baseline_module.AlertEngine()

        @property
        def policy(self):
            return self.base.policy

        @policy.setter
        def policy(self, value):
            self.base.policy = replace(value)
            for engine in variants:
                engine.policy = replace(value)

        def reset(self):
            self.base.reset()
            for engine in variants:
                engine.reset()

        def assess(self, tracks, now, frame, **kwargs):
            nonlocal parity_checks
            # _tier_rows can learn a logged seat zone via direct policy mutation.
            # Mirror it before evaluating variants, including logs starting mid-session.
            for engine in variants:
                if engine.policy.self_zone is None:
                    engine.policy.self_zone = self.base.policy.self_zone
            before = _track_signature(tracks)
            baseline = self.base.assess(tracks, now, frame, **kwargs)
            self.results = []
            defaults = (
                sentry.SELF_LIMB_TOP,
                sentry.SELF_LIMB_HORIZONTAL,
                sentry.SELF_LIMB_HEIGHT_RATIO,
            )
            try:
                for engine, parameters in zip(variants, combinations, strict=True):
                    (
                        sentry.SELF_LIMB_TOP,
                        sentry.SELF_LIMB_HORIZONTAL,
                        sentry.SELF_LIMB_HEIGHT_RATIO,
                    ) = parameters
                    result = engine.assess(tracks, now, frame, **kwargs)
                    assert _track_signature(tracks) == before, "D-03 tracker output mutated"
                    parity_checks += 1
                    self.results.append(result)
            finally:
                (
                    sentry.SELF_LIMB_TOP,
                    sentry.SELF_LIMB_HORIZONTAL,
                    sentry.SELF_LIMB_HEIGHT_RATIO,
                ) = defaults
            assert _track_signature(tracks) == before
            return baseline

    engines = Engines()
    for record, tracks, baseline, _policy in _tier_rows(records, engine_factory=lambda: engines):
        analysis_count += 1
        if baseline.alert_started:
            baseline_starts[record["ts"]] = _limb_event(record, tracks, baseline, baseline)
        for i, result in enumerate(engines.results):
            if result.alert_started:
                variant_starts[i][record["ts"]] = _limb_event(record, tracks, result, baseline)
            if baseline.alert_started:
                # Keep the candidate's roles at the exact old start, including delayed starts.
                baseline_starts[record["ts"]].setdefault("variant_roles", {})[str(i)] = {
                    str(t.track_id): result.roles[t.track_id].value for t in tracks
                }
            for track in tracks:
                if (
                    baseline.roles[track.track_id].value != "self"
                    and result.roles[track.track_id] is Role.SELF
                ):
                    blocked_scores[i].append(track.score)
                    blocked_heights[i].append(track.box.height / record["frame"][1])
    rows = []
    for i, parameters in enumerate(combinations):
        removed = [event for ts, event in baseline_starts.items() if ts not in variant_starts[i]]
        added = [event for ts, event in variant_starts[i].items() if ts not in baseline_starts]
        hands = sum(event["rough_hand"] for event in removed)
        rows.append(
            {
                "index": i,
                "top": parameters[0],
                "horizontal": parameters[1],
                "height_ratio": parameters[2],
                "before": len(baseline_starts),
                "after": len(variant_starts[i]),
                "removed_count": len(removed),
                "added_count": len(added),
                "rough_hand_count": hands,
                "rough_hand_ratio": hands / len(removed) if removed else None,
                "unlike_hand_count": len(removed) - hands,
                "blocked_frame_scores": _distribution(blocked_scores[i]),
                "blocked_frame_heights": _distribution(blocked_heights[i]),
                "removed_event_scores": _distribution(
                    [t["score"] for e in removed for t in e["intruders"]]
                ),
                "removed_event_heights": _distribution(
                    [t["height"] for e in removed for t in e["intruders"]]
                ),
                "unlike_hand_examples": [e for e in removed if not e["rough_hand"]][:10],
                "removed": removed,
                "added": added,
            }
        )
    selected = rows[0] if fixed else select_limb_parameters(rows)
    return {
        "analysis_frames": analysis_count,
        "tracker_parity_checks": parity_checks,
        "tracker_differences": 0,
        "rows": rows,
        "selected_index": selected["index"] if selected else None,
        "parameter_mode": "fixed" if fixed else "sweep",
    }


def select_limb_parameters(rows):
    """Apply the console's acceptance rule; zero removals supply no precision evidence."""
    eligible = [r for r in rows if r["removed_count"] and r["unlike_hand_count"] <= 3]
    return max(
        eligible,
        key=lambda r: (r["rough_hand_ratio"], -r["unlike_hand_count"], r["rough_hand_count"]),
        default=None,
    )


def limb_sweep_main(argv):
    parser = argparse.ArgumentParser(description="Task11 numbers-only limb parameter sweep")
    parser.add_argument("--limb-sweep", action="store_true")
    parser.add_argument("--baseline-rules", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--limb-parameters",
        nargs=3,
        type=float,
        metavar=("TOP", "HORIZONTAL", "HEIGHT_RATIO"),
        help="Replay only this already chosen combination; do not select new parameters",
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = []

    def records():
        for path in sorted(args.paths):
            payload = path.read_bytes()
            source = {
                "name": path.name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            # Retain only numeric replay input, never markers/identities/private payloads.
            compressed = path.suffix.lower() == ".gz" or payload.startswith(b"\x1f\x8b")
            data, chunks = (payload, []) if compressed else (b"", [payload])
            while data:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                try:
                    chunk = decoder.decompress(data)
                except zlib.error:
                    break
                if not decoder.eof:
                    break
                chunks.append(chunk)
                data = decoder.unused_data
            day = []
            fields = (
                "kind",
                "ts",
                "mono",
                "frame",
                "dets_v",
                "self_zone",
                "sensitivity",
                "exclude_self",
                "stationary_mute_seconds",
                "alert_far_passersby",
                "unknown_level",
            )
            for line in b"".join(chunks).decode("utf-8-sig", errors="ignore").splitlines():
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(raw, dict) or raw.get("kind") not in (
                    "analysis",
                    "armed",
                    "settings",
                    "diagnostics_on",
                ):
                    continue
                rec = {key: raw[key] for key in fields if key in raw}
                if raw["kind"] == "analysis":
                    rec["dets"] = [d[:5] for d in raw["dets"]]
                    rec["tracks"] = [
                        {
                            key: t[key]
                            for key in ("box", "score", "motion", "seen", "known")
                            if key in t
                        }
                        for t in raw["tracks"]
                    ]
                day.append(rec)
            day.sort(key=lambda r: r["ts"])
            snapshot_name = path.name if path.suffix.lower() == ".gz" else path.name + ".gz"
            snapshot = args.output_dir / snapshot_name
            snapshot.write_bytes(
                gzip.compress("\n".join(json.dumps(r) for r in day).encode("utf-8"), mtime=0)
            )
            source["records"] = len(day)
            sources.append(source)
            print(f"loading {path.name}: {len(day)} records", flush=True)
            yield from day

    try:
        result = sweep_limbs(
            records(), _legacy_rules(args.baseline_rules), parameters=args.limb_parameters
        )
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        print(f"error: invalid replay input: {error}", file=sys.stderr)
        return 2
    if not result["analysis_frames"]:
        print("error: no analysis frames; input is empty or malformed", file=sys.stderr)
        return 2
    result["sources"] = sources
    (args.output_dir / "sweep.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = [
        "Rough hand proxy: score<0.55, age<1s, bottom>0.85, horizontal overlap/touch (no margin).",
        "No image/ground truth: this proxy cannot establish a true/false positive.",
        "Removed/added are exact ALERT-start timestamp differences (may include timing shifts).",
        "Distributions count per-frame boxes separately from old-start boxes.",
    ]
    for row in result["rows"]:
        lines.append(
            json.dumps(
                {k: v for k, v in row.items() if k not in ("removed", "added")}, ensure_ascii=False
            )
        )
    lines.append(
        f"selected_index={result['selected_index']}; tracker_differences=0; "
        f"frames={result['analysis_frames']}"
    )
    (args.output_dir / "sweep.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[-1], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
