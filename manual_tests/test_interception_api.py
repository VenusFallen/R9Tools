"""
Inspects MouseStroke and KeyStroke attribute names.
Run as administrator. Follow prompts.
"""
import interception
import inspect
import threading

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"

# ---------------------------------------------------------------------------
# Test: receive a MouseStroke and dump all its attributes
# ---------------------------------------------------------------------------
print("\n=== Receive a MouseStroke and inspect attributes ===")
print("     >>> pt Exception:)
                print(f"       .{attr}() [no sig]")
        else:
            print(f"       .{attr} = {val!r}")

# Press and release a MOUSE BUTTON now <<<")

result = [None]
error = [None]

def capture_mouse():
    try:
        inter = interception.Interception()
        inter.set_filter(inter.is_mouse, interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)
        device_idx = inter.await_input(10000)
        if device_idx is None:
            error[0] = "Timed out"
            return
        device = inter._devices[device_idx]
        stroke = device.receive()
        device.send(stroke)
        result[0] = stroke
        inter.destroy()
    except Exception as e:
        error[0] = f"{type(e).__name__}: {e}"

t = threading.Thread(target=capture_mouse, daemon=True)
t.start()
t.join(timeout=12)

if error[0]:
    print(f"{FAIL} {error[0]}")
elif result[0] is None:
    print(f"{FAIL} No result")
else:
    stroke = result[0]
    print(f"{PASS} Got {type(stroke).__name__}")
    print(f"     All attributes:")
    for attr in [a for a in dir(stroke) if not a.startswith('__')]:
        val = getattr(stroke, attr)
        if callable(val):
            try:
                print(f"       .{attr}{inspect.signature(val)}")
            exce---------------------------------------------------------------------------
# Also statically inspect MouseStroke and KeyStroke constructors
# ---------------------------------------------------------------------------
print("\n=== MouseStroke constructor ===")
try:
    print(f"     {inspect.signature(interception.MouseStroke)}")
except Exception as e:
    print(f"     {e}")

print("\n=== KeyStroke constructor ===")
try:
    print(f"     {inspect.signature(interception.KeyStroke)}")
except Exception as e:
    print(f"     {e}")

# ---------------------------------------------------------------------------
# Test: receive a KeyStroke and dump all its attributes
# ---------------------------------------------------------------------------
print("\n=== Receive a KeyStroke and inspect attributes ===")
print("     >>> Press and release any KEYBOARD KEY now <<<")

result2 = [None]
error2 = [None]

def capture_keyboard():
    try:
        inter = interception.Interception()
        inter.set_filter(inter.is_keyboard, interception.FilterKeyFlag.FILTER_KEY_ALL)
        device_idx = inter.await_input(10000)
        if device_idx is None:
            error2[0] = "Timed out"
            return
        device = inter._devices[device_idx]
        stroke = device.receive()
        device.send(stroke)
        result2[0] = stroke
        inter.destroy()
    except Exception as e:
        error2[0] = f"{type(e).__name__}: {e}"

t2 = threading.Thread(target=capture_keyboard, daemon=True)
t2.start()
t2.join(timeout=12)

if error2[0]:
    print(f"{FAIL} {error2[0]}")
elif result2[0] is None:
    print(f"{FAIL} No result")
else:
    stroke = result2[0]
    print(f"{PASS} Got {type(stroke).__name__}")
    print(f"     All attributes:")
    for attr in [a for a in dir(stroke) if not a.startswith('__')]:
        val = getattr(stroke, attr)
        if callable(val):
            try:
                print(f"       .{attr}{inspect.signature(val)}")
            except Exception:
                print(f"       .{attr}() [no sig]")
        else:
            print(f"       .{attr} = {val!r}")

print("\n=== All tests complete ===")
