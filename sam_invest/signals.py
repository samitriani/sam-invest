"""Snapshot marche + signaux techniques par instrument - DETERMINISTE, sans LLM.

Remplace l'ancienne valorisation de portefeuille : ici on ne possede rien, on
SURVEILLE. Pour chaque instrument de la watchlist, on calcule par du code :
  - cours, variation seance, drawdown depuis le plus-haut 52s,
  - indicateurs (SMA50, SMA200, RSI14),
  - signaux derives : tendance, etat RSI, position dans le range 52 semaines.
Tout chiffre vient du code, jamais d'un LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import db
from .config import AppConfig, Instrument
from .indicators import compute_indicators


@dataclass
class Snapshot:
    instrument: Instrument
    last_price: float | None
    change_pct: float | None          # variation seance
    drawdown_pct: float | None        # depuis plus-haut 52s
    high_52w: float | None
    low_52w: float | None
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    tendance: str                     # "haussiere" | "baissiere" | "neutre" | "n/d"
    rsi_etat: str                     # "survendu" | "suracheté" | "neutre" | "n/d"
    position_52w_pct: float | None    # 0 = plus-bas 52s, 100 = plus-haut 52s


def _tendance(last: float | None, sma50: float | None, sma200: float | None) -> str:
    if sma50 is None or sma200 is None:
        return "n/d"
    if sma50 > sma200:
        return "haussiere"
    if sma50 < sma200:
        return "baissiere"
    return "neutre"


def _rsi_etat(rsi: float | None, survente: float, surachat: float) -> str:
    if rsi is None:
        return "n/d"
    if rsi < survente:
        return "survendu"
    if rsi > surachat:
        return "suracheté"
    return "neutre"


def _position_52w(last: float | None, low: float | None, high: float | None) -> float | None:
    if None in (last, low, high) or high == low:
        return None
    return (last - low) / (high - low) * 100.0


def construire_snapshots(config: AppConfig) -> list[Snapshot]:
    cfg = config.signaux_techniques
    survente = float(cfg.get("rsi_survente", 30))
    surachat = float(cfg.get("rsi_surachat", 70))

    snaps: list[Snapshot] = []
    for inst in config.watchlist:
        q = db.get_quote(inst.ticker)
        ind = compute_indicators(db.get_price_history(inst.ticker))

        last = (q.get("last_price") if q else None) or ind.get("last_close")
        change = q.get("change_pct") if q else None
        drawdown = q.get("drawdown_pct") if q else None
        high = (q.get("high_52w") if q else None) or ind.get("high_52w")
        low = (q.get("low_52w") if q else None) or ind.get("low_52w")
        sma50, sma200, rsi = ind.get("sma_50"), ind.get("sma_200"), ind.get("rsi_14")

        snaps.append(Snapshot(
            instrument=inst,
            last_price=last,
            change_pct=change,
            drawdown_pct=drawdown,
            high_52w=high,
            low_52w=low,
            sma_50=sma50,
            sma_200=sma200,
            rsi_14=rsi,
            tendance=_tendance(last, sma50, sma200),
            rsi_etat=_rsi_etat(rsi, survente, surachat),
            position_52w_pct=_position_52w(last, low, high),
        ))
    return snaps
