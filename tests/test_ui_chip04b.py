"""Task 04b regression coverage for long target-program chips."""

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow


def test_long_target_app_chip_stays_inside_settings_panel(qtbot: QtBot) -> None:
    exe = f"{'a' * 256}.exe"
    removed: list[str] = []
    window = MainWindow(
        Settings(action="minimize_app", target_apps=(exe,)),
        lambda _settings: None,
        list_apps=lambda: [],
    )
    qtbot.addWidget(window)
    window.show()

    chip = window.target_app_chips[0]
    chip.removed.connect(removed.append)
    label = chip.findChild(QLabel, "targetAppChipText")
    assert label is not None
    scroll = window.findChild(QScrollArea, "settingsScroll")
    assert scroll is not None

    def assert_bounded() -> None:
        scroll.ensureWidgetVisible(chip.remove_button)
        QApplication.processEvents()
        remove_rect = chip.remove_button.rect().translated(
            chip.remove_button.mapTo(scroll.viewport(), QPoint(0, 0))
        )
        assert chip.width() <= theme.D["target_chip_max_width"]
        assert window.target_chip_layout.minimumSize().width() <= theme.D["target_chip_max_width"]
        assert chip.remove_button.isVisibleTo(window)
        assert scroll.viewport().rect().contains(remove_rect)

    window.resize(1180, 640)
    assert_bounded()
    window.resize(700, 640)
    window.open_drawer()
    qtbot.waitUntil(lambda: window._drawer_animation is None, timeout=1000)
    assert_bounded()

    assert label.text() != exe
    assert "…" in label.text()
    assert label.toolTip() == exe
    chip.remove_button.click()
    assert removed == [exe]
