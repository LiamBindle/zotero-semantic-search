import shutil
import signal
import subprocess
import threading
import time
import urllib.request
import webbrowser

import uvicorn

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"


def _serve():
    uvicorn.run("main:app", host="127.0.0.1", port=PORT, log_level="warning")


def _wait_for_server(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def _open_window():
    # Chromium/Chrome --app mode: no browser chrome, looks like a desktop window
    for cmd in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(cmd):
            subprocess.Popen([cmd, f"--app={URL}", "--disable-extensions"])
            return
    # Firefox kiosk mode as next best option
    if shutil.which("firefox"):
        subprocess.Popen(["firefox", "--new-window", URL])
        return
    # Generic fallback
    webbrowser.open_new(URL)


def main():
    threading.Thread(target=_serve, daemon=True).start()
    if _wait_for_server():
        _open_window()
    else:
        print(f"Server did not start in time. Open {URL} manually.")

    # Keep process alive until Ctrl+C
    signal.signal(signal.SIGINT, lambda *_: None)
    signal.pause()


if __name__ == "__main__":
    main()
