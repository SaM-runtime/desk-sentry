import os

import pytest

# Keep test windows off the real desktop; set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Offscreen Qt has no system fonts; point it at Windows' so metrics match the real UI.
_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
if os.path.isdir(_fonts):
    os.environ.setdefault("QT_QPA_FONTDIR", _fonts)


@pytest.fixture(autouse=True)
def isolated_camera_list(monkeypatch):
    """Window tests must not enumerate devices on the test host."""
    monkeypatch.setattr("desk_sentry.camera_shared.list_cameras", lambda timeout=3.0: [])
