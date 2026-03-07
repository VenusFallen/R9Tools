"""
Interception driver test script.
Moves the mouse slightly right then back to confirm driver is working.
Run as administrator.
"""
import time
import interception

print("Step 1: Locating mouse device...")
try:
    mouse = interception.get_mouse()
    if mouse is None:
        raise RuntimeError("No mouse device found (returned None)")
    print(f"         OK - device: {mouse}")
except Exception as e:
    print(f"         FAILED - {e}")
    exit(1)

print("Step 2: Sending mouse movement (+50px right)...")
try:
    interception.move_relative(50, 0)
    print("         OK")
except Exception as e:
    print(f"         FAILED - {e}")
    exit(1)

time.sleep(0.5)

print("Step 3: Sending mouse movement (-50px left, returning to origin)...")
try:
    interception.move_relative(-50, 0)
    print("         OK")
except Exception as e:
    print(f"         FAILED - {e}")
    exit(1)

print()
print("All tests passed. Interception driver is working correctly.")
