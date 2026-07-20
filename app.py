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
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from sam_invest import db, llm, signals
from sam_invest.briefing import construire_briefing, indicateurs_ligne
from sam_invest.export import construire_export_md
from sam_invest.data_sources import search_instruments
from sam_invest.diagnostic import construire_diagnostic
from sam_invest import glossaire
from sam_invest.events import construire_evenements
from sam_invest.idees import generer_candidats
from sam_invest.logs import log
from sam_invest.config import CONFIG_PATH, load_config, save_watchlist
from sam_invest.update import (update_donnees, update_donnees_instrument,
                               update_global, update_news)

st.set_page_config(page_title="Sam_Invest", page_icon="📊", layout="wide")

# --- Pont secrets Streamlit Cloud -> variables d'environnement ---
# En local, .env est charge par python-dotenv (config.load_secrets). Sur Streamlit
# Community Cloud il n'y a pas de .env (fichier gitignore, jamais deploye) : les
# secrets sont saisis dans le dashboard et exposes via st.secrets. Ce pont permet a
# load_secrets() de fonctionner a l'identique dans les deux environnements (un seul
# code path : os.getenv). Sans fichier secrets.toml (dev local), st.secrets est vide
# ou leve une exception selon la version : on ignore silencieusement dans ce cas.
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

# Touche de police minimale : chiffres/valeurs en monospace (IBM Plex Mono).
# Volontairement reduit a 2 selecteurs, sans transition ni mise en page (perf safe).
st.markdown(
    "<style>"
    "@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&display=swap');"
    "code, [data-testid=\"stMetricValue\"]{font-family:'IBM Plex Mono','Courier New',monospace;}"
    "</style>",
    unsafe_allow_html=True,
)

config = load_config()
db.init_db()

# Etat persistant entre reruns (synthese generee a la demande).
st.session_state.setdefault("synth_global", None)
st.session_state.setdefault("synth_instruments", {})  # {ticker: {fruit, analyse_chiffres, analyse_news, conclusion}}
st.session_state.setdefault("synthese_asof", None)
st.session_state.setdefault("search_results", None)
st.session_state.setdefault("idees_candidats", None)

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


# Un briefing ne doit se baser que sur des donnees/news recentes.
FRAICHEUR_MAX_H = 2


def fraicheur(kind: str) -> tuple[bool, str | None]:
    """(frais, asof) pour un type de maj : frais = moins de FRAICHEUR_MAX_H heures."""
    m = db.last_update(kind)
    if not m or not m.get("asof"):
        return False, None
    try:
        dt = datetime.fromisoformat(str(m["asof"]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False, m.get("asof")
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return age_h <= FRAICHEUR_MAX_H, m["asof"]


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


def mh(container, label, value):
    """st.metric avec tooltip (help) tire du glossaire (None si terme inconnu)."""
    container.metric(label, value, help=glossaire.definition(label))


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
        mh(r1[0], "Capitalisation", _money(p.get("marketCap")))
        mh(r1[1], "Secteur", p.get("sector") or "n/d")
        mh(r1[2], "PER (trailing)", _ratio(p.get("trailingPE")))
        mh(r1[3], "PER (forward)", _ratio(p.get("forwardPE")))
        r2 = st.columns(4)
        mh(r2[0], "Price / Book", _ratio(p.get("priceToBook")))
        mh(r2[1], "Marge nette", _pct_frac(p.get("profitMargins")))
        mh(r2[2], "ROE", _pct_frac(p.get("returnOnEquity")))
        mh(r2[3], "Rendement div.", _pct_raw(p.get("dividendYield"), 2))
        r3 = st.columns(4)
        mh(r3[0], "Croissance CA", _pct_frac(p.get("revenueGrowth")))
        mh(r3[1], "Croissance BPA", _pct_frac(p.get("earningsGrowth")))
        mh(r3[2], "Dette / capitaux", _ratio(p.get("debtToEquity")))
        mh(r3[3], "Current ratio", _ratio(p.get("currentRatio")))
        r4 = st.columns(4)
        mh(r4[0], "Free cash flow", _money(p.get("freeCashflow")))
        mh(r4[1], "Objectif moyen", _ratio(p.get("targetMeanPrice")))
        tgt, cur = p.get("targetMeanPrice"), p.get("currentPrice")
        pot = (f"{(tgt / cur - 1) * 100:+.1f}%"
               if (isinstance(tgt, (int, float)) and isinstance(cur, (int, float)) and cur) else "n/d")
        mh(r4[2], "Potentiel", pot)
    else:  # ETF
        r1 = st.columns(4)
        mh(r1[0], "Categorie", p.get("category") or "n/d")
        mh(r1[1], "Encours", _money(p.get("totalAssets")))
        mh(r1[2], "Frais (TER)", _pct_raw(p.get("expenseRatio"), 2))
        mh(r1[3], "Rendement", _pct_frac(p.get("yield")))
        mh(st, "Perf YTD", _pct_raw(p.get("ytdReturn"), 1))
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


def afficher_avis_analystes(ticker: str) -> None:
    """Rend la sous-partie 'Avis des analystes' : consensus + upgrades/downgrades."""
    ar = db.get_analyst_ratings(ticker)
    if not ar:
        st.caption("Avis des analystes non recuperes. Lance une mise a jour des donnees "
                   "(actions uniquement).")
        return
    st.caption(f"Source : {ar.get('source')} · maj {fmt_dt(ar.get('asof'))}")

    # Consensus courant (nb d'analystes par avis).
    if any(ar.get(k) is not None for k in ("strong_buy", "buy", "hold", "sell", "strong_sell")):
        cc = st.columns(5)
        cc[0].metric("Achat fort", f"{ar.get('strong_buy') or 0:.0f}")
        cc[1].metric("Achat", f"{ar.get('buy') or 0:.0f}")
        cc[2].metric("Conserver", f"{ar.get('hold') or 0:.0f}")
        cc[3].metric("Vendre", f"{ar.get('sell') or 0:.0f}")
        cc[4].metric("Vendre fort", f"{ar.get('strong_sell') or 0:.0f}")

    # Tendance du consensus (evolution mensuelle des avis "achat").
    try:
        trend = json.loads(ar.get("trend") or "[]")
    except Exception:
        trend = []
    if len(trend) >= 2:
        def _achats(e):
            return (e.get("strong_buy") or 0) + (e.get("buy") or 0)
        now = next((e for e in trend if e.get("periode") == "0m"), trend[0])
        prev = next((e for e in trend if e.get("periode") == "-1m"), None)
        if prev:
            diff = _achats(now) - _achats(prev)
            fleche = "↗️ en amelioration" if diff > 0 else ("↘️ en degradation" if diff < 0 else "→ stable")
            st.caption(f"Tendance du consensus vs mois dernier : {fleche} "
                       f"({_achats(prev):.0f} → {_achats(now):.0f} avis a l'achat).")

    # Derniers upgrades / downgrades par firme.
    try:
        ups = json.loads(ar.get("upgrades") or "[]")
    except Exception:
        ups = []
    if ups:
        st.markdown("**Derniers changements d'avis (90 j) :**")
        ICO = {"releve": "🟢", "abaisse": "🔴", "initie": "🆕", "confirme": "⚪"}
        st.dataframe(
            pd.DataFrame([
                {" ": ICO.get(u.get("action"), "·"),
                 "Date": u.get("date"),
                 "Firme": u.get("firme"),
                 "Action": u.get("action"),
                 "De": u.get("de") or "",
                 "Vers": u.get("vers") or ""}
                for u in ups
            ]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("Aucun changement d'avis d'analyste sur les 90 derniers jours.")


def rendre_news(n: dict, a: dict | None = None, compact: bool = False) -> None:
    """Affiche une news : titre (traduit FR si dispo) + categorie/tonalite,
    resume source traduit (mode complet), et lien vers l'article original."""
    head = n.get("headline", "")
    # Titre francais si Haiku l'a traduit, sinon le titre original.
    titre = ((a.get("titre_fr") or "").strip() if a else "") or head
    if a:
        emoji = {"positif": "🟢", "negatif": "🔴"}.get(a.get("tonalite", "neutre"), "⚪")
        st.markdown(f"{emoji} **[{a.get('categorie', 'autre')}]** {titre}")
    else:
        st.markdown(f"• {titre}" if compact else f"**{titre}**")

    meta = []
    if n.get("datetime"):
        meta.append(n["datetime"][:10])
    if n.get("source"):
        meta.append(n["source"])
    meta_txt = " · ".join(meta)

    if not compact:
        # Resume source traduit en priorite, sinon le resume source original,
        # sinon le resume d'une phrase de Haiku.
        resume_fr = (a.get("resume_fr") or "").strip() if a else ""
        resume = resume_fr or (n.get("summary") or "").strip()
        if resume:
            st.write(resume if len(resume) <= 500 else resume[:500].rstrip() + "…")
        elif a and a.get("resume"):
            st.caption(a["resume"])

    if n.get("url"):
        suffix = f"  ·  _{meta_txt}_" if meta_txt else ""
        st.markdown(f"🔗 [Lire l'article original]({n['url']}){suffix}")
    elif meta_txt:
        st.caption(meta_txt)


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
    # Emplacement reserve pour l'export : rempli en fin de script (voir plus bas)
    # afin d'inclure le briefing et le diagnostic generes durant ce rerun.
    export_slot = st.empty()

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
tab_donnees, tab_news, tab_briefing, tab_diag, tab_idees, tab_edit = st.tabs(
    ["📈 Donnees", "📰 News", "🧠 Briefing", "🔬 Diagnostic", "💡 Idees", "✏️ Watchlist"]
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

    _g = glossaire.definition
    _num_cfg = {
        "Cours": st.column_config.NumberColumn(format="%.2f", help=_g("Cours")),
        "Seance %": st.column_config.NumberColumn(format="%.1f", help=_g("Seance")),
        "Drawdown 52s %": st.column_config.NumberColumn(format="%.1f", help=_g("Drawdown 52s")),
        "Position 52s %": st.column_config.NumberColumn(format="%.0f", help=_g("Position 52s")),
        "RSI 14": st.column_config.NumberColumn(format="%.0f", help=_g("RSI 14")),
        "Etat RSI": st.column_config.TextColumn(help=_g("Etat RSI")),
        "Tendance": st.column_config.TextColumn(help=_g("Tendance")),
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
            ar = db.get_analyst_ratings(v.instrument.ticker)
            # Consensus condense : achat fort + achat / conserver / vendre + vendre fort.
            achat = cons = vente = None
            if ar and any(ar.get(k) is not None for k in ("strong_buy", "buy", "hold")):
                achat = (ar.get("strong_buy") or 0) + (ar.get("buy") or 0)
                cons = ar.get("hold") or 0
                vente = (ar.get("sell") or 0) + (ar.get("strong_sell") or 0)
            est_rows.append({
                "Ticker": v.instrument.ticker,
                "Revisions 30j (net)": v.rev_net_30,
                "Hausses": v.rev_up_30,
                "Baisses": v.rev_down_30,
                "Achat": achat,
                "Conserver": cons,
                "Vendre": vente,
                "Obj. cours moyen": v.pt_mean,
                "Potentiel %": v.potentiel_pct,
            })
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**Calendrier**")
            st.dataframe(pd.DataFrame(cal_rows), use_container_width=True, hide_index=True)
        with cc2:
            st.markdown("**Estimations, revisions & consensus**")
            st.dataframe(
                pd.DataFrame(est_rows), use_container_width=True, hide_index=True,
                column_config={
                    "Revisions 30j (net)": st.column_config.NumberColumn(
                        format="%.0f", help=glossaire.definition("Revisions")),
                    "Hausses": st.column_config.NumberColumn(format="%.0f"),
                    "Baisses": st.column_config.NumberColumn(format="%.0f"),
                    "Achat": st.column_config.NumberColumn(
                        format="%.0f", help="Nb d'analystes en Achat (fort inclus)"),
                    "Conserver": st.column_config.NumberColumn(
                        format="%.0f", help="Nb d'analystes en Conserver"),
                    "Vendre": st.column_config.NumberColumn(
                        format="%.0f", help="Nb d'analystes en Vendre (fort inclus)"),
                    "Obj. cours moyen": st.column_config.NumberColumn(
                        format="%.2f", help=glossaire.definition("Objectif")),
                    "Potentiel %": st.column_config.NumberColumn(
                        format="%.1f", help=glossaire.definition("Potentiel")),
                },
            )
        st.caption("Revisions 30j (net) = analystes relevant l'EPS − ceux l'abaissant (negatif = "
                   "attentes en degradation). Achat/Conserver/Vendre = consensus des analystes. "
                   "Potentiel % = objectif moyen vs cours. "
                   "Donnees actions uniquement ; lance une mise a jour des donnees pour les remplir.")

    # Donnees par instrument : cours + fondamentaux.
    st.subheader("Donnees par instrument")
    if config.watchlist:
        tickers = [i.ticker for i in config.watchlist]
        choix = st.selectbox(
            "Instrument a afficher", tickers,
            format_func=lambda t: next((f"{i.ticker} — {i.nom}" for i in config.watchlist if i.ticker == t), t),
        )

        # --- Auto-recuperation : si les donnees de CET instrument ne sont pas du
        # jour, on les recupere automatiquement (lui seul, pas toute la watchlist).
        # Garde-fou : une seule tentative par instrument et par jour dans la session,
        # pour ne pas re-interroger en boucle un ticker qui ne repond pas.
        aujourd_hui = datetime.now().strftime("%Y-%m-%d")

        def _quote_du_jour(t: str) -> bool:
            q = db.get_quote(t)
            if not q or not q.get("asof"):
                return False
            try:
                d = datetime.fromisoformat(str(q["asof"]))
                if d.tzinfo is not None:
                    d = d.astimezone()
                return d.strftime("%Y-%m-%d") == aujourd_hui
            except Exception:
                return False

        tentatives = st.session_state.setdefault("auto_maj_donnees", {})
        if not _quote_du_jour(choix) and tentatives.get(choix) != aujourd_hui:
            tentatives[choix] = aujourd_hui
            with st.spinner(f"Donnees de {choix} pas a jour : recuperation automatique..."):
                cr_auto = update_donnees_instrument(config, choix)
            if cr_auto.get("status") == "ok":
                st.caption(f"✅ Donnees de {choix} recuperees a l'instant.")
            else:
                st.warning(f"Impossible de recuperer les donnees de {choix} "
                           "(reseau/source ?). Reessaie via « Mettre a jour les donnees ».")
        q_sel = db.get_quote(choix)
        if q_sel and q_sel.get("asof"):
            st.caption(f"🕒 Donnees de {choix} : maj {fmt_dt(q_sel['asof'])}.")

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
            mh(ci[0], "Dernier", _fmt(ind["last_close"]))
            mh(ci[1], "SMA 50", _fmt(ind["sma_50"]))
            mh(ci[2], "SMA 200", _fmt(ind["sma_200"]))
            mh(ci[3], "RSI 14", _fmt(ind["rsi_14"], dec=0))
            mh(ci[4], "Plus-haut 52s", _fmt(ind["high_52w"]))
        else:
            st.caption("Pas encore d'historique pour cet instrument. Lance une mise a jour des donnees.")

        # --- Sous-partie 2 : fondamentaux de l'instrument ---
        st.markdown("#### Fondamentaux de l'instrument")
        afficher_fondamentaux(choix)

        # --- Sous-partie 3 : avis des analystes (actions uniquement) ---
        type_choix = next((i.type for i in config.watchlist if i.ticker == choix), "action")
        if type_choix.lower() == "action":
            st.markdown("#### Avis des analystes")
            afficher_avis_analystes(choix)


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
            for i, n in enumerate(raw):
                if i:
                    st.divider()
                rendre_news(n, analyses.get(n.get("headline", "")), compact=False)
    if not une_news:
        st.caption("Aucune news en base. Clique sur « Mettre a jour les news ». Si yfinance "
                   "ne renvoie rien, ajoute une cle FINNHUB_API_KEY dans .env (source plus fiable).")


# --------------------------------------------------------------------------
# ONGLET BRIEFING : flags (gratuits) + synthese Sonnet (a la demande)
# --------------------------------------------------------------------------
with tab_briefing:
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("**Briefing** — vue d'ensemble + une section par instrument en 3 parties : "
                    "**analyse des chiffres** (onglet Donnees), **analyse des news** (onglet "
                    "News), **conclusion & arguments** avec reco 🥒/🍊/🍅. "
                    "_Claude Sonnet, 1 seul appel pour tout._")
    with c2:
        btn_synthese = st.button("🧠 Generer le briefing", use_container_width=True,
                                 disabled=not config.watchlist or not config.secrets.anthropic_api_key)

    # Le briefing reprend le contenu des onglets Donnees et News : on verifie leur fraicheur.
    donnees_fraiches, asof_donnees = fraicheur("donnees")
    news_fraiches, asof_news = fraicheur("news")

    def _tag_fraicheur(frais: bool, asof: str | None) -> str:
        return f"**{fmt_dt(asof) if asof else 'jamais'}**" + ("" if frais else " ⚠️")

    st.caption(
        f"🕒 Donnees : {_tag_fraicheur(donnees_fraiches, asof_donnees)} · "
        f"News : {_tag_fraicheur(news_fraiches, asof_news)} "
        f"(le briefing reprend ces deux onglets ; ⚠️ = plus vieux que {FRAICHEUR_MAX_H} h)."
    )
    st.caption("Code reco : 🥒 acheter · 🍊 maintenir · 🍅 vendre. "
               "⚠️ Heuristique generee par le LLM, **pas un conseil financier** — la decision reste tienne.")

    # --- Recuperation cross-appareil : le briefing genere est persiste en base (pas
    # seulement en session). Si cette session (nouvel appareil/navigateur) n'a encore
    # rien affiche, on recharge le dernier briefing genere - SANS appel Claude.
    if not st.session_state.get("synth_global") and not st.session_state.get("synth_instruments"):
        _cache = db.get_briefing_cache()
        if _cache and _cache.get("global"):
            st.session_state["synth_global"] = _cache.get("global")
            try:
                st.session_state["synth_instruments"] = json.loads(_cache.get("instruments") or "{}")
            except Exception:
                st.session_state["synth_instruments"] = {}
            st.session_state["synthese_asof"] = _cache.get("synthese_asof")

    data = construire_briefing(config)  # deterministe : lit la base, n'appelle pas Claude

    # Generation de la synthese (UN seul appel Sonnet -> global + par instrument).
    # Doit s'executer AVANT l'affichage pour apparaitre des ce rerun.
    if btn_synthese:
        if not (donnees_fraiches and news_fraiches):
            manquants = ([] if donnees_fraiches else ["Donnees"]) + ([] if news_fraiches else ["News"])
            st.warning(
                f"🌿 Avant de generer le briefing, rafraichis d'abord **{' et '.join(manquants)}** "
                f"(rien de recupere, ou plus vieux que {FRAICHEUR_MAX_H} h). Va dans l'onglet "
                "concerne et clique « Mettre a jour », ou « Tout mettre a jour » en haut. "
                "Le briefing sera ainsi base sur des chiffres et des news a jour."
            )
            log("[UI] 'Generer le briefing' bloque : donnees/news pas fraiches "
                f"(donnees_fraiches={donnees_fraiches}, news_fraiches={news_fraiches})", "warning")
        else:
            _cache = db.get_briefing_cache()
            _inchange = (
                _cache is not None
                and _cache.get("donnees_asof") == asof_donnees
                and _cache.get("news_asof") == asof_news
            )
            if _inchange:
                # Donnees ET news identiques a la derniere generation : on evite un
                # appel Sonnet redondant, on recharge simplement le texte deja genere.
                st.session_state["synth_global"] = _cache.get("global")
                try:
                    st.session_state["synth_instruments"] = json.loads(_cache.get("instruments") or "{}")
                except Exception:
                    st.session_state["synth_instruments"] = {}
                st.session_state["synthese_asof"] = _cache.get("synthese_asof")
                st.info(f"ℹ️ Donnees et news inchangees depuis le dernier briefing "
                        f"(genere le {fmt_dt(_cache['generated_at'])}) : texte recupere "
                        "sans nouvel appel Claude.")
                log("[UI] 'Generer le briefing': donnees/news inchangees -> cache reutilise "
                    "(pas d'appel Sonnet).")
            else:
                log(f"[UI] clic 'Generer le briefing' (watchlist={len(config.watchlist)}, "
                    f"instruments_briefing={len(data.get('instruments', []))})")
                prog_ph = st.empty()
                prog_ph.info("🧠 Redaction du briefing + reco (Claude Sonnet)…")
                _prog_state = {"shown": 0}

                def _prog(n: int) -> None:
                    # Rafraichit l'UI pendant le stream (feedback + connexion maintenue),
                    # mais throttle a ~400 caracteres pour ne pas saturer le frontend.
                    if n - _prog_state["shown"] >= 400:
                        _prog_state["shown"] = n
                        prog_ph.info(f"🧠 Redaction du briefing + reco (Claude Sonnet)… "
                                     f"{n} caracteres recus")

                res = llm.synthese_et_reco(config.secrets, data, progress=_prog)
                prog_ph.empty()
                if res:
                    st.session_state["synth_global"] = res.get("global")
                    st.session_state["synth_instruments"] = res.get("instruments", {})
                    m = db.last_update()
                    synthese_asof = m["asof"] if m else None
                    st.session_state["synthese_asof"] = synthese_asof
                    # Persistance en base (pas seulement session) : recuperation
                    # cross-appareil + reference pour eviter les appels redondants.
                    db.save_briefing_cache(
                        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        donnees_asof=asof_donnees, news_asof=asof_news,
                        synthese_asof=synthese_asof,
                        global_text=res.get("global") or "",
                        instruments_json=json.dumps(res.get("instruments") or {}, ensure_ascii=False),
                    )
                    log(f"[UI] briefing stocke: global={len(res.get('global') or '')} chars, "
                        f"instruments={len(res.get('instruments') or {})}")
                else:
                    st.error("Briefing indisponible (cle/credit Claude ?). Voir data/sam_invest.log.")
                    log("[UI] briefing = None -> rien a afficher", "error")

    # =====================================================================
    # SECTION GLOBAL (big picture)
    # =====================================================================
    st.markdown("## 🌍 Global")

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
            # Reco + briefing en 3 parties EN TETE : coeur de la vue par instrument.
            if f_emoji:
                st.markdown(f"### {f_emoji} {f_label}")
            if entry:
                if entry.get("analyse_chiffres"):
                    st.markdown("**📊 Analyse des chiffres**")
                    st.markdown(entry["analyse_chiffres"])
                if entry.get("analyse_news"):
                    st.markdown("**📰 Analyse des news**")
                    st.markdown(entry["analyse_news"])
                if entry.get("conclusion"):
                    st.markdown("**🎯 Conclusion & arguments**")
                    st.markdown(entry["conclusion"])
            else:
                st.caption("📝 Briefing non genere — clique « Generer le briefing » "
                           "en haut de l'onglet.")
            st.markdown("**Chiffres cles**")
            s = snaps_by.get(t)
            if s:
                mc = st.columns(5)
                mh(mc[0], "Cours", _fmt(s.last_price))
                mh(mc[1], "Seance %", _fmt(s.change_pct, 1))
                mh(mc[2], "RSI 14", _fmt(s.rsi_14, 0))
                mh(mc[3], "Tendance", s.tendance)
                mh(mc[4], "Drawdown 52s", _fmt(s.drawdown_pct, 1))
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
                    rendre_news(n, analyses.get(n.get("headline", "")), compact=True)

# --------------------------------------------------------------------------
# ONGLET DIAGNOSTIC : analyse financiere (chiffres = code, conclusions = Opus 4.8)
# Affichage PROGRESSIF (pas d'effet tunnel) : chiffres instantanes + conclusions
# streamees par etape ; executive summary rempli en haut a la fin.
# --------------------------------------------------------------------------
def _rendre_etape_chiffres(etape: dict) -> None:
    # Tableau HTML pour que chaque indicateur porte un tooltip <abbr> (definition).
    st.markdown(f"#### {etape['titre']}")
    lignes = []
    for ligne in etape["lignes"]:
        valeur = f"🚬 {ligne['valeur']}" if ligne.get("doute") else ligne["valeur"]
        src = "yfinance" if ligne["source"] == "yfinance" else "calculé"
        lignes.append(
            "<tr>"
            f"<td style='padding:3px 16px 3px 0'>{glossaire.abbr(ligne['label'])}</td>"
            f"<td style='padding:3px 16px;font-family:monospace;white-space:nowrap'>{valeur}</td>"
            f"<td style='padding:3px 0;color:#8a8a8a;font-size:0.82em'>{src}</td>"
            "</tr>"
        )
    st.markdown("<table style='width:100%;border-collapse:collapse'>"
                + "".join(lignes) + "</table>", unsafe_allow_html=True)


def _entete_diag(diag: dict) -> None:
    st.subheader(f"{diag.get('nom')} ({diag.get('ticker')})"
                 + (f" · {diag['devise']}" if diag.get("devise") else ""))
    annee, dref = diag.get("annee"), diag.get("date_reference")
    exo = (f"Exercice {annee}" + (f" (cloture {dref})" if dref else "")) if annee else (dref or "date n/d")
    h = diag.get("hypotheses", {})
    st.caption(f"📅 Chiffres : **{exo}** (source yfinance). "
               f"WACC estime : taux sans risque {h.get('taux_sans_risque', 0) * 100:.1f}% · "
               f"prime {h.get('prime_marche', 0) * 100:.1f}% · "
               f"beta {h.get('beta') if h.get('beta') is not None else 'n/d'}. "
               "Colonne « Source » : yfinance (brut) / calculé (formule) ; conclusions = LLM (Opus 4.8). "
               "🚬 = chiffre douteux (aberration ou change).")
    if diag.get("note_fiabilite"):
        st.warning(diag["note_fiabilite"])


def _rendre_diag_statique(r: dict) -> None:
    diag = r["diag"]
    _entete_diag(diag)
    st.markdown("### Executive summary")
    st.caption("🤖 LLM · Claude Opus 4.8")
    st.markdown(r.get("resume") or "")
    st.divider()
    for etape in diag["etapes"]:
        _rendre_etape_chiffres(etape)
        concl = r["conclusions"].get(etape["id"])
        if concl:
            st.caption("🤖 Conclusion — LLM · Claude Opus 4.8")
            st.markdown(concl)


with tab_diag:
    st.markdown("**Diagnostic financier** — cherche une entreprise, selectionne-la, puis "
                "**Analyse**. Les chiffres sont calcules par du code (colonne « Source ») ; "
                "Claude Opus 4.8 redige une conclusion par etape + un executive summary "
                "avec preconisation argumentee (🥒 acheter / 🍊 maintenir / 🍅 vendre). "
                "_Actions uniquement._")
    if not config.secrets.anthropic_api_key:
        st.info("ANTHROPIC_API_KEY absente : le diagnostic necessite Claude Opus 4.8.")

    # Etape 1 : recherche (le formulaire => Entree declenche la recherche).
    with st.form("form_diag", clear_on_submit=False):
        fc1, fc2 = st.columns([4, 1])
        q_diag = fc1.text_input("Ticker ou nom", key="diag_q", label_visibility="collapsed",
                                placeholder="ex : NVDA, Alibaba, ASML...")
        rechercher = fc2.form_submit_button("Rechercher", use_container_width=True)
    if rechercher and q_diag.strip():
        with st.spinner("Recherche (Yahoo)..."):
            st.session_state["diag_results"] = search_instruments(q_diag.strip(), max_results=8)

    # Etape 2 : selection + bouton Analyser.
    analyser, ticker_sel = False, None
    results = st.session_state.get("diag_results")
    if results:
        opts = {f"{r['symbol']} — {r['nom']} ({r['bourse']}, {r['type']})": r["symbol"]
                for r in results}
        pick = st.selectbox("Selectionne l'entreprise a analyser", list(opts.keys()), key="diag_pick")
        ticker_sel = opts.get(pick)
        analyser = st.button("Analyser", use_container_width=True,
                             disabled=not config.secrets.anthropic_api_key or not ticker_sel)
    elif results == []:
        st.caption("Aucun resultat. Essaie un autre nom, ou le ticker exact (ex : NVDA).")

    # Etape 3 : analyse (affichage progressif) ou rendu du dernier diagnostic.
    if analyser and ticker_sel:
        with st.spinner("Recuperation des etats financiers..."):
            diag = construire_diagnostic(config, ticker_sel)
        if "erreur" in diag:
            st.error(diag["erreur"])
            st.session_state["diag_result"] = None
        else:
            _entete_diag(diag)
            summary_ph = st.empty()  # exec summary EN HAUT, rempli a la fin
            summary_ph.info("Executive summary : genere apres les etapes ci-dessous...")
            st.divider()
            conclusions = {}
            for etape in diag["etapes"]:
                _rendre_etape_chiffres(etape)
                st.caption("🤖 Conclusion — LLM · Claude Opus 4.8")
                conclusions[etape["id"]] = st.write_stream(
                    llm.conclusion_etape_stream(config.secrets, etape["titre"], etape["lignes"])
                )
            with summary_ph.container():
                st.markdown("### Executive summary")
                st.caption("🤖 LLM · Claude Opus 4.8")
                resume = st.write_stream(
                    llm.exec_summary_diagnostic_stream(config.secrets, diag, conclusions)
                )
            st.session_state["diag_result"] = {
                "diag": diag, "conclusions": conclusions, "resume": resume,
            }
    elif st.session_state.get("diag_result"):
        _rendre_diag_statique(st.session_state["diag_result"])


# --------------------------------------------------------------------------
# ONGLET IDEES : recommandations d'ajout a la watchlist
# --------------------------------------------------------------------------
# Deux sources de candidats : pairs Finnhub (deterministe) + trous thematiques
# (Claude Sonnet, texte uniquement). CHAQUE ticker candidat est ensuite VALIDE
# (recherche Yahoo) et CHIFFRE en direct par le code avant tout affichage -
# aucun chiffre ni ticker invente n'atteint l'utilisateur sans verification.
# --------------------------------------------------------------------------
with tab_idees:
    st.markdown(
        "**Idees d'ajout a la watchlist** — combine des entreprises comparables "
        "(pairs Finnhub) et des suggestions Claude pour combler des trous de "
        "diversification thematique. Chaque candidat est ensuite **valide** (recherche "
        "Yahoo) et **chiffre en direct** par le code, exactement comme l'onglet Donnees : "
        "aucun ticker ni chiffre invente n'est affiche."
    )
    if not config.secrets.finnhub_api_key and not config.secrets.anthropic_api_key:
        st.info("Sans cle Finnhub ni cle Claude, aucune source de candidats n'est "
                "disponible. Ajoute au moins l'une des deux dans `.env`.")

    ic1, ic2 = st.columns([3, 1])
    with ic1:
        avec_them = st.checkbox(
            "Inclure les suggestions thematiques (Claude Sonnet)",
            value=bool(config.secrets.anthropic_api_key),
            disabled=not config.secrets.anthropic_api_key,
            help="Claude propose des tickers pour combler des trous de diversification ; "
                 "il ne calcule aucun chiffre, seul le code valide et chiffre chaque ticker.",
        )
        if not config.secrets.finnhub_api_key:
            st.caption("⚠️ Sans cle Finnhub : pas de candidats 'pairs', "
                       "seulement les suggestions thematiques Claude (si activees).")
    with ic2:
        btn_idees = st.button(
            "💡 Generer des idees", use_container_width=True,
            disabled=not config.watchlist
            or not (config.secrets.finnhub_api_key or config.secrets.anthropic_api_key),
        )

    if btn_idees:
        log(f"[UI] clic 'Generer des idees' (avec_thematiques={avec_them})")
        with st.spinner("Recherche de candidats (pairs + Claude) puis verification "
                        "des chiffres..."):
            candidats = generer_candidats(config, avec_thematiques=avec_them)
        st.session_state["idees_candidats"] = candidats
        log(f"[UI] Generer des idees: {len(candidats)} candidat(s) retenu(s)")

    candidats = st.session_state.get("idees_candidats")
    if candidats is None:
        st.caption("Clique sur « Generer des idees » pour voir des candidats.")
    elif not candidats:
        st.info("Aucun candidat retenu : soit aucune source disponible, soit tous les "
                "tickers trouves/suggeres etaient deja suivis ou introuvables.")
    else:
        st.markdown(f"### {len(candidats)} candidat(s)")
        st.dataframe(
            pd.DataFrame([
                {"Ticker": c.ticker, "Nom": c.nom, "Type": c.type, "Origine": c.origine,
                 "Cours": c.last_price, "Seance %": c.change_pct,
                 "Drawdown 52s %": c.drawdown_pct, "RSI 14": c.rsi_14, "Tendance": c.tendance}
                for c in candidats
            ]),
            use_container_width=True, hide_index=True,
            column_config={
                "Cours": st.column_config.NumberColumn(format="%.2f"),
                "Seance %": st.column_config.NumberColumn(format="%.1f"),
                "Drawdown 52s %": st.column_config.NumberColumn(format="%.1f"),
                "RSI 14": st.column_config.NumberColumn(format="%.0f"),
            },
        )

        labels = {f"{c.ticker} — {c.nom} ({c.origine})": c for c in candidats}
        choix_ajout = st.multiselect(
            "Selectionne les instruments a ajouter a la watchlist :", list(labels.keys())
        )
        if st.button("➕ Ajouter a la watchlist", disabled=not choix_ajout):
            rows = [{"ticker": i.ticker, "nom": i.nom, "type": i.type, "theme": i.theme}
                    for i in config.watchlist]
            existants = {i.ticker.upper() for i in config.watchlist}
            ajoutes = []
            for lab in choix_ajout:
                c = labels[lab]
                if c.ticker.upper() in existants:
                    continue
                rows.append({"ticker": c.ticker, "nom": c.nom, "type": c.type, "theme": ""})
                existants.add(c.ticker.upper())
                ajoutes.append(c.ticker)
            save_watchlist(rows)
            st.session_state["idees_candidats"] = None
            st.success(
                f"{len(ajoutes)} instrument(s) ajoute(s) : {', '.join(ajoutes)}. "
                "Pense a renseigner le theme dans l'onglet Watchlist. Les donnees se "
                "rempliront automatiquement a la premiere visite de l'onglet Donnees."
            )
            st.rerun()

        st.divider()
        for c in candidats:
            with st.expander(f"{c.ticker} — {c.nom} ({c.type} · {c.bourse or 'n/d'})"):
                st.caption(f"Origine : {c.origine}. {c.raison}")
                mc = st.columns(5)
                mh(mc[0], "Cours", _fmt(c.last_price))
                mh(mc[1], "Seance %", _fmt(c.change_pct, 1))
                mh(mc[2], "RSI 14", _fmt(c.rsi_14, 0))
                mh(mc[3], "Tendance", c.tendance)
                mh(mc[4], "Drawdown 52s", _fmt(c.drawdown_pct, 1))
                if c.type.lower() == "action":
                    fc = st.columns(4)
                    mh(fc[0], "Secteur", c.sector or "n/d")
                    mh(fc[1], "PER (trailing)", _fmt(c.per))
                    mh(fc[2], "Croissance CA", _pct_frac(c.revenue_growth))
                    mh(fc[3], "Marge nette", _pct_frac(c.net_margin))
                    if c.consensus_achat is not None:
                        st.caption(
                            f"Consensus analystes : achat {c.consensus_achat:.0f} · "
                            f"conserver {(c.consensus_conserver or 0):.0f} · "
                            f"vendre {(c.consensus_vendre or 0):.0f}."
                        )


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
    wc1, wc2 = st.columns([3, 1])
    with wc1:
        st.caption(f"🕒 Watchlist enregistree le : **{fmt_dt(mtime) if mtime else 'jamais'}** "
                   f"(fichier config.yaml).")
    with wc2:
        if CONFIG_PATH.exists():
            st.download_button(
                "⬇️ Telecharger config.yaml", data=CONFIG_PATH.read_bytes(),
                file_name="config.yaml", mime="text/yaml", use_container_width=True,
                help="Si l'app tourne en ligne (Streamlit Cloud), le disque est "
                     "reinitialise a chaque redeploiement : telecharge ce fichier apres "
                     "toute modification pour la reintegrer a ton depot GitHub.",
            )

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

# ==========================================================================
# EXPORT GLOBAL (.md) — bouton dans l'en-tete, rempli ICI (fin de script)
# pour inclure le briefing ET le diagnostic generes durant ce meme rerun.
# Un seul document Markdown, pense pour etre reanalyse par Claude ensuite.
# ==========================================================================
try:
    _md_export = construire_export_md(
        config,
        synth_global=st.session_state.get("synth_global"),
        synth_instruments=st.session_state.get("synth_instruments"),
        diag_result=st.session_state.get("diag_result"),
        idees_candidats=st.session_state.get("idees_candidats"),
    )
    export_slot.download_button(
        "⬇️ Exporter (.md)",
        data=_md_export.encode("utf-8"),
        file_name=f"sam_invest_export_{datetime.now():%Y%m%d_%H%M}.md",
        mime="text/markdown",
        use_container_width=True,
        disabled=not config.watchlist,
        help="Exporte toutes les donnees (Donnees, News, Briefing, Diagnostic, Idees) en un "
             "Markdown unique, pret a coller a Claude pour analyse.",
    )
except Exception as e:  # l'export ne doit jamais casser l'app
    log(f"[UI] export .md indisponible: {type(e).__name__}: {e}", "error")

# Note : suivi de portefeuille (PRU, allocation, DCA) retire -> outil de watchlist.
# Note : fonctionnalite email SMTP reportee (choix utilisateur).
