"""Summarize Desk Sentry diagnostics logs (numbers only) for tuning.

Usage: python tools/analyze_diagnostics.py [folder-or-file ...]
Default folder: %APPDATA%/DeskSentry/diagnostics
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import zlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

Record = dict[str, Any]


def load(paths: list[Path]) -> list[Record]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += sorted([*p.glob("*.jsonl"), *p.glob("*.jsonl.gz")], key=lambda f: f.name)
        else:
            files.append(p)
    records: list[Record] = []
    for f in files:
        text = _read_gzip(f) if f.suffix == ".gz" else f.read_text(encoding="utf-8")
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return sorted(records, key=lambda r: r["ts"])


def _read_gzip(path: Path) -> str:
    """Concatenated gzip members; a member cut off by a killed process is skipped."""

    data, out = path.read_bytes(), []
    while data:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            out.append(decoder.decompress(data))
        except zlib.error:
            break
        if not decoder.eof:
            break
        data = decoder.unused_data
    return b"".join(out).decode("utf-8", errors="ignore")


def when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def describe_tracks(rec: Record) -> str:
    parts = []
    for t in rec.get("tracks", []):
        x1, y1, x2, y2 = t["box"]
        parts.append(
            f"#{t['id']} {t['role']:<11} score={t['score']:.2f} h={y2 - y1:.2f} "
            f"x={x1:.2f}-{x2:.2f} motion={t['motion']:.2f} disp={t['disp']:.2f} "
            f"growth={t['growth']:.2f} age={t['age']:.1f}s"
        )
    return "\n      ".join(parts) or "(no tracks)"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def marker_statistics(records: list[Record]) -> dict[str, Record]:
    alerts = {
        record["alert_ts"] if record.get("alert_ts") is not None else record["ts"]: record
        for record in records
        if record["kind"] == "analysis" and record.get("alert_started")
    }
    result = {}
    for kind in ("false_alarm", "real"):
        markers = [
            record
            for record in records
            if record["kind"] == "marker" and record.get("marker") == kind
        ]
        linked = [
            alerts[record["alert_ts"]] for record in markers if record.get("alert_ts") in alerts
        ]
        tracks = [track for record in linked for track in record.get("tracks", [])]
        heights = [float(track["box"][3] - track["box"][1]) for track in tracks]
        result[kind] = {
            "count": len(markers),
            "linked": len(linked),
            "roles": dict(Counter(track["role"] for track in tracks)),
            "height_median": percentile(heights, 0.5),
            "height_p10": percentile(heights, 0.1),
            "height_p90": percentile(heights, 0.9),
        }
    return result


def main(argv: list[str]) -> int:
    default = Path(os.environ.get("APPDATA", ".")) / "DeskSentry" / "diagnostics"
    records = load([Path(a) for a in argv if not a.startswith("--")] or [default])
    if "--markers" in argv:
        print(json.dumps(marker_statistics(records), ensure_ascii=False))
        return 0
    if not records:
        print("no diagnostics records found")
        return 1
    analyses = [r for r in records if r["kind"] == "analysis"]
    span_h = (records[-1]["ts"] - records[0]["ts"]) / 3600
    print(
        f"records: {len(records)}  span: {when(records[0]['ts'])} → {when(records[-1]['ts'])}"
        f" ({span_h:.2f} h)"
    )
    print("kinds:", dict(Counter(r["kind"] for r in records)))
    print("feedback:", json.dumps(marker_statistics(records), ensure_ascii=False))

    if analyses:
        lat = [r["latency_ms"] for r in analyses]
        fps = [r["camera_fps"] for r in analyses if r["camera_fps"]]
        print(
            f"inference: median {statistics.median(lat):.0f} ms, max {max(lat):.0f} ms;"
            f" camera fps median {statistics.median(fps) if fps else 0:.1f}"
        )
        print("status time share (sampled):", dict(Counter(r["status"] for r in analyses)))
        roles = Counter(t["role"] for r in analyses for t in r.get("tracks", []))
        print("role observations:", dict(roles))
        zones = {tuple(r["self_zone"]) for r in analyses if r.get("self_zone")}
        print(f"distinct self zones: {len(zones)}")

    alerts = [r for r in analyses if r.get("alert_started")]
    per_hour = len(alerts) / span_h if span_h > 0 else 0
    print(f"\nalerts started: {len(alerts)} ({per_hour:.1f}/h)")
    for r in alerts[:40]:
        print(f"  {when(r['ts'])} {r['status']}\n      {describe_tracks(r)}")

    for kind in ("marker", "popup_dismissed", "error", "camera_lost", "self_calibrated"):
        items = [r for r in records if r["kind"] == kind]
        if not items:
            continue
        print(f"\n{kind}: {len(items)}")
        for r in items[:20]:
            extra = r.get("marker") or r.get("error") or r.get("self_zone") or ""
            print(f"  {when(r['ts'])} {extra}")
            if kind == "error":
                print("   ", r.get("trace", "").strip().splitlines()[-1])
            if kind in ("marker", "popup_dismissed"):
                window = [a for a in analyses if r["ts"] - 15 <= a["ts"] <= r["ts"] + 1]
                seen = [a for a in window if len(a.get("tracks", [])) > 0]
                for a in seen[-6:]:
                    print(f"    {when(a['ts'])} {a['status']}: {describe_tracks(a)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
