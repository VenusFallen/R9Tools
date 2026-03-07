"""
Diagnose toggle key name mismatch.
Press F10 when prompted.
Run as administrator.
"""
import interception
import threading

inter = interception.Interception()
inter.set_filter(inter.is_keyboard, interception.FilterKeyFlag.FILTER_KEY_ALL)

print(">>> Press F10 now <<<")

result = [None]

def capture():
    while True:
        device_idx = inter.await_input(10000)
        if device_idx is None:
            break
        device = inter._devices[device_idx]
        stroke = device.receive()
        device.send(stroke)
        if isinstance(stroke, interception.KeyStroke):
            # Find name by scancode
            matched = []
            for name, val in vars(interception._keycodes).items():
                if isinstance(val, int) and val == stroke.code:
                    matched.append(name)

            print(f"  code={stroke.code}  flags={stroke.flags}  matched names={matched}")
            result[0] = matched

            # Stop after seeing key up of whatever is pressed
            if stroke.flags & interception.KeyFlag.KEY_UP and matched:
                break

t = threading.Thread(target=capture, daemon=True)
t.start()
t.join(timeout=15)

if result[0]:
    names = result[0]
    print(f"\nKey name(s) returned by _code_to_name: {names}")
    print(f"settings.json has toggle_key = 'f10'")
    if "f10" in names:
        print("Match OK — key name matches 'f10'")
    else:
        print(f"MISMATCH — need to store '{names[0]}' in settings, not 'f10'")
else:
    print("No key captured.")

inter.destroy()
