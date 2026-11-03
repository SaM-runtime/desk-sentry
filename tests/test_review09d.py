"""Release path and synchronous, idempotent shutdown regression contracts."""

import ctypes
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from desk_sentry import config, lifecycle, schedule
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine
from test_app_review05c import local_app as local_app
from test_packaging09 import _load_helper


def test_spec_resolves_relative_metadata_against_project_in_every_cwd(tmp_path, monkeypatch):
    project = tmp_path / "project"
    other = tmp_path / "outside"
    (project / "packaging").mkdir(parents=True)
    other.mkdir()
    root = Path(__file__).resolve().parents[1]
    helper = _load_helper()
    monkeypatch.setitem(sys.modules, "build_release", helper)
    monkeypatch.setattr(sys, "path", [str(root / "packaging"), *sys.path])
    monkeypatch.setenv("DESKSENTRY_METADATA_DIR", ".tmp/release")
    prefix = (
        (root / "packaging/DeskSentry.spec").read_text(encoding="utf-8").split("a = Analysis(")[0]
    )
    observed = []
    for cwd in (project, other):
        monkeypatch.chdir(cwd)
        namespace = {"SPEC": str(project / "packaging/DeskSentry.spec")}
        exec(compile(prefix, "DeskSentry.spec", "exec"), namespace)
        directory = namespace["METADATA_DIR"]
        assert directory.is_absolute()
        assert directory == project / ".tmp/release"
        assert namespace["BUILD_INFO"] == directory / "_build_info.json"
        assert namespace["VERSION_INFO"] == directory / "version-info.txt"
        assert namespace["BUILD_INFO"].is_file()
        assert namespace["VERSION_INFO"].is_file()
        observed.append(directory)
    assert observed[0] == observed[1]
    assert not (other / ".tmp").exists()


@pytest.mark.parametrize("diagnostics", [True, False])
def test_confirmed_session_shutdown_finishes_before_filter_returns_and_only_once(
    local_app, qapp, monkeypatch, diagnostics
):
    events = []
    real_stop, real_flush, real_save = Engine.stop, DiagnosticsLog.flush, config.save

    def stop(engine):
        events.append("stop")
        real_stop(engine)

    def flush(log):
        events.append("flush")
        real_flush(log)

    def save(settings, path):
        events.append("save")
        real_save(settings, path)

    monkeypatch.setattr(Engine, "stop", stop)
    monkeypatch.setattr(DiagnosticsLog, "flush", flush)
    monkeypatch.setattr(config, "save", save)
    build, path, _ = local_app
    config.save(replace(config.load(path), diagnostics=diagnostics), path)
    instance = build()
    assert hasattr(instance, "shutdown_now"), "App needs a synchronous shutdown_now contract"
    handler = lifecycle.EndSessionFilter(
        int(instance.window.winId()), instance.quit, shutdown_now=instance.shutdown_now
    )
    instance.window.settings.sensitivity = "high"
    assert config.load(path).sensitivity == "medium"
    events.clear()
    msg = schedule._Message()
    msg.hwnd = int(instance.window.winId())
    msg.message = lifecycle.WM_QUERYENDSESSION
    assert handler.nativeEventFilter(None, ctypes.addressof(msg)) == (True, 1)
    assert events == []
    msg.message = lifecycle.WM_ENDSESSION
    msg.w_param = 1
    assert handler.nativeEventFilter(None, ctypes.addressof(msg)) == (True, 0)
    # No event loop: Windows can end the process as soon as this call returns.
    assert instance.engine._stop.is_set()
    assert events == ["stop", "flush", "save"]
    assert config.load(path).sensitivity == "high"
    qapp.aboutToQuit.emit()
    instance.shutdown_now()
    handler.nativeEventFilter(None, ctypes.addressof(msg))
    assert events == ["stop", "flush", "save"]


def test_normal_quit_defers_engine_stop_until_about_to_quit(local_app, qapp):
    build, _, _ = local_app
    instance = build()
    instance.quit()
    assert not instance.engine._stop.is_set()
    qapp.aboutToQuit.emit()
    assert instance.engine._stop.is_set()
