"""Structured logs (JSON on stderr) — same format as the Rust core logs.

    from vignemale import log

    log.info("order created", order_id=42, user="jacques")
    log.error("payment declined", reason="insufficient balance")

Each line is a JSON object: timestamp, level, target, message + your fields.
The minimum level follows `VIGNEMALE_LOG` (debug | info | warn | error; default info).
"""

import json
import os
import sys
from datetime import datetime, timezone

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def _min_level() -> int:
    raw = os.environ.get("VIGNEMALE_LOG", "info").lower()
    # VIGNEMALE_LOG accepts the Rust-side EnvFilter syntax ("vignemale=trace");
    # on the Python side we only honor the global level when it is recognized.
    return _LEVELS.get(raw, 20)


def _emit(level: str, message: str, **fields) -> None:
    if _LEVELS[level] < _min_level():
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(),
        "target": "vignemale::app",
        "message": message,
    }
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


def debug(message: str, **fields) -> None:
    _emit("debug", message, **fields)


def info(message: str, **fields) -> None:
    _emit("info", message, **fields)


def warn(message: str, **fields) -> None:
    _emit("warn", message, **fields)


def error(message: str, **fields) -> None:
    _emit("error", message, **fields)
