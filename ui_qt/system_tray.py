"""System tray icon and menu."""
import logging
import os
import sys
from typing import Optional
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QAction, QCursor, QGuiApplication
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from ui_qt.utils.app_icon import tray_icon

logger = logging.getLogger(__name__)


def tray_host_paths(executable: str, base_executable: str = "") -> set[str]:
    """Executables Windows may record for this process's tray icon.

    A source install's ``sys.executable`` is the venv shim. Explorer records
    the real image, ``sys._base_executable``, and the windowed ``pythonw``
    sibling when the app was launched without a console.
    """
    paths: set[str] = set()
    for raw in (executable, base_executable):
        if not raw:
            continue
        path = os.path.abspath(raw)
        paths.add(os.path.normcase(path))
        folder, name = os.path.split(path)
        stem, ext = os.path.splitext(name)
        if stem.lower().endswith("python"):
            sibling = os.path.join(folder, stem + "w" + ext)
            paths.add(os.path.normcase(os.path.abspath(sibling)))
    return paths


def _promote_tray_icon() -> bool:
    """Set IsPromoted for this process's tray icon. False if Windows has no row yet."""
    import winreg

    targets = tray_host_paths(
        sys.executable, getattr(sys, "_base_executable", "")
    )
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings")
    except OSError:
        return False
    try:
        count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            name = winreg.EnumKey(root, index)
            try:
                sub = winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
            except OSError:
                continue
            try:
                path, _ = winreg.QueryValueEx(sub, "ExecutablePath")
            except OSError:
                winreg.CloseKey(sub)
                continue
            if os.path.normcase(path) not in targets:
                winreg.CloseKey(sub)
                continue
            winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, 1)
            winreg.CloseKey(sub)
            return True
    finally:
        winreg.CloseKey(root)
    return False

# macOS status items never report DoubleClick, and an attached context menu
# swallows the left click to pop itself up. There, a left click restores the
# window and the menu is shown on right/Ctrl-click instead.
_CLICK_TO_RESTORE = sys.platform == "darwin"


class SystemTrayManager(QSystemTrayIcon):
    """Manages system tray icon and menu."""

    show_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    exit_requested = pyqtSignal()
    toggle_recording = pyqtSignal()
    meeting_toggle_requested = pyqtSignal()
    meeting_dashboard_requested = pyqtSignal()
    copy_last_original_requested = pyqtSignal()
    walkthrough_requested = pyqtSignal()
    screenshot_requested = pyqtSignal()
    scratchpad_requested = pyqtSignal()
    stats_requested = pyqtSignal()
    update_requested = pyqtSignal()
    # Emitted as the Language submenu opens, so a connected slot can refill
    # it with the current choices before it is drawn.
    language_menu_requested = pyqtSignal(object)

    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self._meeting_active = False
        self._recording = False
        self._loudness_step = -1
        self._promoted = False
        self._promote_tries = 0
        self.available = bool(QSystemTrayIcon.isSystemTrayAvailable())

        self._setup_icon()
        self._setup_menu()
        self._connect_signals()

        if self.available:
            self.show()
            logger.info("System tray initialized")
        else:
            logger.warning(
                "No system tray is available; close-to-tray and tray controls "
                "are disabled"
            )

    def _setup_icon(self):
        self.setIcon(tray_icon())
        self.setToolTip("OpenWhisper")

    def show(self):
        super().show()
        self._ensure_promoted()

    def _ensure_promoted(self) -> None:
        """Ask Windows 11 to keep the circle in the corner, not the overflow."""
        if self._promoted or sys.platform != "win32":
            return
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        self._promote_tries += 1
        if not _promote_tray_icon():
            if self._promote_tries < 8:
                QTimer.singleShot(400, self._ensure_promoted)
            return
        self._promoted = True
        # Explorer only moves an icon out of the overflow when it is registered
        # again after IsPromoted is set.
        super().hide()
        super().show()

    def _setup_menu(self):
        self.menu = QMenu()

        show_action = self.menu.addAction("Show")
        show_action.triggered.connect(self._on_show)

        hide_action = self.menu.addAction("Hide")
        hide_action.triggered.connect(self._on_hide)

        self.menu.addSeparator()
        self.toggle_action = self.menu.addAction("Start Recording")
        self.toggle_action.triggered.connect(self._on_toggle)
        self.copy_original_action = self.menu.addAction(
            "Copy original of last dictation"
        )
        self.copy_original_action.triggered.connect(
            self.copy_last_original_requested
        )
        self.walkthrough_action = self.menu.addAction("Start Walkthrough")
        self.walkthrough_action.triggered.connect(self.walkthrough_requested)
        self.screenshot_action = self.menu.addAction("Take Screenshot")
        self.screenshot_action.triggered.connect(self.screenshot_requested)
        self.language_menu = self.menu.addMenu("Language")
        self.language_menu.aboutToShow.connect(self._on_language_menu_about_to_show)

        self.menu.addSeparator()
        self.meeting_toggle_action = self.menu.addAction("Start Meeting")
        self.meeting_toggle_action.triggered.connect(self._on_meeting_toggle)
        self.meeting_dashboard_action = self.menu.addAction("Open Meeting Dashboard")
        self.meeting_dashboard_action.triggered.connect(self._on_meeting_dashboard)
        self.meeting_dashboard_action.setEnabled(False)

        self.menu.addSeparator()
        self.scratchpad_action = self.menu.addAction("Scratchpad")
        self.scratchpad_action.triggered.connect(self.scratchpad_requested)
        self.stats_action = self.menu.addAction("Stats")
        self.stats_action.triggered.connect(self.stats_requested)
        settings_action = self.menu.addAction("Settings")
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        settings_action.triggered.connect(self._on_settings)

        self.menu.addSeparator()
        # Shown only while a newer release is waiting.
        self.update_action = self.menu.addAction("")
        self.update_action.setMenuRole(QAction.MenuRole.NoRole)
        self.update_action.triggered.connect(self.update_requested)
        self.update_action.setVisible(False)
        exit_action = self.menu.addAction("Exit")
        exit_action.setMenuRole(QAction.MenuRole.NoRole)
        exit_action.triggered.connect(self._on_exit)

        if not _CLICK_TO_RESTORE:
            self.setContextMenu(self.menu)

    def _connect_signals(self):
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        Reason = QSystemTrayIcon.ActivationReason
        if not _CLICK_TO_RESTORE:
            if reason == Reason.DoubleClick:
                self._on_show()
            return

        # Qt reports a Ctrl-click (the Mac right-click) as a plain Trigger.
        ctrl_click = bool(
            QGuiApplication.queryKeyboardModifiers()
            & Qt.KeyboardModifier.MetaModifier
        )
        if reason == Reason.Context or (reason == Reason.Trigger and ctrl_click):
            self._popup_menu()
        elif reason in (Reason.Trigger, Reason.DoubleClick):
            self._on_show()

    def _popup_menu(self):
        """Show the menu under the status item (it is not attached on macOS)."""
        geometry = self.geometry()
        if geometry.isValid():
            self.menu.popup(geometry.bottomLeft())
        else:
            self.menu.popup(QCursor.pos())

    def _on_show(self):
        if self.main_window:
            self.main_window.restore_from_tray()

        self.show_requested.emit()

    def _on_hide(self):
        if self.main_window:
            self.main_window.hide()

        self.hide_requested.emit()

    def _on_toggle(self):
        self.toggle_recording.emit()

    def _on_meeting_toggle(self):
        self.meeting_toggle_requested.emit()

    def _on_meeting_dashboard(self):
        self.meeting_dashboard_requested.emit()

    def _on_language_menu_about_to_show(self):
        self.language_menu_requested.emit(self.language_menu)

    def _on_settings(self):
        if self.main_window:
            self.main_window.open_settings()

    def _on_exit(self):
        self.exit_requested.emit()

    def set_recording(self, is_recording: bool):
        self._recording = bool(is_recording)
        self._loudness_step = -1
        self.setIcon(tray_icon(recording=self._recording, loudness=0.0))
        if self._recording:
            self.toggle_action.setText("Stop Recording")
            self.setToolTip("OpenWhisper — recording")
        else:
            self.toggle_action.setText("Start Recording")
            self.setToolTip("OpenWhisper")

    def hear(self, level: float) -> None:
        """Brighten the corner dot with microphone loudness while recording."""
        if not self._recording:
            return
        from ui_qt.utils.loudness import level_to_height

        step = int(round(level_to_height(level) * 8))
        if step == self._loudness_step:
            return
        self._loudness_step = step
        self.setIcon(tray_icon(recording=True, loudness=step / 8))

    def set_walkthrough_active(self, active: bool) -> None:
        self.walkthrough_action.setText("Finish Walkthrough" if active else "Start Walkthrough")

    def set_update_available(self, version: str) -> None:
        """Offer the newer release ``version`` in the menu; "" withdraws it."""
        self.update_action.setText(f"Update to {version}…")
        self.update_action.setVisible(bool(version))
        self.setToolTip(
            f"OpenWhisper: update {version} available" if version else "OpenWhisper"
        )

    def set_meeting_active(
        self, active: bool, dashboard_available: Optional[bool] = None
    ):
        """Update meeting actions, optionally overriding dashboard availability."""
        self._meeting_active = bool(active)
        if self._meeting_active:
            self.meeting_toggle_action.setText("End Meeting")
        else:
            self.meeting_toggle_action.setText("Start Meeting")
        if dashboard_available is None:
            dashboard_available = self._meeting_active
        self.meeting_dashboard_action.setEnabled(bool(dashboard_available))
