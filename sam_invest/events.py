"""Vue derivee des evenements & estimations - DETERMINISTE, sans LLM.

Lit la table events_estimates et calcule par du code :
  - jours avant les prochains resultats / l'ex-dividende,
  - revisions nettes d'EPS (hausses - baisses) sur 7 et 30 jours,
  - potentiel vs objectif de cours moyen.
S'applique aux ACTIONS (les ETF n'ont pas ces donnees).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from . import db
from .config import AppConfig, Instrument


@dataclass
class EventView:
    instrument: Instrument
    earnings_date: str | None
    jours_avant_resultats: int | None
    exdiv_date: str | None
    jours_avant_exdiv: int | None
    rev_net_30: float | None        # hausses - baisses (30j)
    rev_up_30: float | None
    rev_down_30: float | None
    rev_period: str | None
    pt_mean: float | None
    pt_current: float | None
    potentiel_pct: float | None     # (pt_mean / cours - 1) * 100


def _jours_avant(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None


def construire_evenements(config: AppConfig) -> list[EventView]:
    """Vue par action (les ETF sont exclus : pas de resultats/estimations)."""
    vues: list[EventView] = []
    for inst in config.actions():
        e = db.get_events_estimates(inst.ticker)
        if not e:
            vues.append(EventView(inst, None, None, None, None, None, None, None, None, None, None, None))
            continue
        up30, down30 = e.get("eps_rev_up_30"), e.get("eps_rev_down_30")
        net30 = (up30 - down30) if (up30 is not None and down30 is not None) else None
        pt_mean, pt_cur = e.get("pt_mean"), e.get("pt_current")
        potentiel = ((pt_mean / pt_cur - 1.0) * 100.0) if (pt_mean and pt_cur) else None
        vues.append(EventView(
            instrument=inst,
            earnings_date=e.get("earnings_date"),
            jours_avant_resultats=_jours_avant(e.get("earnings_date")),
            exdiv_date=e.get("exdiv_date"),
            jours_avant_exdiv=_jours_avant(e.get("exdiv_date")),
            rev_net_30=net30,
            rev_up_30=up30,
            rev_down_30=down30,
            rev_period=e.get("eps_rev_period"),
            pt_mean=pt_mean,
            pt_current=pt_cur,
            potentiel_pct=potentiel,
        ))
    return vues
