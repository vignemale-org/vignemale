"""Logs structurés (JSON sur stderr) — même format que les logs du core Rust.

    from vignemale import log

    log.info("commande créée", order_id=42, user="jacques")
    log.error("paiement refusé", reason="solde insuffisant")

Chaque ligne est un objet JSON : timestamp, level, target, message + tes champs.
Le niveau minimum suit `VIGNEMALE_LOG` (debug | info | warn | error ; défaut info).
"""

import json
import os
import sys
from datetime import datetime, timezone

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def _min_level() -> int:
    raw = os.environ.get("VIGNEMALE_LOG", "info").lower()
    # VIGNEMALE_LOG accepte la syntaxe EnvFilter côté Rust ("vignemale=trace") ;
    # côté Python on ne retient que le niveau global s'il est reconnu.
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
