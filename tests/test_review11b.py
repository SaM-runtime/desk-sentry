"""Task11b: a fresh self handoff remains an arm reference without changing presence."""

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from desk_sentry.detector import Box
from desk_sentry.sentry import AlertEngine, Policy, Role
from desk_sentry.tracker import Track


@pytest.mark.parametrize("arm_first", [False, True])
@pytest.mark.parametrize("user_x,presence_time", [(630, 0.2), (700, 0)])
def test_self_handoff_outside_zone_excludes_arm_and_preserves_presence(
    arm_first, user_x, presence_time
):
    rules = AlertEngine(
        Policy(sensitivity="high", self_zone=(0.404, 0.27, 0.83, 1), stationary_mute_seconds=0)
    )
    rules.assess([], -3, (1000, 1000))
    old_user = Track(1, Box(450, 350, 900, 1000), 0.95, -2, 0, hits=20)
    rules.assess([old_user], 0, (1000, 1000))
    user = Track(2, Box(user_x, 350, 1000, 1000), 0.95, 0.2, 0.2, hits=2)
    arm = Track(3, Box(920, 700, 999, 990), 0.95, 0.1, 0.2, hits=2)
    tracks = [arm, user] if arm_first else [user, arm]
    result = rules.assess(tracks, 0.2, (1000, 1000))
    assert result.roles == {2: Role.SELF, 3: Role.SELF}
    assert not result.alert_started and result.intruders == 0
    assert rules._self_ids == {2}
    assert rules._last_self_seen == presence_time
    assert rules._last_self_box == (user.box if presence_time else old_user.box)


@pytest.fixture
def replay_cli(tmp_path):
    root = Path(__file__).resolve().parents[1]
    baseline = tmp_path / "baseline"
    rules = baseline / "src/desk_sentry/sentry.py"
    rules.parent.mkdir(parents=True)
    rules.write_bytes((root / "src/desk_sentry/sentry.py").read_bytes())
    (baseline / "manifest.json").write_text(
        json.dumps({"src/desk_sentry/sentry.py": hashlib.sha256(rules.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )

    def invoke(payload, name="input.jsonl", extra=()):
        source = tmp_path / name
        source.write_bytes(payload)
        output = tmp_path / "output"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "tools/replay_diagnostics.py"),
                "--limb-sweep",
                "--baseline-rules",
                str(rules),
                "--output-dir",
                str(output),
                *extra,
                str(source),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        return result, output

    return invoke


@pytest.mark.parametrize(
    "name,compressed",
    [
        ("input.jsonl", False),
        ("input.jsonl.gz", True),
        ("magic.jsonl", True),
    ],
)
def test_sweep_cli_reads_one_plain_or_gzip_analysis(replay_cli, name, compressed):
    record = {
        "kind": "analysis",
        "ts": 1000,
        "mono": 0,
        "frame": [1000, 1000],
        "dets": [],
        "tracks": [],
    }
    payload = (json.dumps(record) + "\n").encode()
    result, output = replay_cli(gzip.compress(payload) if compressed else payload, name)
    assert result.returncode == 0, result.stdout + result.stderr
    sweep = json.loads((output / "sweep.json").read_text(encoding="utf-8"))
    assert sweep["analysis_frames"] == 1
    # A full sweep must go through the acceptance gate, which rejects no evidence.
    assert sweep["parameter_mode"] == "sweep"
    assert sweep["selected_index"] is None


@pytest.mark.parametrize("payload", [b"", b"not-json\n", b"{}\n", b"[]\n"])
def test_sweep_cli_rejects_empty_or_invalid_input_clearly(replay_cli, payload):
    result, output = replay_cli(payload)
    assert result.returncode != 0
    assert "no analysis frames" in result.stderr.lower()
    assert not (output / "sweep.json").exists()


def test_fixed_parameters_replay_runs_only_the_adopted_combination(replay_cli):
    record = {
        "kind": "analysis",
        "ts": 1000,
        "mono": 0,
        "frame": [1000, 1000],
        "dets": [],
        "tracks": [],
    }
    result, output = replay_cli(
        json.dumps(record).encode(), extra=("--limb-parameters", ".10", ".08", ".9")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    replayed = json.loads((output / "sweep.json").read_text(encoding="utf-8"))
    assert replayed["analysis_frames"] == replayed["tracker_parity_checks"] == 1
    assert replayed["parameter_mode"] == "fixed"
    assert len(replayed["rows"]) == 1
    row = replayed["rows"][0]
    assert (row["top"], row["horizontal"], row["height_ratio"]) == (0.10, 0.08, 0.9)
    # The retained snapshot must be readable by the normal gzip-aware replay loader.
    snapshot = output / "input.jsonl.gz"
    assert json.loads(gzip.decompress(snapshot.read_bytes())) == record
