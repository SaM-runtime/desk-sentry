"""TASK16 removes unused Qt libraries while preserving Widgets runtime."""

from test_packaging09 import _load_helper


def test_prune_labs_and_lottie_dlls_only(tmp_path):
    helper = _load_helper()
    site = tmp_path / "site"
    qt = site / "PySide6"
    qt.mkdir(parents=True)
    unused = [qt / "Qt6LabsAnimation.dll", qt / "Qt6LabsSettings.dll", qt / "Qt6Lottie.dll"]
    retained = [qt / "Qt6Widgets.dll", qt / "Qt6Gui.dll", qt / "Qt6Core.dll"]
    for path in unused + retained:
        path.write_bytes(b"fixture")
    helper._prune_unused_bundle_files(site)
    assert all(not path.exists() for path in unused)
    assert all(path.is_file() for path in retained)
