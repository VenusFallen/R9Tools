"""
Shared helper for standing up a validated interception.Interception()
context and applying keyboard/mouse filters, retrying with backoff on
transient driver bring-up failures.

Why this exists
----------------
interception-python's Interception.__init__() opens handles to all 20
possible device slots (\\\\.\\interceptionNN) and silently swallows any
exception raised while doing so (it catches Exception internally and just
calls self.destroy(), leaving self._devices however-far it got — possibly
empty, possibly a partial list shorter than the 20 slots callers assume).
Interception.set_filter() then indexes self._devices[i] for i in range(20)
with zero bounds checking, so any transient failure to open even one device
handle turns into an unhandled IndexError the instant set_filter() runs.

This has been observed in the wild as a fresh-install-time issue — see
RecoilEngine._bringUpInterception() in recoil.py for the original diagnosis
and fix, which covers the background input-engine startup path. This module
extracts the same construct + validate + retry pattern for the several
foreground, user-initiated keybind-capture threads scattered across both UI
stacks (PySide6 panels + theme.py, and the DX11/ImGui panels), which hit the
exact same failure mode but previously ran interception setup *outside*
their try/finally — a failure there could leave a capture UI permanently
stuck (hotkeys suspended, capture flag never reset, no way to recover short
of restarting the app).

This module is intentionally dependency-free (no PySide6, no imgui, no
dcomp) so both UI stacks can import it without creating a cross-stack
dependency between their module layouts.
"""
import logging
import time

import interception

log = logging.getLogger("r9tools.interception_bringup")

# Foreground, user-initiated capture threads block a modal "press a
# key..." UI rather than the whole app, so this budget is intentionally
# much shorter than RecoilEngine._bringUpInterception()'s ~10s worst case —
# it just needs to give a genuinely transient hiccup (e.g. driver still
# settling shortly after a fresh install/reboot) a real chance to clear.
DEFAULT_ATTEMPTS   = 8
DEFAULT_BASE_DELAY = 0.05  # seconds, doubles each retry up to the cap
DEFAULT_MAX_DELAY  = 0.3


def bringUpInterception(configure, attempts=DEFAULT_ATTEMPTS,
                         base_delay=DEFAULT_BASE_DELAY,
                         max_delay=DEFAULT_MAX_DELAY,
                         should_continue=None, context="capture"):
    """Construct an interception.Interception() context, validate it opened
    all 20 device handles, and apply filters via configure(inter), retrying
    with backoff on failure.

    configure: callable(inter) -> None, invoked once the context validates
        as complete. Should call inter.set_filter(...) as needed. Any
        exception raised here is treated the same as a construction
        failure — the context is torn down and the attempt is retried.
    should_continue: optional callable() -> bool, polled after each failed
        attempt; if it returns False, bring-up is abandoned early (e.g. the
        capture was cancelled while a retry was pending) and None is
        returned without logging a hard failure.
    context: short human-readable label used in log messages to identify
        which capture site a given bring-up attempt/failure belongs to.

    Returns the ready Interception instance, or None if every retry was
    exhausted (or should_continue() returned False before that).
    """
    delay = base_delay
    lastErr = None
    for attempt in range(1, attempts + 1):
        inter = None
        try:
            inter = interception.Interception()
            # A fully-settled driver context always has all 20 device
            # slots open — a short list here means get_handles() hit a
            # failure partway through and the exception was swallowed
            # internally, i.e. this context is unusable even though
            # construction "succeeded".
            if len(inter._devices) < 20:
                raise RuntimeError(
                    f"Interception context incomplete: "
                    f"{len(inter._devices)}/20 device handles opened"
                )
            configure(inter)
            if attempt > 1:
                log.warning(
                    "[%s] Interception driver context came up on attempt %d/%d",
                    context, attempt, attempts,
                )
            return inter
        except Exception as exc:
            lastErr = exc
            if inter is not None:
                try:
                    inter.destroy()
                except Exception:
                    pass
            if should_continue is not None and not should_continue():
                return None
            if attempt == attempts:
                break
            time.sleep(delay)
            delay = min(delay * 2, max_delay)

    log.error(
        "[%s] Interception driver context failed to come up after %d "
        "attempts — capture cannot proceed. Last error: %r",
        context, attempts, lastErr,
    )
    return None
