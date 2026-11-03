from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from desk_sentry import config
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.sentry import Role, Status
from desk_sentry.ui.mini_mirror import DEFAULT_SIZE, EDGE, MiniMirror


def alert() -> Snapshot:
    return Snapshot(
        status=Status.ALERT,
        frame=np.zeros((540, 960, 3), np.uint8),
        tracks=(TrackView(2, (0.05, 0.2, 0.15, 0.6), Role.INTRUDER, 0.8),),
        intruders=1,
    )


def test_restore_offscreen_geometry_falls_back_to_corner(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.restore((-50_000, -50_000, 300, 200))
    assert (mini.width(), mini.height()) == DEFAULT_SIZE


def test_restore_keeps_saved_geometry(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.restore((40, 50, 320, 190))
    assert (mini.x(), mini.y(), mini.width(), mini.height()) == (40, 50, 320, 180)


def test_renders_alert_and_reports_geometry(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    saved: list[tuple[int, int, int, int]] = []
    mini.on_geometry = saved.append
    mini.restore((40, 50, 320, 190))
    mini.show()
    mini.set_snapshot(alert())
    mini.grab()  # paints frame, badge and alert border
    mini.resize(400, 240)
    qtbot.waitUntil(lambda: bool(saved) and saved[-1][2:] == (400, 225), timeout=3000)


def test_double_click_expands(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    expanded: list[bool] = []
    mini.on_expand = lambda: expanded.append(True)
    mini.restore(None)
    mini.show()
    qtbot.mouseDClick(mini.view, Qt.MouseButton.LeftButton)
    assert expanded == [True]


def test_edges_detection(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.resize(300, 200)
    assert mini._edges(QPoint(1, 1)) == Qt.Edge.LeftEdge | Qt.Edge.TopEdge
    assert mini._edges(QPoint(299, 100)) == Qt.Edge.RightEdge
    assert mini._edges(QPoint(150, 100)) == Qt.Edge(0)
    assert mini._edges(QPoint(150, mini.height() - EDGE // 2)) == Qt.Edge.BottomEdge


def test_compact_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    config.save(Settings(compact_mode=True, compact_geometry=(10, 20, 300, 180)), path)
    loaded = config.load(path)
    assert loaded.compact_mode is True
    assert loaded.compact_geometry == (10, 20, 300, 180)
    config.save(Settings(compact_geometry=(10, 20, 5, 5)), path)
    assert config.load(path).compact_geometry is None


def test_native_top_edge_resize_uses_height_and_keeps_horizontal_center(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.setGeometry(100, 100, 384, 216)
    mini.show()
    proposed = QRect(100, 40, 384, 276)
    mini._native_resize_edges = Qt.Edge.TopEdge
    QWidget.setGeometry(mini, proposed)
    QApplication.processEvents()
    assert (mini.width(), mini.height()) == (491, 276)
    assert mini.geometry().center().x() == proposed.center().x()
    assert mini.geometry().bottom() == proposed.bottom()


def test_native_top_corner_resize_uses_width_and_keeps_bottom(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.setGeometry(100, 100, 384, 216)
    mini.show()
    proposed = QRect(80, 70, 480, 300)
    mini._native_resize_edges = Qt.Edge.LeftEdge | Qt.Edge.TopEdge
    QWidget.setGeometry(mini, proposed)
    QApplication.processEvents()
    assert (mini.width(), mini.height()) == (480, 270)
    assert mini.geometry().right() == proposed.right()
    assert mini.geometry().bottom() == proposed.bottom()


def test_native_bottom_edge_resize_uses_height_and_keeps_top(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.setGeometry(100, 100, 384, 216)
    mini.show()
    proposed = QRect(100, 100, 384, 270)
    mini._native_resize_edges = Qt.Edge.BottomEdge
    QWidget.setGeometry(mini, proposed)
    QApplication.processEvents()
    assert (mini.width(), mini.height()) == (480, 270)
    assert mini.geometry().center().x() == proposed.center().x()
    assert mini.geometry().top() == proposed.top()


def test_native_right_edge_resize_uses_width_and_keeps_vertical_center(qtbot: QtBot) -> None:
    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.setGeometry(100, 100, 384, 216)
    mini.show()
    proposed = QRect(100, 100, 480, 216)
    mini._native_resize_edges = Qt.Edge.RightEdge
    QWidget.setGeometry(mini, proposed)
    QApplication.processEvents()
    assert (mini.width(), mini.height()) == (480, 270)
    assert mini.geometry().left() == proposed.left()
    assert mini.geometry().center().y() == proposed.center().y()
