"""Check the extracted py2exe GUI in a Windows runner desktop session."""

import os
from pathlib import Path
import subprocess
import sys
import time

import win32con
import win32gui
import win32process


def windows_for(pid):
    windows = []

    def record(handle, _):
        if win32process.GetWindowThreadProcessId(handle)[1] == pid:
            windows.append((handle, win32gui.GetWindowText(handle), win32gui.IsWindowVisible(handle)))
        return True

    win32gui.EnumWindows(record, None)
    return windows


def check_window(executable):
    process = subprocess.Popen([executable, "--language", "en"], cwd=executable.parent)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            windows = windows_for(process.pid)
            for handle, title, visible in windows:
                if visible and title in ("SyncPlayU configuration", "SyncPlayU configuration - SyncPlayU"):
                    print(f"Opened: {title}")
                    win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
                    if process.wait(timeout=10) == 0:
                        return
                    raise RuntimeError(f"Closing configuration exited with {process.returncode}")
            time.sleep(0.5)
        raise RuntimeError(f"Configuration did not open: exit={process.poll()}, windows={windows_for(process.pid)}")
    except Exception:
        log = Path(os.environ["APPDATA"]) / ".syncplay.log"
        if log.is_file():
            print(log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    check_window(Path(sys.argv[1]).resolve())
