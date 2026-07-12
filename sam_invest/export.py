"""Export Markdown de TOUTES les donnees de l'app - pour analyse par Claude.

Assemble en un seul fichier .md, pensé pour etre recolle a Claude ensuite :
  - metadonnees (date d'export, devise, horodatage des dernieres MAJ) ;
  - une legende des codes maison (fruit, flags, RSI, 🚬) que Claude ne devine pas ;
  - onglet Donnees : tableau signaux (actions/ETF) + calendrier & estimations ;
  - onglet News : news brutes + classement Haiku (categorie/tonalite/traduction) ;
  - onglet Briefing : synthese globale + briefing 3 parties + reco par instrument ;
  - onglet Diagnostic : dernier diagnostic de la session (si realise).

Tout est DETERMINISTE cote chiffres (lu en base) ; les textes de synthese
proviennent de Claude et sont repris tels quels. Aucun appel LLM ici.
"""

from __future__ import annotations

import json
from datetime import datetime

from . import db
from .briefing import construire_briefing
from .config import AppConfig

FRUIT = {"concombre": ("🥒", "Acheter"), "orange": ("🍊", "Maintenir"),
         "tomate": ("🍅", "Vendre")}


# --------------------------------------------------------------------------
# Formatage
# --------------------------------------------------------------------------
def _fmt(x, dec=2) -> str:
    return f"{x:.{dec}f}" if isinstance(x, (int, float)) else "n/d"


def _money(x) -> str:
    if not isinstance(x, (int, float)):
        return "n/d"
    a = abs(x)
    if a >= 1e12:
        return f"{x / 1e12:.2f} T"
    if a >= 1e9:
        return f"{x / 1e9:.2f} Md"
    if a >= 1e6:
        return f"{x / 1e6:.2f} M"
    return f"{x:.0f}"


def _pct_frac(x, dec=1) -> str:
    return f"{x * 100:.{dec}f}%" if isinstance(x, (int, float)) else "n/d"


def _pct_raw(x, dec=1) -> str:
    return f"{x:.{dec}f}%" if isinstance(x, (int, float)) else "n/d"


def _fmt_dt(v) -> str:
    if not v:
        return "jamais"
    try:
        if isinstance(v, (int, float)):
            dt = datetime.fromtimestamp(v)
        else:
            dt = datetime.fromisoformat(str(v)).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(v)


def _cell(x) -> str:
    """Cellule de tableau : nombre arrondi, texte echappe, None -> n/d."""
    if x is None or x == "":
        return "n/d"
    if isinstance(x, float):
        x = f"{x:.2f}"
    return str(x).replace("|", "\\|").replace("\n", " ")


def _jours(j) -> str:
    if j is None:
        return "n/d"
    if j < 0:
        return "passe"
    return "auj." if j == 0 else ("demain" if j == 1 else f"{j} j")


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
def _entete(config: AppConfig) -> list[str]:
    md, mn = db.last_update("donnees"), db.last_update("news")
    return [
        "# Export Sam_Invest — watchlist & analyse",
        "",
        "> Fichier genere pour etre analyse par Claude. Les valeurs chiffrees "
        "(prix, ratios, signaux) sont calculees par du code deterministe ; les textes "
        "de synthese sont rediges par Claude et repris tels quels. "
        "**Ce document n'est pas un conseil financier ; la decision reste humaine.**",
        "",
        f"- **Date d'export** : {_fmt_dt(datetime.now())} (heure locale)",
        f"- **Devise d'affichage** : {config.devise}",
        f"- **Instruments suivis** : {len(config.watchlist)}",
        f"- **Derniere MAJ donnees** : {_fmt_dt(md['asof']) if md else 'jamais'}"
        + (f" — {md['detail']}" if md and md.get('detail') else ""),
        f"- **Derniere MAJ news** : {_fmt_dt(mn['asof']) if mn else 'jamais'}"
        + (f" — {mn['detail']}" if mn and mn.get('detail') else ""),
        "",
        "## Legende",
        "- **Recommandation (fruit)** : 🥒 concombre = acheter · 🍊 orange = maintenir · "
        "🍅 tomate = vendre.",
        "- **Flags** : 🔴 alerte · 🟡 info (regles deterministes : chute, technique, "
        "degradation, evenements, revisions).",
        "- **Signaux** : tendance = SMA50 vs SMA200 ; position 52s % (0 = plus-bas 52 "
        "semaines, 100 = plus-haut) ; RSI 14 (<30 survendu, >70 surachete).",
        "- **Revisions 30j (net)** = analystes relevant l'EPS − ceux l'abaissant "
        "(negatif = attentes en degradation). **Potentiel %** = objectif moyen vs cours.",
        "- **🚬** (section Diagnostic) = chiffre marque douteux (aberration ou effet de "
        "change).",
        "",
        "---",
        "",
    ]


def _section_signaux(instruments: list[dict]) -> list[str]:
    out = ["## 1. Vue d'ensemble — signaux (onglet Donnees)", ""]
    cols = ("Ticker", "Nom", "Theme", "Cours", "Seance %", "Drawdown 52s %",
            "Position 52s %", "RSI 14", "Etat RSI", "Tendance")
    for label, key in (("Actions", "action"), ("ETF", "etf")):
        sous = [i for i in instruments if (i.get("type") or "").lower() == key]
        if not sous:
            continue
        out.append(f"### {label} ({len(sous)})")
        out.append("")
        out.append("| " + " | ".join(cols) + " |")
        out.append("|" + "---|" * len(cols))
        for i in sous:
            out.append("| " + " | ".join(_cell(v) for v in (
                i.get("ticker"), i.get("nom"), i.get("theme"), i.get("last_price"),
                i.get("change_pct"), i.get("drawdown_pct"), i.get("position_52w_pct"),
                i.get("rsi_14"), i.get("rsi_etat"), i.get("tendance"),
            )) + " |")
        out.append("")
    return out


def _section_evenements(evenements: list[dict]) -> list[str]:
    actionnables = [e for e in evenements if any(
        e.get(k) is not None for k in ("jours_avant_resultats", "revisions_nettes_30j",
                                       "potentiel_pct", "exdiv_le"))]
    if not actionnables:
        return []
    out = ["## 2. A venir & estimations (actions)", ""]
    cols = ("Ticker", "Resultats", "Dans", "Ex-dividende", "Revisions 30j net",
            "Hausses", "Baisses", "Obj. cours moyen", "Potentiel %")
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "---|" * len(cols))
    for e in evenements:
        out.append("| " + " | ".join(_cell(v) for v in (
            e.get("ticker"), e.get("resultats_le"), _jours(e.get("jours_avant_resultats")),
            e.get("exdiv_le"), e.get("revisions_nettes_30j"), e.get("revisions_hausses_30j"),
            e.get("revisions_baisses_30j"), e.get("objectif_cours_moyen"),
            e.get("potentiel_pct"),
        )) + " |")
    out.append("")
    return out


def _section_briefing_global(synth_global: str | None, synth_instruments: dict) -> list[str]:
    out = ["## 3. Briefing global (Claude)", ""]
    if synth_global:
        out.append(synth_global)
    else:
        out.append("_Briefing non genere dans la session (bouton « Generer le briefing »)._")
    out.append("")
    if synth_instruments:
        from collections import Counter
        cnt = Counter((v or {}).get("fruit", "") for v in synth_instruments.values())
        out.append(f"**Recommandations :** 🥒 {cnt.get('concombre', 0)} acheter · "
                   f"🍊 {cnt.get('orange', 0)} maintenir · 🍅 {cnt.get('tomate', 0)} vendre.")
        out.append("")
    return out


def _fondamentaux_lignes(prof: dict) -> list[str]:
    try:
        p = json.loads(prof["payload"])
    except Exception:
        return ["- Fondamentaux illisibles."]
    src = f"source {prof.get('source')} · maj {_fmt_dt(prof.get('asof'))}"
    if prof.get("type") == "action":
        tgt, cur = p.get("targetMeanPrice"), p.get("currentPrice")
        pot = (f"{(tgt / cur - 1) * 100:+.1f}%"
               if (isinstance(tgt, (int, float)) and isinstance(cur, (int, float)) and cur)
               else "n/d")
        pairs = [
            ("Capitalisation", _money(p.get("marketCap"))),
            ("Secteur", p.get("sector") or "n/d"),
            ("PER (trailing)", _fmt(p.get("trailingPE"))),
            ("PER (forward)", _fmt(p.get("forwardPE"))),
            ("Price / Book", _fmt(p.get("priceToBook"))),
            ("Marge nette", _pct_frac(p.get("profitMargins"))),
            ("ROE", _pct_frac(p.get("returnOnEquity"))),
            ("Rendement div.", _pct_raw(p.get("dividendYield"), 2)),
            ("Croissance CA", _pct_frac(p.get("revenueGrowth"))),
            ("Croissance BPA", _pct_frac(p.get("earningsGrowth"))),
            ("Dette / capitaux", _fmt(p.get("debtToEquity"))),
            ("Current ratio", _fmt(p.get("currentRatio"))),
            ("Free cash flow", _money(p.get("freeCashflow"))),
            ("Objectif moyen", _fmt(p.get("targetMeanPrice"))),
            ("Potentiel", pot),
        ]
    else:  # ETF
        pairs = [
            ("Categorie", p.get("category") or "n/d"),
            ("Encours", _money(p.get("totalAssets"))),
            ("Frais (TER)", _pct_raw(p.get("expenseRatio"), 2)),
            ("Rendement", _pct_frac(p.get("yield"))),
            ("Perf YTD", _pct_raw(p.get("ytdReturn"), 1)),
        ]
    lignes = [f"_Fondamentaux ({src})_ :"]
    lignes += [f"- {k} : {v}" for k, v in pairs]
    if prof.get("type") != "action":
        top = p.get("top_holdings") or []
        if top:
            noms = ", ".join(
                f"{h.get('symbol')} ({h.get('pct') * 100:.1f}%)" if isinstance(h.get('pct'), (int, float))
                else str(h.get('symbol'))
                for h in top[:10])
            lignes.append(f"- Principales lignes : {noms}")
    return lignes


def _news_lignes(ticker: str) -> list[str]:
    raw = db.get_news(ticker)
    if not raw:
        return ["_Aucune news en base._"]
    na = db.get_news_analysis(ticker)
    analyses = {}
    if na and na.get("payload"):
        try:
            analyses = {a.get("headline", ""): a for a in json.loads(na["payload"])}
        except Exception:
            analyses = {}
    out = [f"_News recentes ({len(raw)})_ :"]
    for n in raw:
        a = analyses.get(n.get("headline", ""))
        titre = ((a.get("titre_fr") or "").strip() if a else "") or n.get("headline", "")
        meta = []
        if n.get("datetime"):
            meta.append(str(n["datetime"])[:10])
        if n.get("source"):
            meta.append(n["source"])
        meta_txt = " · ".join(meta)
        if a:
            emoji = {"positif": "🟢", "negatif": "🔴"}.get(a.get("tonalite", "neutre"), "⚪")
            entete = f"- {emoji} [{a.get('categorie', 'autre')}] **{titre}**"
        else:
            entete = f"- **{titre}**"
        if meta_txt:
            entete += f" ({meta_txt})"
        out.append(entete)
        resume = ((a.get("resume_fr") or "").strip() if a else "") or (n.get("summary") or "").strip()
        if resume:
            out.append(f"  - {resume if len(resume) <= 600 else resume[:600].rstrip() + '…'}")
        if n.get("url"):
            out.append(f"  - {n['url']}")
    return out


def _section_par_instrument(config: AppConfig, data: dict, synth_instruments: dict) -> list[str]:
    inst_by = {i["ticker"]: i for i in data.get("instruments", [])}
    ev_by = {e["ticker"]: e for e in data.get("evenements", [])}
    flags_by: dict[str, list] = {}
    for f in data.get("flags", []):
        flags_by.setdefault(f["ticker"], []).append(f)

    out = ["## 4. Detail par instrument", ""]
    for inst in config.watchlist:
        t = inst.ticker
        s = inst_by.get(t, {})
        out.append(f"### {t} — {inst.nom} ({inst.type} · {inst.theme or 'sans theme'})")
        out.append("")
        # Chiffres cles
        out.append(
            f"**Chiffres cles** : cours {_fmt(s.get('last_price'))} · "
            f"seance {_pct_raw(s.get('change_pct'))} · RSI {_fmt(s.get('rsi_14'), 0)} "
            f"({s.get('rsi_etat', 'n/d')}) · tendance {s.get('tendance', 'n/d')} · "
            f"drawdown 52s {_pct_raw(s.get('drawdown_pct'))} · "
            f"position 52s {_fmt(s.get('position_52w_pct'), 0)}."
        )
        out.append("")
        # Evenements (actions)
        e = ev_by.get(t)
        if e and inst.type.lower() == "action":
            bits = []
            if e.get("jours_avant_resultats") is not None:
                bits.append(f"resultats dans {e['jours_avant_resultats']} j "
                            f"({e.get('resultats_le')})")
            if e.get("revisions_nettes_30j") is not None:
                bits.append(f"revisions 30j net {e['revisions_nettes_30j']:+.0f}")
            if e.get("potentiel_pct") is not None:
                bits.append(f"potentiel {e['potentiel_pct']:+.0f}%")
            if bits:
                out.append("**Evenements / estimations** : " + " · ".join(bits) + ".")
                out.append("")
        # Fondamentaux
        prof = db.get_profile(t)
        if prof and prof.get("payload"):
            out += _fondamentaux_lignes(prof)
            out.append("")
        # Flags
        fl = flags_by.get(t, [])
        if fl:
            out.append("**Flags** :")
            for f in fl:
                ic = "🔴" if f["severite"] == "alerte" else "🟡"
                out.append(f"- {ic} [{f['regle']}] {f['message']}")
        else:
            out.append("**Flags** : aucun.")
        out.append("")
        # Briefing Claude (3 parties + reco)
        entry = synth_instruments.get(t) or {}
        if entry:
            f_emoji, f_label = FRUIT.get(entry.get("fruit", ""), ("", ""))
            out.append("**Briefing (Claude)** :")
            if entry.get("analyse_chiffres"):
                out.append(f"- 📊 Analyse des chiffres : {entry['analyse_chiffres']}")
            if entry.get("analyse_news"):
                out.append(f"- 📰 Analyse des news : {entry['analyse_news']}")
            if entry.get("conclusion"):
                out.append(f"- 🎯 Conclusion & arguments : {entry['conclusion']}")
            if f_emoji:
                out.append(f"- Recommandation : {f_emoji} {f_label}")
        else:
            out.append("**Briefing (Claude)** : non genere dans la session.")
        out.append("")
        # News
        out += _news_lignes(t)
        out.append("")
        out.append("---")
        out.append("")
    return out


def _section_diagnostic(diag_result: dict | None) -> list[str]:
    if not diag_result or not diag_result.get("diag"):
        return []
    diag = diag_result["diag"]
    conclusions = diag_result.get("conclusions", {})
    resume = diag_result.get("resume", "")
    out = ["## 5. Diagnostic financier (dernier de la session)", ""]
    entete = f"### {diag.get('nom')} ({diag.get('ticker')})"
    if diag.get("devise"):
        entete += f" · {diag['devise']}"
    out.append(entete)
    annee, dref = diag.get("annee"), diag.get("date_reference")
    exo = (f"Exercice {annee}" + (f" (cloture {dref})" if dref else "")) if annee else (dref or "date n/d")
    out.append(f"_Chiffres : {exo} (source yfinance). 🚬 = chiffre douteux._")
    if diag.get("note_fiabilite"):
        out.append(f"> ⚠️ {diag['note_fiabilite']}")
    out.append("")
    if resume:
        out.append("**Executive summary (Claude Opus) :**")
        out.append("")
        out.append(resume)
        out.append("")
    for etape in diag.get("etapes", []):
        out.append(f"#### {etape['titre']}")
        out.append("")
        out.append("| Indicateur | Valeur | Source |")
        out.append("|---|---|---|")
        for ligne in etape["lignes"]:
            valeur = f"🚬 {ligne['valeur']}" if ligne.get("doute") else ligne["valeur"]
            out.append(f"| {_cell(ligne['label'])} | {_cell(valeur)} | {_cell(ligne['source'])} |")
        out.append("")
        concl = conclusions.get(etape["id"])
        if concl:
            out.append(f"_Conclusion (Claude Opus)_ : {concl}")
            out.append("")
    return out


# --------------------------------------------------------------------------
# Point d'entree
# --------------------------------------------------------------------------
def construire_export_md(
    config: AppConfig,
    data: dict | None = None,
    synth_global: str | None = None,
    synth_instruments: dict | None = None,
    diag_result: dict | None = None,
) -> str:
    """Assemble tout l'etat de l'app en un document Markdown unique.

    `data` = sortie de construire_briefing (recalculee si None). `synth_global`,
    `synth_instruments` et `diag_result` proviennent de la session Streamlit
    (None si rien n'a ete genere).
    """
    if data is None:
        data = construire_briefing(config)
    synth_instruments = synth_instruments or {}

    lignes: list[str] = []
    lignes += _entete(config)
    lignes += _section_signaux(data.get("instruments", []))
    lignes += _section_evenements(data.get("evenements", []))
    lignes += _section_briefing_global(synth_global, synth_instruments)
    lignes += _section_par_instrument(config, data, synth_instruments)
    lignes += _section_diagnostic(diag_result)
    return "\n".join(lignes).rstrip() + "\n"
