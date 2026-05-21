# screen_keeper.py
# Nudges the mouse 1px every `interval` seconds so the OS never triggers
# the screensaver or display sleep while the bot runs.
# Works on both Linux (xdotool) and Windows (pyautogui).
import platform
import subprocess
import threading
import time


def _jiggle(interval: int) -> None:
    is_windows = platform.system() == "Windows"
    while True:
        time.sleep(interval)
        try:
            if is_windows:
                import pyautogui
                x, y = pyautogui.position()
                pyautogui.moveTo(x + 1, y, duration=0.1)
                pyautogui.moveTo(x, y, duration=0.1)
            else:
                subprocess.run(["xdotool", "mousemove_relative", "--", "1", "0"], check=False)
                subprocess.run(["xdotool", "mousemove_relative", "--", "-1", "0"], check=False)
        except Exception:
            pass


def start(interval: int = 55) -> None:
    """Start keep-alive thread (daemon — auto-stops when main process exits)."""
    t = threading.Thread(target=_jiggle, args=(interval,), daemon=True)
    t.start()
    print(f"[ScreenKeeper] Mouse jiggler started (every {interval}s).", flush=True)