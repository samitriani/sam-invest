"""Assemblage du briefing watchlist - DETERMINISTE pour les chiffres.

Construit un dict entierement chiffre par le code (snapshots + signaux + flags
+ news resumees). Ce dict est :
  - affiche tel quel dans l'UI ;
  - passe a Claude Sonnet (llm.synthese_briefing) qui ne fait que le reformuler.
"""

from __future__ import annotations

import json

from . import db, rules, signals
from .config import AppConfig
from .events import construire_evenements
from .indicators import compute_indicators


def construire_briefing(config: AppConfig) -> dict:
    snaps = signals.construire_snapshots(config)
    flags = rules.tous_les_flags(config, snaps)

    instruments = [
        {
            "ticker": s.instrument.ticker,
            "nom": s.instrument.nom,
            "type": s.instrument.type,
            "theme": s.instrument.theme,
            "last_price": s.last_price,
            "change_pct": s.change_pct,
            "drawdown_pct": s.drawdown_pct,
            "sma_50": s.sma_50,
            "sma_200": s.sma_200,
            "rsi_14": s.rsi_14,
            "tendance": s.tendance,
            "rsi_etat": s.rsi_etat,
            "position_52w_pct": s.position_52w_pct,
        }
        for s in snaps
    ]

    # Evenements & estimations (actions) pour la synthese.
    evenements = [
        {
            "ticker": v.instrument.ticker,
            "nom": v.instrument.nom,
            "resultats_le": v.earnings_date,
            "jours_avant_resultats": v.jours_avant_resultats,
            "exdiv_le": v.exdiv_date,
            "revisions_nettes_30j": v.rev_net_30,
            "revisions_hausses_30j": v.rev_up_30,
            "revisions_baisses_30j": v.rev_down_30,
            "objectif_cours_moyen": v.pt_mean,
            "potentiel_pct": v.potentiel_pct,
        }
        for v in construire_evenements(config)
    ]

    # News resumees (sortie Haiku) par ticker, pour la synthese.
    news_resumees = {}
    for inst in config.watchlist:
        na = db.get_news_analysis(inst.ticker)
        if na and na.get("payload"):
            try:
                news_resumees[inst.ticker] = json.loads(na["payload"])
            except Exception:
                pass

    return {
        "devise": config.devise,
        "derniere_maj": db.last_update(),
        "instruments": instruments,
        "evenements": evenements,
        "flags": [
            {"ticker": f.ticker, "regle": f.regle, "severite": f.severite, "message": f.message}
            for f in flags
        ],
        "news_resumees": news_resumees,
    }


def indicateurs_ligne(ticker: str) -> dict:
    """Indicateurs techniques deterministes pour un instrument (graphique/details)."""
    return compute_indicators(db.get_price_history(ticker))
