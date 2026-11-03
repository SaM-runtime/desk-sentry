"""Generic privacy policy and optional local pattern-file regressions."""

import importlib.util
import sys
from pathlib import Path


def test_detects_paths_network_and_internal_records():
    spec = importlib.util.spec_from_file_location(
        "privacy_scan", Path(__file__).parents[1] / "tools/privacy_scan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for text in (
        "C:" + "/Users/example/file",
        "192." + "168.1.5",
        "10." + "2.3.4",
        "." + "console/notes",
        "person" + "@example.org",
    ):
        assert module.findings("sample.txt", text)
    assert not module.findings("sample.txt", "version 10.0.17134.12")
    assert not module.findings(".gitignore", "." + "console/")
    upstream = "shiqi.yu" + "@gmail.com"
    assert not module.findings("licenses/YuNet-OpenCV-Zoo-MIT.txt", upstream)
    assert module.findings("README.md", upstream)


def test_worktree_scans_new_names_skips_deleted_and_history_is_opt_in_by_default(
    tmp_path, monkeypatch, capsys
):
    spec = importlib.util.spec_from_file_location(
        "privacy_scan", Path(__file__).parents[1] / "tools/privacy_scan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    rules = tmp_path / "patterns.txt"
    rules.write_text("# neutral fixtures\n\nAlice Example|ACME\n", encoding="utf-8")
    monkeypatch.setenv("PRIVACY_SCAN_EXTRA", str(rules))
    name = "Alice Example.txt"
    (tmp_path / name).write_text("safe", encoding="utf-8")
    calls = []

    def git(*args):
        calls.append(args)
        if args[0] == "ls-files":
            return (name + "\0deleted.txt\0").encode()
        return ("private" + "@example.org").encode()

    monkeypatch.setattr(module, "git", git)
    monkeypatch.setattr(sys, "argv", ["privacy_scan.py", "--worktree"])
    assert module.main() == 1
    assert f"{name}:0:private" in capsys.readouterr().out
    assert calls == [("ls-files", "--cached", "--others", "--exclude-standard", "-z")]
    (tmp_path / name).unlink()
    assert module.main() == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["privacy_scan.py"])
    assert module.main() == 1
    assert "<git-log>:1:email" in capsys.readouterr().out


def test_optional_rules_missing_default_and_invalid(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "privacy_scan", Path(__file__).parents[1] / "tools/privacy_scan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("PRIVACY_SCAN_EXTRA", raising=False)
    assert module.load_extra() == []
    assert "absent" in capsys.readouterr().out
    default = tmp_path / ".git/info/privacy-patterns.txt"
    default.parent.mkdir(parents=True)
    default.write_text("# comment\n\nACME\n", encoding="utf-8")
    assert module.findings("sample", "ACME", module.load_extra()) == ["private"]
    default.write_text("[", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["privacy_scan.py", "--worktree"])
    assert module.main() == 1
    assert "read-error" in capsys.readouterr().out
