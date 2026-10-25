import sys
import time


class Progress:
    def __init__(self, label: str, total: int | None = None, stream=sys.stderr):
        self.label = label
        self.total = total
        self.done = 0
        self.extra = ""
        self.stream = stream
        self.tty = stream.isatty()
        self.t0 = time.monotonic()
        self._last = 0.0

    def update(self, n: int = 1, extra: str | None = None, force: bool = False) -> None:
        self.done += n
        if extra is not None:
            self.extra = extra
        now = time.monotonic()
        if force or now - self._last >= (0.2 if self.tty else 10):
            self._last = now
            self._render()

    def _render(self, end: str = "") -> None:
        el = time.monotonic() - self.t0
        if self.total:
            pct = self.done / self.total * 100
            eta = (el / self.done * (self.total - self.done)) if self.done else 0
            body = f"{self.label} {self.done}/{self.total} ({pct:.0f}%) 用时 {el:.0f}s 剩余约 {eta:.0f}s"
        else:
            body = f"{self.label} {self.done} 用时 {el:.0f}s"
        if self.extra:
            body += f" | {self.extra}"
        if self.tty:
            self.stream.write("\r\033[K" + body + end)
        else:
            self.stream.write(body + "\n")
        self.stream.flush()

    def close(self) -> None:
        self._render(end="\n" if self.tty else "")
