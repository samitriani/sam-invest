"""Journal de debug fichier - simple et lisible.

Ecrit dans data/sam_invest.log. Sert au diagnostic (ex: pourquoi le briefing
n'apparait pas). A lire avec un editeur de texte ou via l'outil.
"""

from __future__ import annotations

import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "sam_invest.log"

_logger: logging.Logger | None = None


def _get() -> logging.Logger:
    global _logger
    if _logger is None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        lg = logging.getLogger("sam_invest")
        lg.setLevel(logging.DEBUG)
        if not lg.handlers:
            h = logging.FileHandler(LOG_PATH, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
            lg.addHandler(h)
            lg.propagate = False
        _logger = lg
    return _logger


def log(msg: str, level: str = "info") -> None:
    getattr(_get(), level, _get().info)(msg)
