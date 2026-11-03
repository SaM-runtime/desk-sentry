"""Cover selection is independent of action; feedback shortcuts reject every collision."""

from PySide6.QtGui import QKeySequence

from desk_sentry.config import Settings
from desk_sentry.ui.main_window import MainWindow


def test_cover_single_picker_preserves_saved_exe_and_enables_foreground_restore(qtbot):
    saved = []
    apps = [("private.exe", "Private"), ("cover.exe", "Cover")]
    window = MainWindow(
        Settings(action="none", cover_app="saved.exe"), saved.append, list_apps=lambda: list(apps)
    )
    qtbot.addWidget(window)
    window.show()
    assert window.cover_app.currentData() == "saved.exe"
    assert window.restore_after_alert_row.isVisibleTo(window)
    window.cover_app.setCurrentIndex(window.cover_app.findData("cover.exe"))
    window.cover_app.activated.emit(window.cover_app.currentIndex())
    assert saved[-1].cover_app == "cover.exe" and saved[-1].action == "none"
    window.reload_apps()
    assert window.cover_app.currentData() == "cover.exe"
    window.cover_app.setCurrentIndex(0)
    window.cover_app.activated.emit(0)
    assert saved[-1].cover_app == ""
    assert not window.restore_after_alert_row.isVisible()


def test_marker_edits_reject_duplicate_and_keep_prior_registered_binding(qtbot):
    saved = []
    window = MainWindow(Settings(mark_false_hotkey="Ctrl+Alt+F"), saved.append)
    qtbot.addWidget(window)
    window.mark_real_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+F"))
    window.mark_real_hotkey.editingFinished.emit()
    assert window.settings.mark_real_hotkey == "" and saved == []
    window.mark_real_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+H"))
    window.mark_real_hotkey.editingFinished.emit()
    assert window.settings.mark_real_hotkey == "" and saved == []
    window.mark_real_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+R"))
    window.mark_real_hotkey.editingFinished.emit()
    assert saved[-1].mark_real_hotkey == "Ctrl+Alt+R"
    window.hide_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+R"))
    window.hide_hotkey.editingFinished.emit()
    assert window.settings.hide_hotkey == "Ctrl+Alt+H"
    window.hotkey.setKeySequence(QKeySequence("Ctrl+Alt+F"))
    window.hotkey.editingFinished.emit()
    assert window.settings.mirror_hotkey == "Ctrl+Alt+M"
