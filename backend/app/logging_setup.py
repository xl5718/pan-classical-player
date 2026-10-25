import logging
import re

_PATTERNS = [
    re.compile(r"(?<![A-Za-z_])((?:access_token|refresh_token|client_secret|client_id|code|device_code)=)[^&\s\"']+"),
    re.compile(r"(\"(?:access_token|refresh_token|client_secret|device_code)\"\s*:\s*\")[^\"]+"),
]
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _secrets.add(value)


def redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub(r"\1***", text)
    for s in _secrets:
        text = text.replace(s, "***")
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", log_file=None) -> None:
    fmt = RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        from logging.handlers import RotatingFileHandler

        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
