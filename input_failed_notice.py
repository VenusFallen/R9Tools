"""
input_failed_notice.py — non-modal "R9Tools has lost input" notification,
plus the modal dismiss-confirm loop that follows it.

Two independent trigger paths feed the same window, in-place:

  - OLD path: bridge.inputEngineFailed (recoil.py's consecutive-I/O-
    exception counter — see RecoilEngine._onInterceptionIoFailure). No-arg,
    fires exactly once per session, no specific device known. trigger()
    puts this window into "old" mode: a single "Dismiss" button leading
    into a modal restart-required confirmation. This mode is STICKY for
    the rest of the session — see triggerDevices()/recoverDevices() below
    — since a broad, untargeted restart is riskier than one aimed at a
    known device (confirmed this session: `pnputil /restart-device`
    without a specific target broke a previously-working device and
    required a full reboot to fix), so once we're in this mode we never
    offer the new path's "Try to Reconnect" action.

  - NEW path: bridge.deviceInputFailed / bridge.deviceInputRecovered
    (device_watch.py's WM_DEVICECHANGE-based detection — see
    DeviceFailureWatcher). Carries specific device instance ID(s), and can
    fire/clear more than once per session (a device can be disabled,
    reconnected, then disabled again). triggerDevices()/recoverDevices()
    put/keep this window in "device" mode: a second "Try to Reconnect"
    button alongside "Ignore", targeting exactly the currently-known-failed
    device(s) via Disable-PnpDevice + Enable-PnpDevice (confirmed live to
    recover an already-broken Interception connection with zero app
    restart/reboot — see _attempt_pnp_reconnect()). No success/failure
    claim is ever made for a reconnect attempt — recovery is only ever
    reported back through the same WM_DEVICECHANGE detection that's
    already watching (recoverDevices(), called independently of whether a
    reconnect was even attempted).

Both paths ultimately share one window instance rather than stacking two
popups — see trigger()/triggerDevices() for exactly how they're unified.

Independent top-level window, deliberately NOT parented into
PanelWindow's (normally hidden) widget tree: the whole point is that this
can appear on its own even while the panel/topbar are hidden and the
Insert hotkey that would normally reveal them is dead (no input capture
at all once the old path has fired; the new path may still leave *other*
devices working, but the failed device(s) themselves are unresponsive).
"""
import logging
import subprocess
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

import theme

# Confirmed live this session: disabling then re-enabling the SPECIFIC
# failed device's instance ID recovers an already-open, already-broken
# Interception connection with zero app restart and zero reboot. Do NOT
# use `pnputil /restart-device` here or anywhere else — that was also
# tested and BROKE a working device, requiring a full reboot to fix.
_PNP_TIMEOUT_S = 20


def _attempt_pnp_reconnect(instance_id: str) -> None:
    """Disable then re-enable one specific PnP device via PowerShell.
    Never raises — failures are logged only, since the caller deliberately
    makes no success/failure claim to the user (see module docstring).
    Matches this codebase's existing pattern of shelling out to
    sc.exe/tasklist.exe for driver/process control (main.py) rather than a
    native Win32 CM_* API — reliability over micro-optimizing away a
    process spawn for a one-time, user-initiated action."""
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

    def __init__(self, bridge):
        super().__init__(None, Qt.WindowType.Window)
        self._bridge = bridge

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
    # OLD path — bridge.inputEngineFailed. Behavior unchanged from before
    # the new path existed, except it now also takes over ("converts") an
    # already-visible "device" mode window in place, instead of a second
    # window ever appearing — see module docstring.
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
            "control are no longer working. A system restart is required "
            "to fix this."
        )
        self._reconnectBtn.hide()
        self._dismissBtn.setText("Dismiss")
        self._dismissBtn.setEnabled(True)
        self._show()

    # ------------------------------------------------------------------
    # NEW path — bridge.deviceInputFailed / bridge.deviceInputRecovered.
    # Both are emitted with the QueuedConnection PySide6 applies to any
    # cross-thread signal, and in this case are also emitted directly from
    # the Qt main thread (nativeEventFilter callbacks) to begin with — see
    # device_watch.py / panel_window.py / main.py — so this is always safe
    # to touch widgets directly here too.
    # ------------------------------------------------------------------

    def triggerDevices(self, ids: list):
        """A settled batch of failed device instance IDs. See
        device_watch.DeviceFailureWatcher for exactly when this fires
        (debounced + batched — never on every raw remove/arrival blip)."""
        if self._mode == "old":
            # Old path is sticky/terminal for the session — see module
            # docstring for why we don't downgrade an unrecoverable-restart
            # notice back into a recoverable-looking one.
            return
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
        every previously-failed device has come back (whether from a
        manual physical reconnect, a successful "Try to Reconnect"
        attempt, or any other cause) — this is the ONLY place that clears
        "device" mode; no explicit success/failure claim is ever made for
        a reconnect attempt itself (see _onReconnectClicked)."""
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
        if self._reconnecting or not self._deviceIds:
            return
        self._reconnecting = True
        self._reconnectBtn.setEnabled(False)
        self._reconnectBtn.setText("Reconnecting...")
        ids = list(self._deviceIds)
        threading.Thread(target=self._reconnectThread, args=(ids,), daemon=True).start()

    def _reconnectThread(self, ids: list):
        """Runs on a background thread — never touches Qt widgets directly
        (see bridge.reconnectAttemptFinished, auto-queued back onto the
        main thread). Each PnP cmdlet call has real latency (~1-2s to
        settle based on live testing), hence the background thread."""
        for instance_id in ids:
            _attempt_pnp_reconnect(instance_id)
        self._bridge.reconnectAttemptFinished.emit()

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
            # Single, documented quit path for this app — see main.py's
            # app.setQuitOnLastWindowClosed(False) comment: "All real quit
            # paths in this app are explicit (bridge.quitRequested ->
            # app.quit(), the topbar's quit button, and updater's
            # post-install app.quit())". Goes through the normal
            # engine.stop() shutdown sequence, which is safe to call even
            # with a dead/failed Interception context.
            self._bridge.quitRequested.emit()
            return

        # Canceled — the user isn't ready to restart yet. Re-show the exact
        # same notice so they retain an easy way back into this flow later,
        # rather than being left with only the passive red badge.
        self._show()
