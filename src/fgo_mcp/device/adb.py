import subprocess

class ADBError(Exception):
    pass

def adb(*args, timeout: float = 60) -> str:
    result = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ADBError(result.stderr.strip() or f"adb {' '.join(*args)} failed")
    return result.stdout.strip()

def device_serial() -> str:
    """The device's hardware serial. It names the device's roster: one game account per device."""
    return adb("shell", "getprop", "ro.serialno")

def screenshot():
    """A full-resolution screenshot (slow; the engine uses the video stream, calibration uses this)."""
    import cv2
    import numpy as np

    png = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, timeout=60).stdout

    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)