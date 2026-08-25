"""
input_failed_notice.py — non-modal "R9Tools has lost input" notification,
plus the modal dismiss-confirm loop that follows it.

Two independent trigger paths feed the same window in place rather than
stacking two popups:

  - OLD path: bridge.inputEngineFailed (recoil.py's I/O-exception counter).
    No device known, fires once per session; puts the window in "old" mode
    (Dismiss, leading into a restart-required confirmation, plus a "Try to
    Reconnect" of its own — see _oldPathReconnectThread()). "old" mode is
    sticky against being overridden by a later triggerDevices() call (see
    triggerDevices()), but is NOT permanently stuck for the session the way
    the module used to be: old-mode's reconnect cycles every
    currently-present keyboard/mouse device (no specific device is known,
    unlike the device-specific path below) and then calls
    RecoilEngine.retryBringUp(), which returns a real, verified
    success/failure result rather than an inferred one — a True result
    clears "old" mode and closes the notice outright, since input capture
    is genuinely confirmed working again at that point.

  - NEW path: bridge.deviceInputFailed / bridge.deviceInputRecovered
    (device_watch.py's WM_DEVICECHANGE detection). Carries specific device
    instance ID(s) and can fire/clear repeatedly; puts/keeps the window in
    "device" mode with a "Try to Reconnect" button that disables+re-enables
    exactly the failed device(s) (see _attempt_pnp_reconnect()). No success/
    failure claim is made for a reconnect attempt — recovery is only ever
    reported back through the same WM_DEVICECHANGE detection.

Independent top-level window, deliberately NOT parented into PanelWindow's
widget tree: it can appear on its own even while the panel/topbar (and the
hotkey that would reveal them) are hidden.
"""
import logging
import subprocess
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

import theme

# Disabling then re-enabling the specific failed device's instance ID
# recovers an already-broken Interception connection with no app restart or
# reboot needed. Do NOT use `pnputil /restart-device` instead — it can break
# a working device and force a full reboot to fix.
_PNP_TIMEOUT_S = 20


def _enumerate_keyboard_mouse_devices() -> list[str]:
    """Enumerate the instance IDs of every currently-present device in the
    Keyboard and Mouse PnP classes, for the OLD path's "Try to Reconnect"
    (no specific failed device is known there, unlike the device-specific
    path). Never raises — an enumeration failure just means the reconnect
    attempt below cycles zero devices before still calling
    RecoilEngine.retryBringUp() on its own."""
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "(Get-PnpDevice -Class Keyboard,Mouse -PresentOnly).InstanceId",
            ],
            capture_output=True, text=True, timeout=_PNP_TIMEOUT_S,
        )
        if result.returncode != 0:
            logging.warning(
                "Get-PnpDevice enumeration failed (exit %d): %s",
                result.returncode,
                (result.stderr or result.stdout or "<no output>").strip(),
            )
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        logging.exception("Get-PnpDevice enumeration raised")
        return []


def _attempt_pnp_reconnect(instance_id: str) -> None:
    """Disable then re-enable one specific PnP device via PowerShell.
    Never raises — failures are logged only, since the caller makes no
    success/failure claim to the user (see module docstring)."""
    escaped = instance_id.replace("'", "''")  # defensive; instance IDs don't normally contain quotes
    for verb in ("Disable-PnpDevice", "Enable-PnpDevice"):
        cmd = f"{verb} -InstanceId '{escaped}' -Confirm:$false"
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=_PNP_TIMEOUT_S,
            )
            if result.returncode != 0:
                logging.warning(
                    "%s failed for device %r (exit %d): %s",
                    verb, instance_id, result.returncode,
                    (result.stderr or result.stdout or "<no output>").strip(),
                )
        except Exception:
            logging.exception("%s raised for device %r", verb, instance_id)


class InputFailedNotice(QWidget):
    """Non-modal notice window shown once input capture has failed, via
    either the old (dismiss-only) or new (reconnect-or-ignore) path."""

    def __init__(self, bridge, engine):
        super().__init__(None, Qt.WindowType.Window)
        self._bridge = bridge
        # Only needed for the OLD path's "Try to Reconnect"
        # (RecoilEngine.retryBringUp()) — the device-specific path never
        # touches the engine directly, only PowerShell.
        self._engine = engine

        # None = never triggered yet. "old"/"device" — see module docstring.
        self._mode = None
        self._deviceIds: list[str] = []
        self._reconnecting = False

        self.setWindowTitle("R9Tools — Input Lost")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(theme.makeQSS())
        self.setFixedWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("R9Tools has lost input", self)
        title.setStyleSheet(
            "font: bold 11pt 'Segoe UI Variable Display', 'Segoe UI'; "
            "color: #ff5555;"
        )
        layout.addWidget(title)

        self._body = QLabel("", self)
        self._body.setWordWrap(True)
        layout.addWidget(self._body)

        btnRow = QHBoxLayout()
        btnRow.addStretch()
        self._reconnectBtn = QPushButton("Try to Reconnect", self)
        self._reconnectBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reconnectBtn.clicked.connect(self._onReconnectClicked)
        self._reconnectBtn.hide()  # only shown in "device" mode
        btnRow.addWidget(self._reconnectBtn)
        self._dismissBtn = QPushButton("Dismiss", self)
        self._dismissBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismissBtn.clicked.connect(self._onDismissOrIgnore)
        btnRow.addWidget(self._dismissBtn)
        layout.addLayout(btnRow)

    # ------------------------------------------------------------------
    # OLD path — bridge.inputEngineFailed. Converts an already-visible
    # "device" mode window in place rather than stacking a second window.
    # ------------------------------------------------------------------

    def trigger(self):
        """Connected to bridge.inputEngineFailed. That signal is emitted
        from the engine's listen thread and delivered here via PySide6's
        automatic cross-thread QueuedConnection, so this always runs on the
        Qt main thread — safe to touch widgets directly."""
        self._mode = "old"
        self._deviceIds = []
        self._reconnecting = False
        self._body.setText(
            "R9Tools has lost input — hotkeys, macros, remaps, and recoil "
            "control are no longer working. \"Try to Reconnect\" will "
            "attempt to reset your keyboard and mouse and restart input "
            "capture, but this is not guaranteed to work. If it doesn't "
            "help, you'll need to restart your computer to fix this."
        )
        self._reconnectBtn.setText("Try to Reconnect")
        self._reconnectBtn.setEnabled(True)
        self._reconnectBtn.show()
        self._dismissBtn.setText("Dismiss")
        self._dismissBtn.setEnabled(True)
        self._show()

    # ------------------------------------------------------------------
    # NEW path — bridge.deviceInputFailed / bridge.deviceInputRecovered.
    # Both fire from nativeEventFilter callbacks already on the Qt main
    # thread, so it's safe to touch widgets directly here too.
    # ------------------------------------------------------------------

    def triggerDevices(self, ids: list):
        """A settled batch of failed device instance IDs. See
        device_watch.DeviceFailureWatcher for exactly when this fires
        (debounced + batched — never on every raw remove/arrival blip)."""
        if self._mode == "old":
            return  # old path is sticky/terminal for the session — see module docstring
        self._mode = "device"
        self._deviceIds = list(ids)
        self._reconnecting = False
        self._updateDeviceBody()
        self._reconnectBtn.setText("Try to Reconnect")
        self._reconnectBtn.setEnabled(True)
        self._reconnectBtn.show()
        self._dismissBtn.setText("Ignore")
        self._dismissBtn.setEnabled(True)
        self._show()

    def recoverDevices(self, ids: list):
        """The remaining settled-failed device set after a recovery — see
        device_watch.DeviceFailureWatcher.on_recovered. An empty list means
        every previously-failed device has come back; this is the only
        place that clears "device" mode."""
        if self._mode != "device":
            return  # never triggered, or old path is sticky — nothing to update
        self._deviceIds = list(ids)
        if not self._deviceIds:
            self._mode = None
            self._reconnecting = False
            self.hide()
            return
        self._updateDeviceBody()

    def reconnectFinished(self):
        """The background Disable/Enable-PnpDevice attempt has completed
        (see bridge.reconnectAttemptFinished) — reverts the button from
        "Reconnecting..." back to "Try to Reconnect" without claiming
        success or failure either way (see module docstring)."""
        if self._mode != "device":
            return
        self._reconnecting = False
        self._reconnectBtn.setText("Try to Reconnect")
        self._reconnectBtn.setEnabled(bool(self._deviceIds))

    def _updateDeviceBody(self):
        n = len(self._deviceIds)
        plural = "s" if n != 1 else ""
        self._body.setText(
            f"R9Tools has lost input from {n} device{plural} — hotkeys, "
            "macros, remaps, and recoil control tied to it may no longer "
            "work. \"Try to Reconnect\" will attempt to reset the affected "
            "device(s), but this is not guaranteed to work. If it doesn't "
            "help, you'll have to restart your computer on your own."
        )

    def _onReconnectClicked(self):
        if self._reconnecting:
            return
        if self._mode == "device":
            if not self._deviceIds:
                return
            self._reconnecting = True
            self._reconnectBtn.setEnabled(False)
            self._reconnectBtn.setText("Reconnecting...")
            ids = list(self._deviceIds)
            threading.Thread(target=self._reconnectThread, args=(ids,), daemon=True).start()
        elif self._mode == "old":
            self._reconnecting = True
            self._reconnectBtn.setEnabled(False)
            self._reconnectBtn.setText("Reconnecting...")
            threading.Thread(target=self._oldPathReconnectThread, daemon=True).start()

    def _reconnectThread(self, ids: list):
        """Runs on a background thread — never touches Qt widgets directly
        (see bridge.reconnectAttemptFinished, auto-queued back onto the
        main thread). Each PnP cmdlet call has real latency (~1-2s to
        settle), hence the background thread."""
        for instance_id in ids:
            _attempt_pnp_reconnect(instance_id)
        self._bridge.reconnectAttemptFinished.emit()

    def _oldPathReconnectThread(self):
        """OLD path's "Try to Reconnect" — runs on a background thread,
        never touching Qt widgets directly (see bridge.bringUpRetryFinished,
        auto-queued back onto the main thread). No specific device is known
        here, so every currently-present keyboard/mouse device is cycled
        (unlike the device-specific path's fixed ID list) before asking the
        engine to retry bring-up. self._engine.retryBringUp() has its own
        internal retry/backoff (worst case ~8.75s — see recoil.py), on top
        of the PnP cycling time above, so this can run for several seconds;
        that is expected, not a hang."""
        for instance_id in _enumerate_keyboard_mouse_devices():
            _attempt_pnp_reconnect(instance_id)
        success = False
        if self._engine is not None:
            try:
                success = bool(self._engine.retryBringUp())
            except Exception:
                logging.exception("RecoilEngine.retryBringUp() raised")
        self._bridge.bringUpRetryFinished.emit(success)

    def oldPathReconnectFinished(self, success: bool):
        """Connected to bridge.bringUpRetryFinished — the OLD path
        counterpart to reconnectFinished() above. Unlike that method, the
        result here is a real, verified answer from
        RecoilEngine.retryBringUp() (see recoil.py), not an inferred one:
        on success the notice is fully cleared (mode reset, window hidden)
        rather than merely reverting the button, since input capture is
        actually confirmed working again."""
        if self._mode != "old":
            return
        self._reconnecting = False
        if success:
            self._mode = None
            self.hide()
            return
        self._reconnectBtn.setText("Try to Reconnect")
        self._reconnectBtn.setEnabled(True)

    # ------------------------------------------------------------------
    # Shared show / dismiss-or-ignore
    # ------------------------------------------------------------------

    def _show(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _onDismissOrIgnore(self):
        self.hide()
        if self._mode == "device":
            confirm_text = "Are you sure? You'll have to restart your computer on your own."
        else:
            confirm_text = (
                "Are you sure you want to dismiss? You'll need to restart "
                "your computer to fix this issue."
            )
        confirmed = QMessageBox.question(
            self,
            "R9Tools — Confirm",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

        if confirmed:
            # Goes through the normal engine.stop() shutdown sequence,
            # which is safe to call even with a dead/failed Interception context.
            self._bridge.quitRequested.emit()
            return

        # Canceled — the user isn't ready to restart yet. Re-show the exact
        # same notice so they retain an easy way back into this flow later,
        # rather than being left with only the passive red badge.
        self._show()
