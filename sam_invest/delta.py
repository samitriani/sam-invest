"""Briefing 'delta' - DETERMINISTE, sans LLM.

Compare l'etat courant a un instantane de reference (la derniere fois que
l'utilisateur a clique 'Marquer comme vu'). Respecte la contrainte 100% manuel /
sans cron : la reference est stockee en base et le delta se calcule a l'ouverture.

Detecte : nouveaux flags, flags resolus, variations de cours notables,
nouvelles news, changements de revisions d'estimations.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .config import AppConfig


def etat_courant(config: AppConfig, data: dict) -> dict:
    """Capture l'etat courant (serialisable) a partir du dict de briefing."""
    instruments = {i["ticker"]: i.get("last_price") for i in data.get("instruments", [])}
    flags = [
        {"key": f"{f['regle']}|{f['ticker']}", "regle": f["regle"],
         "severite": f["severite"], "message": f["message"]}
        for f in data.get("flags", [])
    ]
    news_ids = {}
    for inst in config.watchlist:
        ids = [n["id"] for n in db.get_news(inst.ticker)]
        if ids:
            news_ids[inst.ticker] = ids
    revisions = {e["ticker"]: e.get("revisions_nettes_30j") for e in data.get("evenements", [])}
    earnings = {e["ticker"]: e.get("resultats_le") for e in data.get("evenements", [])}
    return {
        "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "instruments": instruments,
        "flags": flags,
        "news_ids": news_ids,
        "revisions": revisions,
        "earnings": earnings,
    }


def calculer_delta(prev: dict | None, cur: dict, seuil_var_pct: float = 3.0) -> dict:
    """Compare l'etat de reference (prev) a l'etat courant (cur)."""
    if not prev:
        return {"premiere_visite": True}

    # --- Flags apparus / resolus (identite = regle|ticker) ---
    prev_keys = {f["key"] for f in prev.get("flags", [])}
    cur_flags = cur.get("flags", [])
    cur_keys = {f["key"] for f in cur_flags}
    nouveaux_flags = [f for f in cur_flags if f["key"] not in prev_keys]
    flags_resolus = [f for f in prev.get("flags", []) if f["key"] not in cur_keys]

    # --- Variations de cours notables depuis la reference ---
    variations = []
    pinst = prev.get("instruments", {})
    for t, now in cur.get("instruments", {}).items():
        old = pinst.get(t)
        if now is None or not old:
            continue
        var = (now / old - 1.0) * 100.0
        if abs(var) >= seuil_var_pct:
            variations.append({"ticker": t, "avant": old, "maintenant": now, "var_pct": var})
    variations.sort(key=lambda x: abs(x["var_pct"]), reverse=True)

    # --- Nouvelles news (ids absents de la reference) ---
    nouvelles_news = {}
    pnews = prev.get("news_ids", {})
    for t, ids in cur.get("news_ids", {}).items():
        prev_ids = set(pnews.get(t, []))
        new_ids = [i for i in ids if i not in prev_ids]
        if new_ids:
            titres = {n["id"]: n["headline"] for n in db.get_news(t)}
            nouvelles_news[t] = [titres.get(i, "") for i in new_ids if titres.get(i)]

    # --- Changements de revisions d'estimations ---
    chg_rev = []
    prev_rev = prev.get("revisions", {})
    for t, now in cur.get("revisions", {}).items():
        old = prev_rev.get(t)
        if now is None or old is None or now == old:
            continue
        chg_rev.append({"ticker": t, "avant": old, "maintenant": now})

    return {
        "premiere_visite": False,
        "depuis": prev.get("asof"),
        "nouveaux_flags": nouveaux_flags,
        "flags_resolus": flags_resolus,
        "variations_prix": variations,
        "nouvelles_news": nouvelles_news,
        "changements_revisions": chg_rev,
    }


def est_vide(d: dict) -> bool:
    """True si le delta ne contient aucun changement a signaler."""
    if d.get("premiere_visite"):
        return False  # on affiche un message d'init, pas 'rien'
    return not any((d.get("nouveaux_flags"), d.get("flags_resolus"),
                    d.get("variations_prix"), d.get("nouvelles_news"),
                    d.get("changements_revisions")))
