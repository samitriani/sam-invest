"""Sam_Invest - interface Streamlit (un seul processus, 100% manuel).

Outil de WATCHLIST : on surveille des instruments (on ne possede rien ici).
Aucune planification, aucun cron : tout se declenche par les boutons.

UX en 3 onglets, chacun avec son propre bouton de mise a jour, plus un bouton
de mise a jour globale en haut a droite. Objectif : maitriser la consommation
d'API Claude.
  - Onglet Donnees  : prix + fondamentaux. AUCUN appel Claude.
  - Onglet News     : recup news + classement (Claude Haiku).
  - Onglet Briefing : flags (deterministes, gratuits) + synthese (Claude Sonnet,
                      uniquement quand on clique).

Separation stricte : chiffres/signaux = code deterministe ; texte = Claude (llm.py).
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from sam_invest import db, delta, llm, signals
from sam_invest.briefing import construire_briefing, indicateurs_ligne
from sam_invest.data_sources import search_instruments
from sam_invest.events import construire_evenements
from sam_invest.logs import log
from sam_invest.config import CONFIG_PATH, load_config, save_watchlist
from sam_invest.update import update_donnees, update_global, update_news

st.set_page_config(page_title="Sam_Invest", page_icon="📊", layout="wide")

config = load_config()
db.init_db()

# Etat persistant entre reruns (synthese generee a la demande).
st.session_state.setdefault("synth_global", None)
st.session_state.setdefault("synth_instruments", {})  # {ticker: {"fruit", "briefing"}}
st.session_state.setdefault("synthese_asof", None)
st.session_state.setdefault("search_results", None)

# Code couleur des recommandations (verdict "fruit").
FRUIT_LABEL = {"concombre": ("🥒", "Acheter"), "orange": ("🍊", "Maintenir"),
               "tomate": ("🍅", "Vendre")}


# ==========================================================================
# Helpers
# ==========================================================================
def run_update(fn, label: str) -> dict:
    """Lance une fonction de mise a jour avec barre de progression."""
    bar = st.progress(0.0, text=f"{label}...")

    def cb(frac: float, msg: str) -> None:
        bar.progress(min(max(frac, 0.0), 1.0), text=msg)

    with st.spinner(f"{label} (yfinance -> Finnhub -> FMP)..."):
        cr = fn(config, cb)
    bar.empty()
    return cr


def afficher_compte_rendu(cr: dict) -> None:
    if cr.get("status") == "ok":
        st.success(f"Termine. {cr.get('resume','')}")
    else:
        st.error(cr.get("resume", "Rien a faire (watchlist vide)."))
    with st.expander("Detail par instrument"):
        for d in cr.get("details", []):
            st.text(d)


def fmt_dt(valeur) -> str:
    """Formate une date ISO (UTC) ou un timestamp en 'JJ/MM/AAAA HH:MM' heure locale."""
    try:
        if isinstance(valeur, (int, float)):
            dt = datetime.fromtimestamp(valeur)
        else:
            dt = datetime.fromisoformat(str(valeur)).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(valeur)


def caption_derniere_maj(kind: str, libelle: str) -> None:
    m = db.last_update(kind)
    if m:
        st.caption(f"🕒 Derniere mise a jour {libelle} : **{fmt_dt(m['asof'])}** — {m['detail']}")
    else:
        st.caption(f"🕒 Aucune mise a jour {libelle} effectuee.")


def _fmt(x, dec=2):
    return f"{x:.{dec}f}" if isinstance(x, (int, float)) else "n/d"


def _money(x):
    """Grands montants -> T / Md / M (devise affichee a part)."""
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


def _pct_frac(x, dec=1):
    """x est une fraction (0.05) -> '5.0%'."""
    return f"{x * 100:.{dec}f}%" if isinstance(x, (int, float)) else "n/d"


def _pct_raw(x, dec=1):
    """x est deja un pourcentage (0.18) -> '0.18%'."""
    return f"{x:.{dec}f}%" if isinstance(x, (int, float)) else "n/d"


def _ratio(x, dec=2):
    return f"{x:.{dec}f}" if isinstance(x, (int, float)) else "n/d"


def afficher_fondamentaux(ticker: str) -> None:
    """Rend la sous-partie 'Fondamentaux' selon le type (action/ETF)."""
    prof = db.get_profile(ticker)
    if not prof or not prof.get("payload"):
        st.caption("Fondamentaux non recuperes. Lance une mise a jour des donnees.")
        return
    try:
        p = json.loads(prof["payload"])
    except Exception:
        st.caption("Fondamentaux illisibles.")
        return
    st.caption(f"Source : {prof.get('source')} · maj {fmt_dt(prof.get('asof'))}")

    if prof.get("type") == "action":
        r1 = st.columns(4)
        r1[0].metric("Capitalisation", _money(p.get("marketCap")))
        r1[1].metric("Secteur", p.get("sector") or "n/d")
        r1[2].metric("PER (trailing)", _ratio(p.get("trailingPE")))
        r1[3].metric("PER (forward)", _ratio(p.get("forwardPE")))
        r2 = st.columns(4)
        r2[0].metric("Price / Book", _ratio(p.get("priceToBook")))
        r2[1].metric("Marge nette", _pct_frac(p.get("profitMargins")))
        r2[2].metric("ROE", _pct_frac(p.get("returnOnEquity")))
        r2[3].metric("Rendement div.", _pct_raw(p.get("dividendYield"), 2))  # deja en %
        r3 = st.columns(4)
        r3[0].metric("Croissance CA", _pct_frac(p.get("revenueGrowth")))
        r3[1].metric("Croissance BPA", _pct_frac(p.get("earningsGrowth")))
        r3[2].metric("Dette / capitaux", _ratio(p.get("debtToEquity")))
        r3[3].metric("Current ratio", _ratio(p.get("currentRatio")))
        r4 = st.columns(4)
        r4[0].metric("Free cash flow", _money(p.get("freeCashflow")))
        r4[1].metric("Objectif moyen", _ratio(p.get("targetMeanPrice")))
        tgt, cur = p.get("targetMeanPrice"), p.get("currentPrice")
        if isinstance(tgt, (int, float)) and isinstance(cur, (int, float)) and cur:
            r4[2].metric("Potentiel", f"{(tgt / cur - 1) * 100:+.1f}%")
        else:
            r4[2].metric("Potentiel", "n/d")
    else:  # ETF
        r1 = st.columns(4)
        r1[0].metric("Categorie", p.get("category") or "n/d")
        r1[1].metric("Encours", _money(p.get("totalAssets")))
        r1[2].metric("Frais (TER)", _pct_raw(p.get("expenseRatio"), 2))
        r1[3].metric("Rendement", _pct_frac(p.get("yield")))
        st.metric("Perf YTD", _pct_raw(p.get("ytdReturn"), 1))
        top = p.get("top_holdings") or []
        if top:
            st.markdown("**Principales lignes :**")
            st.dataframe(
                pd.DataFrame([
                    {"Ticker": h.get("symbol"), "Nom": h.get("name"),
                     "Poids %": (h.get("pct") * 100 if isinstance(h.get("pct"), (int, float)) else None)}
                    for h in top
                ]),
                use_container_width=True, hide_index=True,
                column_config={"Poids %": st.column_config.NumberColumn(format="%.1f")},
            )


# ==========================================================================
# En-tete + bouton de mise a jour GLOBALE (haut a droite)
# ==========================================================================
head_l, head_r = st.columns([4, 1])
with head_l:
    st.title("📊 Sam_Invest — watchlist & signaux")
with head_r:
    st.write("")  # petit espaceur vertical pour aligner le bouton
    btn_global = st.button(
        "🔄 Tout mettre a jour", use_container_width=True, disabled=not config.watchlist,
        help="Donnees + News. Ne genere PAS la synthese Sonnet (cout maitrise).",
    )

st.caption(
    "Watchlist personnelle (Tech & emergents). Chiffres et signaux calcules par du "
    "code (deterministe) ; Claude resume/explique seulement, sans verdict acheter/vendre. "
    "**La decision finale reste humaine.**"
)

if config.warnings:
    with st.expander("⚠️ Avertissements de configuration", expanded=not config.watchlist):
        for w in config.warnings:
            st.warning(w)

# La mise a jour globale s'execute avant les onglets pour que les tableaux soient frais.
if btn_global:
    afficher_compte_rendu(run_update(update_global, "Mise a jour globale"))


# ==========================================================================
# Onglets
# ==========================================================================
tab_donnees, tab_news, tab_briefing, tab_edit = st.tabs(
    ["📈 Donnees", "📰 News", "🧠 Briefing", "✏️ Watchlist"]
)


# --------------------------------------------------------------------------
# ONGLET DONNEES : prix + fondamentaux + signaux (aucun appel Claude)
# --------------------------------------------------------------------------
with tab_donnees:
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("**Donnees de marche** — prix + fondamentaux. _Aucun appel Claude._")
    with c2:
        btn_data = st.button("🔄 Mettre a jour les donnees", use_container_width=True,
                             disabled=not config.watchlist)
    caption_derniere_maj("donnees", "donnees")
    if btn_data:
        afficher_compte_rendu(run_update(update_donnees, "Mise a jour des donnees"))

    st.subheader("Watchlist & signaux")
    snaps = signals.construire_snapshots(config)

    def _row(s) -> dict:
        return {
            "Ticker": s.instrument.ticker,
            "Nom": s.instrument.nom,
            "Theme": s.instrument.theme,
            "Cours": s.last_price,
            "Seance %": s.change_pct,
            "Drawdown 52s %": s.drawdown_pct,
            "Position 52s %": s.position_52w_pct,
            "RSI 14": s.rsi_14,
            "Etat RSI": s.rsi_etat,
            "Tendance": s.tendance,
        }

    _num_cfg = {
        "Cours": st.column_config.NumberColumn(format="%.2f"),
        "Seance %": st.column_config.NumberColumn(format="%.1f"),
        "Drawdown 52s %": st.column_config.NumberColumn(format="%.1f"),
        "Position 52s %": st.column_config.NumberColumn(format="%.0f"),
        "RSI 14": st.column_config.NumberColumn(format="%.0f"),
    }

    if snaps:
        for type_label, type_key in (("Actions", "action"), ("ETF", "etf")):
            sous = [s for s in snaps if s.instrument.type.lower() == type_key]
            if not sous:
                continue
            st.markdown(f"**{type_label}** ({len(sous)})")
            df = pd.DataFrame([_row(s) for s in sous])
            st.dataframe(df, use_container_width=True, hide_index=True, column_config=_num_cfg)
        st.caption("Position 52s % : 0 = plus-bas 52s, 100 = plus-haut. "
                   "Tendance : SMA50 vs SMA200. Colonnes vides = lance une mise a jour des donnees.")
    else:
        st.warning("Watchlist vide : remplis config.yaml.")

    # --- A venir (resultats / ex-dividende) + Estimations (actions) ---
    vues = construire_evenements(config)
    if vues:
        st.subheader("📅 A venir & estimations (actions)")

        def _jours(j):
            if j is None:
                return "n/d"
            if j < 0:
                return "passe"
            return "auj." if j == 0 else ("demain" if j == 1 else f"{j} j")

        cal_rows, est_rows = [], []
        for v in vues:
            cal_rows.append({
                "Ticker": v.instrument.ticker,
                "Resultats": v.earnings_date or "n/d",
                "Dans": _jours(v.jours_avant_resultats),
                "Ex-dividende": v.exdiv_date or "n/d",
                "Dans ": _jours(v.jours_avant_exdiv),
            })
            est_rows.append({
                "Ticker": v.instrument.ticker,
                "Revisions 30j (net)": v.rev_net_30,
                "Hausses": v.rev_up_30,
                "Baisses": v.rev_down_30,
                "Obj. cours moyen": v.pt_mean,
                "Potentiel %": v.potentiel_pct,
            })
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**Calendrier**")
            st.dataframe(pd.DataFrame(cal_rows), use_container_width=True, hide_index=True)
        with cc2:
            st.markdown("**Estimations & revisions**")
            st.dataframe(
                pd.DataFrame(est_rows), use_container_width=True, hide_index=True,
                column_config={
                    "Revisions 30j (net)": st.column_config.NumberColumn(format="%.0f"),
                    "Hausses": st.column_config.NumberColumn(format="%.0f"),
                    "Baisses": st.column_config.NumberColumn(format="%.0f"),
                    "Obj. cours moyen": st.column_config.NumberColumn(format="%.2f"),
                    "Potentiel %": st.column_config.NumberColumn(format="%.1f"),
                },
            )
        st.caption("Revisions 30j (net) = analystes relevant l'EPS − ceux l'abaissant (negatif = "
                   "attentes en degradation). Potentiel % = objectif moyen vs cours. "
                   "Donnees actions uniquement ; lance une mise a jour des donnees pour les remplir.")

    # Donnees par instrument : cours + fondamentaux.
    st.subheader("Donnees par instrument")
    if config.watchlist:
        tickers = [i.ticker for i in config.watchlist]
        choix = st.selectbox(
            "Instrument a afficher", tickers,
            format_func=lambda t: next((f"{i.ticker} — {i.nom}" for i in config.watchlist if i.ticker == t), t),
        )

        # --- Sous-partie 1 : cours de l'instrument ---
        st.markdown("#### Cours de l'instrument")
        hist = db.get_price_history(choix)
        if hist:
            dfh = pd.DataFrame(hist)
            dfh["date"] = pd.to_datetime(dfh["date"])
            dfh = dfh.set_index("date")
            st.line_chart(dfh["close"], height=300)

            ind = indicateurs_ligne(choix)
            ci = st.columns(5)
            ci[0].metric("Dernier", _fmt(ind["last_close"]))
            ci[1].metric("SMA 50", _fmt(ind["sma_50"]))
            ci[2].metric("SMA 200", _fmt(ind["sma_200"]))
            ci[3].metric("RSI 14", _fmt(ind["rsi_14"], dec=0))
            ci[4].metric("Plus-haut 52s", _fmt(ind["high_52w"]))
        else:
            st.caption("Pas encore d'historique pour cet instrument. Lance une mise a jour des donnees.")

        # --- Sous-partie 2 : fondamentaux de l'instrument ---
        st.markdown("#### Fondamentaux de l'instrument")
        afficher_fondamentaux(choix)


# --------------------------------------------------------------------------
# ONGLET NEWS : recup news + classement Haiku
# --------------------------------------------------------------------------
with tab_news:
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("**News** — recuperation + classement. _Utilise Claude Haiku._")
    with c2:
        btn_news = st.button("🔄 Mettre a jour les news", use_container_width=True,
                             disabled=not config.watchlist)
    caption_derniere_maj("news", "news")
    if not config.secrets.anthropic_api_key:
        st.info("Sans cle Claude active, les news s'affichent en clair mais ne sont "
                "ni classees ni resumees.")
    if btn_news:
        afficher_compte_rendu(run_update(update_news, "Mise a jour des news"))

    st.subheader("News par instrument")
    import json as _json
    une_news = False
    for inst in config.watchlist:
        raw = db.get_news(inst.ticker)
        if not raw:
            continue
        une_news = True
        na = db.get_news_analysis(inst.ticker)
        analyses = {}
        if na and na.get("payload"):
            try:
                analyses = {a.get("headline", ""): a for a in _json.loads(na["payload"])}
            except Exception:
                analyses = {}
        with st.expander(f"{inst.ticker} — {inst.nom} ({len(raw)} news)"):
            for n in raw:
                head = n.get("headline", "")
                a = analyses.get(head)
                if a:
                    ton = a.get("tonalite", "neutre")
                    emoji = {"positif": "🟢", "negatif": "🔴"}.get(ton, "⚪")
                    st.markdown(f"{emoji} **[{a.get('categorie','autre')}]** {head}")
                else:
                    st.markdown(f"• {head}")
                meta = []
                if n.get("datetime"):
                    meta.append(n["datetime"][:10])
                if n.get("source"):
                    meta.append(n["source"])
                meta_txt = " · ".join(meta)
                if n.get("url"):
                    st.caption(f"{meta_txt} — [lien]({n['url']})" if meta_txt else f"[lien]({n['url']})")
                elif meta_txt:
                    st.caption(meta_txt)
                if a and a.get("resume"):
                    st.caption(a["resume"])
    if not une_news:
        st.caption("Aucune news en base. Clique sur « Mettre a jour les news ». Si yfinance "
                   "ne renvoie rien, ajoute une cle FINNHUB_API_KEY dans .env (source plus fiable).")


# --------------------------------------------------------------------------
# ONGLET BRIEFING : flags (gratuits) + synthese Sonnet (a la demande)
# --------------------------------------------------------------------------
with tab_briefing:
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("**Briefing** — vue d'ensemble + une section par instrument, chacune avec "
                    "son interpretation et sa reco 🥒/🍊/🍅. "
                    "_Claude Sonnet, 1 seul appel pour tout._")
    with c2:
        btn_synthese = st.button("🧠 Generer le briefing", use_container_width=True,
                                 disabled=not config.watchlist or not config.secrets.anthropic_api_key)
    md, mn = db.last_update("donnees"), db.last_update("news")
    st.caption(
        f"🕒 Donnees : **{fmt_dt(md['asof']) if md else 'jamais'}** · "
        f"News : **{fmt_dt(mn['asof']) if mn else 'jamais'}** "
        "(le briefing se base sur ces donnees ; mets-les a jour avant de generer)."
    )
    st.caption("Code reco : 🥒 acheter · 🍊 maintenir · 🍅 vendre. "
               "⚠️ Heuristique generee par le LLM, **pas un conseil financier** — la decision reste tienne.")

    data = construire_briefing(config)  # deterministe : lit la base, n'appelle pas Claude

    # --- Delta : ce qui a change depuis la derniere visite (deterministe, gratuit) ---
    prev_snap = db.get_briefing_snapshot()
    prev_state = None
    if prev_snap and prev_snap.get("payload"):
        try:
            prev_state = json.loads(prev_snap["payload"])
        except Exception:
            prev_state = None
    cur_state = delta.etat_courant(config, data)
    seuil_var = float(config.briefing.get("delta_variation_notable_pct", 3))
    d = delta.calculer_delta(prev_state, cur_state, seuil_var)
    data["delta_depuis_derniere_visite"] = d  # transmis a la synthese Sonnet

    # Generation de la synthese (UN seul appel Sonnet -> global + par instrument).
    # Doit s'executer AVANT l'affichage pour apparaitre des ce rerun.
    if btn_synthese:
        log(f"[UI] clic 'Generer le briefing' (watchlist={len(config.watchlist)}, "
            f"instruments_briefing={len(data.get('instruments', []))})")
        with st.spinner("Redaction du briefing + reco (Claude Sonnet)..."):
            res = llm.synthese_et_reco(config.secrets, data)
        if res:
            st.session_state["synth_global"] = res.get("global")
            st.session_state["synth_instruments"] = res.get("instruments", {})
            m = db.last_update()
            st.session_state["synthese_asof"] = (m["asof"] if m else None)
            log(f"[UI] briefing stocke: global={len(res.get('global') or '')} chars, "
                f"instruments={len(res.get('instruments') or {})}")
        else:
            st.error("Briefing indisponible (cle/credit Claude ?). Voir data/sam_invest.log.")
            log("[UI] briefing = None -> rien a afficher", "error")

    # =====================================================================
    # SECTION GLOBAL (big picture)
    # =====================================================================
    st.markdown("## 🌍 Global")

    dc1, dc2 = st.columns([3, 1])
    with dc1:
        st.markdown("### 🆕 Depuis ta derniere visite")
    with dc2:
        btn_vu = st.button("✅ Marquer comme vu", use_container_width=True,
                           help="Fixe l'etat actuel comme reference : le delta repart de zero.")

    if d.get("premiere_visite"):
        st.info("Pas de point de comparaison (premiere visite). Clique « Marquer comme vu » "
                "pour fixer une reference ; les prochains briefings montreront les changements.")
    elif delta.est_vide(d):
        st.success(f"Aucun changement depuis le {fmt_dt(d['depuis'])}.")
    else:
        st.caption(f"Compare a l'etat du {fmt_dt(d['depuis'])}.")
        if d["nouveaux_flags"]:
            st.markdown("**Nouveaux flags :**")
            for f in d["nouveaux_flags"]:
                ic = "🔴" if f["severite"] == "alerte" else "🟡"
                st.markdown(f"- {ic} [{f['regle']}] {f['message']}")
        if d["flags_resolus"]:
            st.markdown("**Flags resorbes :**")
            for f in d["flags_resolus"]:
                st.markdown(f"- ✅ [{f['regle']}] {f['message']}")
        if d["variations_prix"]:
            st.markdown(f"**Variations de cours notables (≥ {seuil_var:.0f}%) :**")
            st.dataframe(
                pd.DataFrame([
                    {"Ticker": v["ticker"], "Avant": v["avant"],
                     "Maintenant": v["maintenant"], "Var %": v["var_pct"]}
                    for v in d["variations_prix"]
                ]),
                use_container_width=True, hide_index=True,
                column_config={
                    "Avant": st.column_config.NumberColumn(format="%.2f"),
                    "Maintenant": st.column_config.NumberColumn(format="%.2f"),
                    "Var %": st.column_config.NumberColumn(format="%.1f"),
                },
            )
        if d["changements_revisions"]:
            st.markdown("**Revisions d'estimations modifiees :**")
            for c in d["changements_revisions"]:
                st.markdown(f"- {c['ticker']} : net 30j {c['avant']:+.0f} → {c['maintenant']:+.0f}")
        if d["nouvelles_news"]:
            n_total = sum(len(v) for v in d["nouvelles_news"].values())
            with st.expander(f"Nouvelles news ({n_total})"):
                for t, titres in d["nouvelles_news"].items():
                    for h in titres:
                        st.markdown(f"- **{t}** — {h}")

    if btn_vu:
        db.save_briefing_snapshot(cur_state["asof"], json.dumps(cur_state, ensure_ascii=False))
        st.success("Etat marque comme vu. Le delta repart de zero.")
        st.rerun()

    # --- Vue d'ensemble : resume des flags + synthese globale (big picture) ---
    flags = data["flags"]
    flags_by: dict[str, list] = {}
    for f in flags:
        flags_by.setdefault(f["ticker"], []).append(f)
    n_al = sum(1 for f in flags if f["severite"] == "alerte")
    n_if = sum(1 for f in flags if f["severite"] == "info")
    st.markdown(f"### Vue d'ensemble — {n_al} alerte(s), {n_if} info(s)")
    if st.session_state.get("synth_global"):
        if st.session_state.get("synthese_asof"):
            st.caption(f"Synthese basee sur les donnees du {fmt_dt(st.session_state['synthese_asof'])}.")
        st.markdown(st.session_state["synth_global"])
    elif config.secrets.anthropic_api_key:
        st.caption("Clique sur « Generer le briefing » pour la vue d'ensemble, les "
                   "commentaires et les recos par instrument.")
    else:
        st.info("ANTHROPIC_API_KEY absente : briefing desactive, mais les flags et donnees "
                "par instrument ci-dessous restent valables.")

    # Recap des recommandations (verdict fruit) si un briefing a ete genere.
    _si = st.session_state.get("synth_instruments") or {}
    if _si:
        from collections import Counter
        cnt = Counter((_si.get(i.ticker) or {}).get("fruit", "") for i in config.watchlist)
        st.markdown(f"**Recommandations :** 🥒 {cnt.get('concombre', 0)} acheter · "
                    f"🍊 {cnt.get('orange', 0)} maintenir · 🍅 {cnt.get('tomate', 0)} vendre.")

    # =====================================================================
    # SECTION PAR INSTRUMENT
    # =====================================================================
    st.markdown("## 📋 Par instrument")
    snaps_by = {s.instrument.ticker: s for s in signals.construire_snapshots(config)}
    evby = {e["ticker"]: e for e in data.get("evenements", [])}
    synth_inst = st.session_state.get("synth_instruments") or {}
    log(f"[UI] rendu 'Par instrument': synth_inst={len(synth_inst)} cles, "
        f"watchlist={len(config.watchlist)}, "
        f"cles_communes={len([i for i in config.watchlist if i.ticker in synth_inst])}")

    if not synth_inst and config.secrets.anthropic_api_key:
        st.info("💡 Le briefing et la reco par instrument apparaissent apres "
                "« Generer le briefing » en haut de l'onglet. Les chiffres, flags et news "
                "ci-dessous sont deja disponibles sans appel Claude.")

    for inst in config.watchlist:
        t = inst.ticker
        fl = flags_by.get(t, [])
        has_alerte = any(f["severite"] == "alerte" for f in fl)
        entry = synth_inst.get(t) or {}
        f_emoji, f_label = FRUIT_LABEL.get(entry.get("fruit", ""), ("", ""))
        # L'icone du volet = la reco si dispo, sinon l'etat des flags.
        icon = f_emoji or ("🔴" if has_alerte else ("🟡" if fl else "·"))
        with st.expander(f"{icon} {t} — {inst.nom}"):
            # Reco + briefing EN TETE : coeur de la vue par instrument.
            if f_emoji:
                st.markdown(f"### {f_emoji} {f_label}")
            brief = entry.get("briefing")
            if brief:
                st.markdown(f"📝 {brief}")
            elif not entry:
                st.caption("📝 Briefing non genere — clique « Generer le briefing » "
                           "en haut de l'onglet.")
            st.markdown("**Chiffres cles**")
            s = snaps_by.get(t)
            if s:
                mc = st.columns(5)
                mc[0].metric("Cours", _fmt(s.last_price))
                mc[1].metric("Seance %", _fmt(s.change_pct, 1))
                mc[2].metric("RSI 14", _fmt(s.rsi_14, 0))
                mc[3].metric("Tendance", s.tendance)
                mc[4].metric("Drawdown 52s", _fmt(s.drawdown_pct, 1))
            # Evenements / estimations (actions)
            e = evby.get(t)
            if e and inst.type.lower() == "action":
                bits = []
                if e.get("jours_avant_resultats") is not None:
                    bits.append(f"Resultats dans {e['jours_avant_resultats']} j ({e.get('resultats_le')})")
                if e.get("revisions_nettes_30j") is not None:
                    bits.append(f"Revisions 30j net {e['revisions_nettes_30j']:+.0f}")
                if e.get("potentiel_pct") is not None:
                    bits.append(f"Potentiel {e['potentiel_pct']:+.0f}%")
                if bits:
                    st.caption(" · ".join(bits))
            # Flags de cet instrument
            for f in fl:
                if f["severite"] == "alerte":
                    st.error(f"🔴 [{f['regle']}] {f['message']}")
                else:
                    st.warning(f"🟡 [{f['regle']}] {f['message']}")
            if not fl:
                st.caption("Aucun flag.")
            # News recentes de l'instrument (top 4)
            raw = db.get_news(t)
            if raw:
                na = db.get_news_analysis(t)
                analyses = {}
                if na and na.get("payload"):
                    try:
                        analyses = {a.get("headline", ""): a for a in json.loads(na["payload"])}
                    except Exception:
                        analyses = {}
                st.markdown("**News recentes :**")
                for n in raw[:4]:
                    head = n.get("headline", "")
                    a = analyses.get(head)
                    if a:
                        emoji = {"positif": "🟢", "negatif": "🔴"}.get(a.get("tonalite", "neutre"), "⚪")
                        st.markdown(f"- {emoji} [{a.get('categorie','autre')}] {head}")
                    else:
                        st.markdown(f"- {head}")

# --------------------------------------------------------------------------
# ONGLET WATCHLIST : edition simple (ajouter / retirer / modifier des lignes)
# --------------------------------------------------------------------------
with tab_edit:
    st.markdown("**Edition de la watchlist** — modifie les cellules, ajoute une ligne "
                "(derniere ligne `+`) ou supprime (case a gauche + corbeille), puis enregistre.")
    try:
        mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
    except OSError:
        mtime = None
    st.caption(f"🕒 Watchlist enregistree le : **{fmt_dt(mtime) if mtime else 'jamais'}** "
               f"(fichier config.yaml).")

    # --- Recherche par nom (pas besoin de connaitre les tickers) ---
    st.markdown("#### 🔎 Rechercher un instrument")
    sc1, sc2 = st.columns([4, 1])
    with sc1:
        q = st.text_input("Nom ou ticker", key="wl_search_q", label_visibility="collapsed",
                          placeholder="ex : air liquide, alibaba, nasdaq, semiconducteurs...")
    with sc2:
        btn_search = st.button("Rechercher", use_container_width=True)
    if btn_search and q.strip():
        with st.spinner("Recherche (Yahoo)..."):
            st.session_state["search_results"] = search_instruments(q.strip())

    results = st.session_state.get("search_results")
    if results:
        labels = {f"{r['symbol']} — {r['nom']} ({r['bourse']}, {r['type']})": r for r in results}
        choix = st.multiselect("Resultats — coche ce que tu veux ajouter :", list(labels.keys()))
        if st.button("➕ Ajouter a la watchlist", disabled=not choix):
            existants = {i.ticker.upper() for i in config.watchlist}
            rows = [{"ticker": i.ticker, "nom": i.nom, "type": i.type, "theme": i.theme}
                    for i in config.watchlist]
            ajout = 0
            for lab in choix:
                r = labels[lab]
                if r["symbol"].upper() in existants:
                    continue
                rows.append({"ticker": r["symbol"], "nom": r["nom"], "type": r["type"], "theme": ""})
                existants.add(r["symbol"].upper())
                ajout += 1
            save_watchlist(rows)
            st.session_state["search_results"] = None
            st.success(f"{ajout} instrument(s) ajoute(s). Pense a renseigner le theme ci-dessous.")
            st.rerun()
    elif results == []:
        st.caption("Aucun resultat (action/ETF). Pour un ETF, cherche le nom du fonds ou "
                   "son ticker (ex : « invesco qqq », « QQQ ») — les indices sont exclus.")

    st.divider()
    st.markdown("##### Watchlist actuelle (edition directe)")

    df_wl = pd.DataFrame(
        [{"Ticker": i.ticker, "Nom": i.nom, "Type": i.type, "Theme": i.theme}
         for i in config.watchlist]
    )
    if df_wl.empty:
        df_wl = pd.DataFrame([{"Ticker": "", "Nom": "", "Type": "action", "Theme": ""}])

    edited = st.data_editor(
        df_wl,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "Ticker": st.column_config.TextColumn("Ticker", help="Symbole yfinance (ex: NVDA, ASML, CW8.PA)", required=True),
            "Nom": st.column_config.TextColumn("Nom"),
            "Type": st.column_config.SelectboxColumn("Type", options=["action", "ETF"], required=True),
            "Theme": st.column_config.TextColumn("Theme", help="Etiquette libre (ex: Tech, Emergents)"),
        },
        key="editeur_watchlist",
    )

    col_save, col_info = st.columns([1, 3])
    with col_save:
        btn_save = st.button("💾 Enregistrer", use_container_width=True)
    with col_info:
        st.caption("L'enregistrement reecrit uniquement la liste dans config.yaml ; "
                   "les seuils et regles sont preserves.")

    if btn_save:
        rows = edited.to_dict(orient="records")
        # Validation simple : ticker non vide + pas de doublon.
        vus, propres, ignores = set(), [], 0
        for r in rows:
            t = str(r.get("Ticker", "") or "").strip()
            if not t:
                ignores += 1
                continue
            if t.upper() in vus:
                ignores += 1
                continue
            vus.add(t.upper())
            propres.append({"ticker": t, "nom": r.get("Nom", ""),
                            "type": r.get("Type", "action"), "theme": r.get("Theme", "")})
        if not propres:
            st.error("Aucun instrument valide a enregistrer (ticker manquant ?).")
        else:
            n = save_watchlist(propres)
            msg = f"Watchlist enregistree : {n} instrument(s)."
            if ignores:
                msg += f" {ignores} ligne(s) ignoree(s) (ticker vide ou doublon)."
            st.success(msg)
            st.rerun()  # recharge config.yaml pour rafraichir toute l'app

# Note : suivi de portefeuille (PRU, allocation, DCA) retire -> outil de watchlist.
# Note : fonctionnalite email SMTP reportee (choix utilisateur).
