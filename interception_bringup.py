"""
Shared helper for standing up a validated interception.Interception()
context and applying keyboard/mouse filters, retrying with backoff on
transient driver bring-up failures.

Why: Interception.__init__() silently swallows exceptions while opening its
20 device handles, and set_filter() then indexes them with no bounds
checking — a partial open turns into an unhandled IndexError. This module
gives every foreground, user-initiated keybind-capture thread (across both
UI stacks) the same construct + validate + retry pattern
RecoilEngine._bringUpInterception() uses for the background input engine,
so a bring-up failure can't leave a capture UI permanently stuck.

Intentionally dependency-free (no PySide6, no imgui, no dcomp) so both UI
stacks can import it without a cross-stack dependency.
"""
import logging
import time

import interception

log = logging.getLogger("r9tools.interception_bringup")

# Foreground, user-initiated capture threads block a modal "press a
# key..." UI, so this budget is much shorter than
# RecoilEngine._bringUpInterception()'s ~10s worst case — just enough to
# give a transient hiccup (e.g. driver still settling after a fresh
# install/reboot) a chance to clear.
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
            # A fully-settled driver context always has all 20 device slots
            # open — a short list means get_handles() failed partway
            # through and the exception was swallowed internally.
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


def destroyInterception(inter):
    """Tear down an interception.Interception() context returned by
    bringUpInterception(), releasing its filtered device handles. Safe to
    call with inter=None and safe to call more than once.

    Every capture site must call this exactly once, unconditionally, in its
    finally block, or the filtered handle stays open (and capturing
    matching strokes) for the rest of the process's lifetime.
    """
    if inter is None:
        return
    try:
        inter.destroy()
    except Exception:
        pass
