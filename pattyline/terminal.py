"""Small reusable terminal progress indicators."""
import sys
import threading
import time


class Spinner:
    """Animate a -/\\| spinner while a blocking operation runs."""

    FRAMES = "-/\\|"

    def __init__(self, message, stream=None, interval=0.12):
        self.message = message
        self.stream = stream or sys.stdout
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if not self.stream.isatty():
            return self
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        if self._thread:
            self._stop.set()
            self._thread.join()
            self.stream.write("\r" + " " * (len(self.message) + 2) + "\r")
            self.stream.flush()

    def _animate(self):
        index = 0
        while not self._stop.is_set():
            self.stream.write(f"\r{self.FRAMES[index % len(self.FRAMES)]} {self.message}")
            self.stream.flush()
            index += 1
            self._stop.wait(self.interval)
