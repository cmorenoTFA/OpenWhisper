import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

import ui_qt.system_tray as system_tray
from ui_qt.system_tray import SystemTrayManager

Reason = QSystemTrayIcon.ActivationReason


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def _make_tray(click_to_restore: bool):
    with patch.object(system_tray, "_CLICK_TO_RESTORE", click_to_restore):
        window = MagicMock()
        tray = SystemTrayManager(window)
    tray.menu.popup = MagicMock()
    return tray, window


def _activate(tray, reason, click_to_restore, modifiers=Qt.KeyboardModifier.NoModifier):
    with patch.object(system_tray, "_CLICK_TO_RESTORE", click_to_restore), patch.object(
        system_tray.QGuiApplication, "queryKeyboardModifiers", return_value=modifiers
    ):
        tray._on_activated(reason)


class TestMacStatusItemClicks:
    def test_menu_is_not_attached_so_left_click_reaches_the_app(self):
        tray, _ = _make_tray(click_to_restore=True)
        assert tray.contextMenu() is None

    def test_left_click_restores_hidden_window(self):
        tray, window = _make_tray(click_to_restore=True)
        _activate(tray, Reason.Trigger, click_to_restore=True)
        window.restore_from_tray.assert_called_once()
        tray.menu.popup.assert_not_called()

    def test_right_click_opens_menu_without_restoring(self):
        tray, window = _make_tray(click_to_restore=True)
        _activate(tray, Reason.Context, click_to_restore=True)
        tray.menu.popup.assert_called_once()
        window.restore_from_tray.assert_not_called()

    def test_ctrl_click_opens_menu_without_restoring(self):
        tray, window = _make_tray(click_to_restore=True)
        # Qt maps the physical Control key to MetaModifier on macOS.
        _activate(
            tray,
            Reason.Trigger,
            click_to_restore=True,
            modifiers=Qt.KeyboardModifier.MetaModifier,
        )
        tray.menu.popup.assert_called_once()
        window.restore_from_tray.assert_not_called()


def test_tray_icon_is_a_circle_and_turns_red_while_recording():
    from ui_qt.utils.app_icon import render_tray_pixmap

    idle = render_tray_pixmap(32).toImage()
    assert idle.pixelColor(0, 0).alpha() == 0
    center = idle.pixelColor(16, 16)
    assert center.alpha() > 200
    assert center.blue() > center.red()

    tray, _ = _make_tray(click_to_restore=False)
    tray.set_recording(True)
    hot = tray.icon().pixmap(32).toImage().pixelColor(16, 16)
    assert hot.red() > center.red()
    assert tray.toggle_action.text() == "Stop Recording"


class TestOtherPlatformTrayClicks:
    def test_context_menu_stays_attached(self):
        tray, _ = _make_tray(click_to_restore=False)
        assert tray.contextMenu() is tray.menu

    def test_double_click_restores_and_single_click_does_not(self):
        tray, window = _make_tray(click_to_restore=False)
        _activate(tray, Reason.Trigger, click_to_restore=False)
        window.restore_from_tray.assert_not_called()
        _activate(tray, Reason.DoubleClick, click_to_restore=False)
        window.restore_from_tray.assert_called_once()
