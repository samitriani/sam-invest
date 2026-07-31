"""Sam_Invest - interface Streamlit (un seul processus, 100% manuel).

Outil de WATCHLIST : on surveille des instruments (on ne possede rien ici).
Aucune planification, aucun cron : tout se declenche par les boutons.

UX MOBILE D'ABORD (usage principal : telephone, en transports) :
  - Navigation en MENU BURGER (st.navigation, sidebar repliee) : une seule page
    rendue par run -> page legere, et l'URL porte la page courante donc une
    reconnexion revient exactement ou on etait (les onglets, eux, revenaient au 1er).
  - Textes explicatifs regroupes dans la page "A propos" : les pages de travail
    ne gardent qu'une ligne de contexte.
  - Tout ce qui coute cher (briefing Sonnet, diagnostic Opus) est PERSISTE EN BASE,
    pas en session : une coupure reseau ne fait jamais perdre un resultat.

Chaque page a son propre bouton de mise a jour, plus une mise a jour globale dans
le menu. Objectif : maitriser la consommation d'API Claude.
  - Donnees    : prix + fondamentaux. AUCUN appel Claude.
  - News       : recup news + classement (Claude Haiku).
  - Briefing   : flags (deterministes, gratuits) + synthese (Claude Sonnet, au clic).
  - VN Diagnostic : analyse financiere complete (Claude Opus, au clic).
  - Watchlist    : edition de la liste + suggestions d'ajout (Claude Sonnet, au clic).

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
from sam_invest.data_sources import search_instruments, suggest_theme
from sam_invest.diagnostic import construire_diagnostic
from sam_invest import glossaire
from sam_invest.events import construire_evenements
from sam_invest.idees import generer_candidats
from sam_invest.logs import log
from sam_invest.config import (CONFIG_PATH, doublon_pour, doublons_probables,
                               load_config, save_watchlist)
from sam_invest.update import (update_donnees, update_donnees_instrument,
                               update_global, update_news)

st.set_page_config(page_title="Sam_Invest", page_icon="📊", layout="wide",
                   initial_sidebar_state="auto")  # replie en burger sur telephone

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

# Touche de police minimale : chiffres/valeurs en monospace (IBM Plex Mono),
# plus un bloc mobile (ecran de telephone = usage principal) qui compacte titres,
# marges et metriques. Volontairement limite a des selecteurs stables de Streamlit.
st.markdown(
    "<style>"
    "@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&display=swap');"
    "code, [data-testid=\"stMetricValue\"]{font-family:'IBM Plex Mono','Courier New',monospace;}"
    # Les rangees horizontales (metriques) s'enroulent au lieu de deborder.
    "[data-testid=\"stHorizontalBlock\"]{flex-wrap:wrap;}"
    "@media (max-width:640px){"
    # Marges laterales reduites : plus de largeur utile pour les tableaux.
    "[data-testid=\"stMainBlockContainer\"]{padding-left:0.8rem;padding-right:0.8rem;"
    "padding-top:3rem;}"
    # Titres compactes : moins de defilement pour atteindre le contenu.
    "h1{font-size:1.45rem !important;} h2{font-size:1.2rem !important;}"
    "h3{font-size:1.05rem !important;} h4{font-size:0.95rem !important;}"
    # Metriques : 2 a 3 par ligne au lieu d'une pile verticale.
    "[data-testid=\"stMetric\"]{min-width:8.5rem;flex:1 1 8.5rem;}"
    "[data-testid=\"stMetricValue\"]{font-size:1.05rem;}"
    "[data-testid=\"stMetricLabel\"] p{font-size:0.72rem;}"
    "}"
    "</style>",
    unsafe_allow_html=True,
)

config = load_config()
db.init_db()

# Etat persistant entre reruns (synthese generee a la demande).
st.session_state.setdefault("synth_global", None)
st.session_state.setdefault("synth_instruments", {})  # {ticker: {reco, analyse_chiffres, analyse_news, conclusion}}
st.session_state.setdefault("synthese_asof", None)
st.session_state.setdefault("search_results", None)
st.session_state.setdefault("idees_candidats", None)

# Recommandation : le MOT est ecrit en toutes lettres, la couleur ne fait que le
# renforcer. C'est ce qui manquait au codage precedent par fruits (🥒/🍊/🍅) :
# « concombre = acheter » ne s'auto-explique jamais, et l'icone se telescopait
# avec les pastilles de flags. Ici le badge reste lisible meme sans distinguer
# les couleurs. Composant natif Streamlit : rien a maintenir cote CSS.
RECO_LABEL = {"achat": ("ACHAT", "green"), "garder": ("GARDER", "orange"),
              "vendre": ("VENDRE", "red")}


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


def libelle_resultat(r: dict) -> str:
    """Libelle d'un resultat de recherche, avec alerte sur les cotations relais.

    Une cotation secondaire (Munich, Francfort, MTF...) porte le meme cours mais
    presque aucun fondamental cote Yahoo : le diagnostic y sort avec WACC, PER et
    PBR a "n/d". On le signale au lieu de laisser choisir a l'aveugle.
    """
    marque = " ⚠️ cotation secondaire" if r.get("rang", 0) else ""
    return f"{r['symbol']} — {r['nom']} ({r['bourse']}, {r['type']}){marque}"


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


def badge_reco_md(reco: str) -> str:
    """Badge de reco en markdown, pour un libelle d'expander (`:green-badge[ACHAT]`).

    Renvoie '' si la reco est absente ou illisible : le titre s'affiche alors sans
    badge plutot que de casser.
    """
    entree = RECO_LABEL.get(reco or "")
    if not entree:
        return ""
    mot, couleur = entree
    return f":{couleur}-badge[{mot}]"


def afficher_badge_reco(reco: str) -> None:
    """Badge de reco en composant (dans le corps d'une section)."""
    entree = RECO_LABEL.get(reco or "")
    if entree:
        mot, couleur = entree
        st.badge(mot, color=couleur,
                 help="Heuristique de lecture generee par Claude a partir des chiffres "
                      "et des news — pas un conseil financier. La decision reste tienne.")


def bloc_metriques(items) -> None:
    """Rangee de metriques qui s'ENROULE au lieu de s'empiler.

    st.columns() passe en pile verticale sous 640 px : 5 metriques = 5 ecrans de
    defilement sur telephone. Un conteneur horizontal garde 2-3 metriques par
    ligne. `items` = [(label, valeur), ...] ; tooltip tire du glossaire.
    """
    with st.container(horizontal=True):
        for label, valeur in items:
            st.metric(label, valeur, help=glossaire.definition(label))


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
        tgt, cur = p.get("targetMeanPrice"), p.get("currentPrice")
        pot = (f"{(tgt / cur - 1) * 100:+.1f}%"
               if (isinstance(tgt, (int, float)) and isinstance(cur, (int, float)) and cur) else "n/d")
        bloc_metriques([
            ("Capitalisation", _money(p.get("marketCap"))),
            ("Secteur", p.get("sector") or "n/d"),
            ("PER (trailing)", _ratio(p.get("trailingPE"))),
            ("PER (forward)", _ratio(p.get("forwardPE"))),
        ])
        bloc_metriques([
            ("Price / Book", _ratio(p.get("priceToBook"))),
            ("Marge nette", _pct_frac(p.get("profitMargins"))),
            ("ROE", _pct_frac(p.get("returnOnEquity"))),
            ("Rendement div.", _pct_raw(p.get("dividendYield"), 2)),
        ])
        bloc_metriques([
            ("Croissance CA", _pct_frac(p.get("revenueGrowth"))),
            ("Croissance BPA", _pct_frac(p.get("earningsGrowth"))),
            ("Dette / capitaux", _ratio(p.get("debtToEquity"))),
            ("Current ratio", _ratio(p.get("currentRatio"))),
        ])
        bloc_metriques([
            ("Free cash flow", _money(p.get("freeCashflow"))),
            ("Objectif moyen", _ratio(p.get("targetMeanPrice"))),
            ("Potentiel", pot),
        ])
    else:  # ETF
        bloc_metriques([
            ("Categorie", p.get("category") or "n/d"),
            ("Encours", _money(p.get("totalAssets"))),
            ("Frais (TER)", _pct_raw(p.get("expenseRatio"), 2)),
            ("Rendement", _pct_frac(p.get("yield"))),
            ("Perf YTD", _pct_raw(p.get("ytdReturn"), 1)),
        ])
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
        bloc_metriques([
            ("Achat fort", f"{ar.get('strong_buy') or 0:.0f}"),
            ("Achat", f"{ar.get('buy') or 0:.0f}"),
            ("Conserver", f"{ar.get('hold') or 0:.0f}"),
            ("Vendre", f"{ar.get('sell') or 0:.0f}"),
            ("Vendre fort", f"{ar.get('strong_sell') or 0:.0f}"),
        ])

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


def titre_page(icone: str, titre: str, accroche: str) -> None:
    """En-tete compact de page : une ligne de titre + une ligne de contexte.

    Le detail (methodo, couts, code couleur, glossaire) vit dans la page
    "A propos" : sur un ecran de telephone, chaque paragraphe en dur repousse
    les chiffres hors de l'ecran.
    """
    st.markdown(f"### {icone} {titre}")
    st.caption(accroche + "  ·  _Details dans **ℹ️ A propos** (menu ☰)._")


# ==========================================================================
# PAGES (une seule est rendue par run -> page legere sur mobile)
# ==========================================================================
# --------------------------------------------------------------------------
# PAGE DONNEES : prix + fondamentaux + signaux (aucun appel Claude)
# --------------------------------------------------------------------------
def page_donnees():
    titre_page("📈", "Donnees", "Prix + fondamentaux, aucun appel Claude.")
    if st.button("🔄 Mettre a jour les donnees", use_container_width=True,
                 disabled=not config.watchlist):
        afficher_compte_rendu(run_update(update_donnees, "Mise a jour des donnees"))
    caption_derniere_maj("donnees", "donnees")

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

    # Sections pliables : sur telephone, la page tient en un ecran de sommaire et
    # on ouvre ce qu'on veut lire. Streamlit garde l'etat plie/deplie pendant la
    # session, donc un choix de repliage survit aux interactions suivantes.
    if snaps:
        for type_label, type_key in (("Actions", "action"), ("ETF", "etf")):
            sous = [s for s in snaps if s.instrument.type.lower() == type_key]
            if not sous:
                continue
            with st.expander(f"{type_label} ({len(sous)})", expanded=True):
                df = pd.DataFrame([_row(s) for s in sous])
                st.dataframe(df, use_container_width=True, hide_index=True,
                             column_config=_num_cfg)
        st.caption("Colonnes vides = lance une mise a jour des donnees.")
    else:
        st.warning("Watchlist vide : remplis config.yaml.")

    # --- A venir (resultats / ex-dividende) + Estimations (actions) ---
    # Streamlit interdit d'imbriquer un expander dans un expander : « A venir &
    # estimations » reste donc un titre, et ses deux tableaux sont chacun pliables.
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
        with st.expander("Calendrier", expanded=False):
            st.dataframe(pd.DataFrame(cal_rows), use_container_width=True, hide_index=True)
        with st.expander("Estimations, revisions & consensus", expanded=False):
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
        st.caption("Actions uniquement (survole les en-tetes pour la definition de chaque "
                   "colonne).")


# --------------------------------------------------------------------------
# PAGE PAR INSTRUMENT : cours + fondamentaux + analystes d'UN instrument
# --------------------------------------------------------------------------
# Detachee de la page Donnees : c'est une consultation ciblee (« ou en est
# NVDA ? »), pas la vue d'ensemble. La separer evite de faire defiler tout le
# tableau de la watchlist pour l'atteindre, et allege les deux pages.
# --------------------------------------------------------------------------
def page_instrument():
    titre_page("🔎", "Par instrument",
               "Cours, fondamentaux et avis d'analystes d'un instrument de la watchlist.")
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
            # Decimation : quotidien sur ~6 mois recents, hebdomadaire au-dela.
            # Visuellement identique mais ~2x moins de points rendus (page plus
            # legere). Les INDICATEURS restent calcules sur l'historique complet.
            close = dfh["close"]
            if len(close) > 250:
                recent = close.iloc[-126:]
                ancien = close.iloc[:-126].resample("W-FRI").last().dropna()
                close = pd.concat([ancien, recent])
                close = close[~close.index.duplicated(keep="last")]
            st.line_chart(close, height=300)

            ind = indicateurs_ligne(choix)
            bloc_metriques([
                ("Dernier", _fmt(ind["last_close"])),
                ("SMA 50", _fmt(ind["sma_50"])),
                ("SMA 200", _fmt(ind["sma_200"])),
                ("RSI 14", _fmt(ind["rsi_14"], dec=0)),
                ("Plus-haut 52s", _fmt(ind["high_52w"])),
            ])
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
    else:
        st.warning("Watchlist vide : ajoute un instrument dans la page Watchlist.")


# --------------------------------------------------------------------------
# PAGE NEWS : recup news + classement Haiku
# --------------------------------------------------------------------------
def page_news():
    titre_page("📰", "News", "Recuperation + classement par Claude Haiku.")
    if st.button("🔄 Mettre a jour les news", use_container_width=True,
                 disabled=not config.watchlist):
        afficher_compte_rendu(run_update(update_news, "Mise a jour des news"))
    caption_derniere_maj("news", "news")
    if not config.secrets.anthropic_api_key:
        st.info("Sans cle Claude active, les news s'affichent en clair mais ne sont "
                "ni classees ni resumees.")

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
        st.caption("Aucune news en base. Clique sur « Mettre a jour les news ».")


# --------------------------------------------------------------------------
# PAGE BRIEFING : flags (gratuits) + synthese Sonnet (a la demande)
# --------------------------------------------------------------------------
def page_briefing():
    titre_page("🧠", "Briefing",
               "Vue d'ensemble + analyse en 3 parties par instrument (Claude Sonnet).")
    btn_synthese = st.button("🧠 Generer le briefing", use_container_width=True,
                             disabled=not config.watchlist or not config.secrets.anthropic_api_key)

    # Le briefing reprend le contenu des pages Donnees et News : on verifie leur fraicheur.
    donnees_fraiches, asof_donnees = fraicheur("donnees")
    news_fraiches, asof_news = fraicheur("news")

    def _tag_fraicheur(frais: bool, asof: str | None) -> str:
        return f"**{fmt_dt(asof) if asof else 'jamais'}**" + ("" if frais else " ⚠️")

    st.caption(
        f"🕒 Donnees : {_tag_fraicheur(donnees_fraiches, asof_donnees)} · "
        f"News : {_tag_fraicheur(news_fraiches, asof_news)} "
        f"(⚠️ = plus vieux que {FRAICHEUR_MAX_H} h)."
    )

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

    def _generer_briefing() -> None:
        """UN appel Sonnet (ou reprise du cache si donnees/news inchangees).

        Recalcule la fraicheur en interne pour fonctionner aussi bien apres un
        rafraichissement declenche dans le meme run (bouton combine ci-dessous).
        """
        _df, _ad = fraicheur("donnees")
        _nf, _an = fraicheur("news")
        _dd = construire_briefing(config)
        _cache = db.get_briefing_cache()
        _inchange = (_cache is not None
                     and _cache.get("donnees_asof") == _ad
                     and _cache.get("news_asof") == _an)
        if _inchange:
            # Donnees ET news identiques a la derniere generation : pas de nouvel
            # appel Sonnet, on recharge simplement le texte deja genere.
            st.session_state["synth_global"] = _cache.get("global")
            try:
                st.session_state["synth_instruments"] = json.loads(_cache.get("instruments") or "{}")
            except Exception:
                st.session_state["synth_instruments"] = {}
            st.session_state["synthese_asof"] = _cache.get("synthese_asof")
            st.info(f"ℹ️ Donnees et news inchangees depuis le dernier briefing "
                    f"(genere le {fmt_dt(_cache['generated_at'])}) : texte recupere "
                    "sans nouvel appel Claude.")
            log("[UI] briefing: donnees/news inchangees -> cache reutilise (pas d'appel Sonnet).")
            return

        log(f"[UI] generation briefing (watchlist={len(config.watchlist)}, "
            f"instruments={len(_dd.get('instruments', []))})")
        tickers = [i.ticker for i in config.watchlist]
        total = max(len(tickers), 1)
        bar = st.progress(0.0, text="🧠 Redaction du briefing (Claude Sonnet)…")
        _seen = {"n": -1}

        def _prog(n_chars: int, texte: str = "") -> None:
            # Progression exprimee en INSTRUMENTS (plus parlant que des caracteres) :
            # on compte les tickers dont la cle JSON est deja apparue dans le flux.
            done = sum(1 for tk in tickers if f'"{tk}"' in texte)
            if done != _seen["n"]:
                _seen["n"] = done
                if done == 0:
                    bar.progress(0.03, text="🧠 Redaction de la vue d'ensemble…")
                else:
                    bar.progress(min(done / total, 1.0),
                                 text=f"🧠 Briefing par instrument… {min(done, total)}/{total}")

        res = llm.synthese_et_reco(config.secrets, _dd, progress=_prog)
        bar.empty()
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
                donnees_asof=_ad, news_asof=_an, synthese_asof=synthese_asof,
                global_text=res.get("global") or "",
                instruments_json=json.dumps(res.get("instruments") or {}, ensure_ascii=False),
            )
            log(f"[UI] briefing stocke: global={len(res.get('global') or '')} chars, "
                f"instruments={len(res.get('instruments') or {})}")
        else:
            st.error("Briefing indisponible (cle/credit Claude ?). Voir data/sam_invest.log.")
            log("[UI] briefing = None -> rien a afficher", "error")

    # Generation de la synthese. Doit s'executer AVANT l'affichage (meme rerun).
    if btn_synthese and donnees_fraiches and news_fraiches:
        _generer_briefing()

    # Raccourci quotidien : si donnees/news sont perimees, un seul bouton
    # rafraichit ce qui manque PUIS genere, sans quitter la page.
    if (not (donnees_fraiches and news_fraiches)
            and config.watchlist and config.secrets.anthropic_api_key):
        manquants = ([] if donnees_fraiches else ["Donnees"]) + ([] if news_fraiches else ["News"])
        if btn_synthese:
            st.warning(
                f"🌿 **{' et '.join(manquants)}** trop anciennes (rien de recupere, ou plus "
                f"vieux que {FRAICHEUR_MAX_H} h). Utilise le bouton ci-dessous pour tout faire "
                "en un clic, ou rafraichis la page concernee puis reclique « Generer »."
            )
            log("[UI] 'Generer le briefing' : donnees/news pas fraiches "
                f"(donnees={donnees_fraiches}, news={news_fraiches})", "warning")
        if st.button(f"🔄 Rafraichir {' + '.join(manquants)} puis generer le briefing",
                     key="refresh_then_brief", use_container_width=True):
            if not donnees_fraiches:
                run_update(update_donnees, "Mise a jour des donnees")
            if not news_fraiches:
                run_update(update_news, "Mise a jour des news")
            _generer_briefing()
            st.rerun()

    # =====================================================================
    # SECTION GLOBAL (big picture)
    # =====================================================================
    st.markdown("## 🌍 Global")

    # --- Vue d'ensemble : resume des flags + synthese globale (big picture) ---
    flags = data["flags"]
    flags_by: dict[str, list] = {}
    for f in flags:
        flags_by.setdefault(f["ticker"], []).append(f)

    # Anciennete des flags (nouveau vs persistant) : historise a chaque maj des
    # donnees. Un flag est "nouveau" s'il est apparu lors de la derniere maj.
    _anc = db.anciennete_flags()
    _asof_donnees_raw = (db.last_update("donnees") or {}).get("asof")
    REGLE_LABEL = {"chute": "chute brutale", "technique": "signal technique",
                   "degradation": "degradation", "evenement": "evenement",
                   "revision": "revision d'estimations"}

    def _flag_cle(f) -> str:
        return f"{f['ticker']}|{f['regle']}|{f['severite']}"

    def _flag_nouveau(f) -> bool:
        a = _anc.get(_flag_cle(f))
        return bool(a and _asof_donnees_raw and a["premiere_vue"] == _asof_donnees_raw)

    def _flag_depuis(f) -> str | None:
        a = _anc.get(_flag_cle(f))
        return a["premiere_vue"] if a else None

    alertes = [f for f in flags if f["severite"] == "alerte"]
    infos = [f for f in flags if f["severite"] == "info"]
    nouvelles = [f for f in alertes if _flag_nouveau(f)]
    persistantes = [f for f in alertes if not _flag_nouveau(f)]
    st.markdown(f"### Vue d'ensemble — {len(nouvelles)} nouvelle(s) alerte(s), "
                f"{len(persistantes)} persistante(s), {len(infos)} info(s)")

    # Nouvelles alertes EN PREMIER (ce qui a change depuis la derniere maj).
    for f in nouvelles:
        st.error(f"🆕 [{REGLE_LABEL.get(f['regle'], f['regle'])}] {f['message']}")
    # Alertes persistantes regroupees par regle, repliees (deja vues).
    if persistantes:
        with st.expander(f"🔁 {len(persistantes)} alerte(s) persistante(s) — deja signalees"):
            par_regle: dict[str, list] = {}
            for f in persistantes:
                par_regle.setdefault(f["regle"], []).append(f)
            for regle, items in par_regle.items():
                st.markdown(f"**{REGLE_LABEL.get(regle, regle)}** ({len(items)})")
                for f in items:
                    depuis = _flag_depuis(f)
                    suffix = f" · depuis le {fmt_dt(depuis)}" if depuis else ""
                    st.caption(f"{f['message']}{suffix}")

    if st.session_state.get("synth_global"):
        if st.session_state.get("synthese_asof"):
            st.caption(f"Synthese basee sur les donnees du {fmt_dt(st.session_state['synthese_asof'])}.")
        st.markdown(st.session_state["synth_global"])
    elif config.secrets.anthropic_api_key:
        st.caption("Clique sur « Generer le briefing » pour la vue d'ensemble et les "
                   "commentaires par instrument.")
    else:
        st.info("ANTHROPIC_API_KEY absente : briefing desactive, mais les flags et donnees "
                "par instrument ci-dessous restent valables.")

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
        st.info("💡 Le briefing par instrument apparait apres "
                "« Generer le briefing » en haut de la page. Les chiffres, flags et news "
                "ci-dessous sont deja disponibles sans appel Claude.")

    # Recapitulatif des recos. Masque si aucune n'est connue : un briefing genere
    # avant l'ajout des recos afficherait sinon un « 0 · 0 · 0 » trompeur.
    if synth_inst:
        from collections import Counter
        cnt = Counter((synth_inst.get(i.ticker) or {}).get("reco", "")
                      for i in config.watchlist)
        if any(cnt.get(c) for c in RECO_LABEL):
            bouts = [f"{RECO_LABEL[c][0].lower()} : **{cnt.get(c, 0)}**"
                     for c in ("achat", "garder", "vendre")]
            st.caption("Recommandations — " + " · ".join(bouts))
        else:
            st.caption("Briefing genere avant l'ajout des recommandations : "
                       "regenere-le pour les obtenir.")

    # Tri : ce qui merite l'attention en haut (nouvelle alerte > alerte > info >
    # rien), puis par reco (vendre d'abord, ce qui appelle une decision).
    # L'ordre de la watchlist n'est pas un ordre de priorite.
    def _tri_instrument(inst):
        fl = flags_by.get(inst.ticker, [])
        has_al = any(f["severite"] == "alerte" for f in fl)
        nouvelle_al = any(_flag_nouveau(f) for f in fl if f["severite"] == "alerte")
        sev_rank = 0 if nouvelle_al else (1 if has_al else (2 if fl else 3))
        reco = (synth_inst.get(inst.ticker) or {}).get("reco", "")
        reco_rank = {"vendre": 0, "garder": 1, "achat": 2}.get(reco, 3)
        return (sev_rank, reco_rank, inst.ticker)

    for inst in sorted(config.watchlist, key=_tri_instrument):
        t = inst.ticker
        fl = flags_by.get(t, [])
        has_alerte = any(f["severite"] == "alerte" for f in fl)
        entry = synth_inst.get(t) or {}
        # Le badge de reco (mot + couleur) est le SEUL marqueur du titre : pas de
        # pastille de flag en plus, les deux systemes se telescopaient.
        badge = badge_reco_md(entry.get("reco", ""))
        titre = f"{badge}  **{t}** — {inst.nom}" if badge else f"**{t}** — {inst.nom}"
        with st.expander(titre):
            # Briefing en 3 parties EN TETE : coeur de la vue par instrument.
            if entry:
                afficher_badge_reco(entry.get("reco", ""))
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
                           "en haut de la page.")
            st.markdown("**Chiffres cles**")
            s = snaps_by.get(t)
            if s:
                bloc_metriques([
                    ("Cours", _fmt(s.last_price)),
                    ("Seance %", _fmt(s.change_pct, 1)),
                    ("RSI 14", _fmt(s.rsi_14, 0)),
                    ("Tendance", s.tendance),
                    ("Drawdown 52s", _fmt(s.drawdown_pct, 1)),
                ])
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
            # Flags de cet instrument : badge 'nouveau' vs 'depuis le ...'.
            for f in fl:
                nouveau = _flag_nouveau(f)
                prefix = "🆕 " if nouveau else ""
                depuis = _flag_depuis(f)
                suff = f"  _(depuis le {fmt_dt(depuis)})_" if (not nouveau and depuis) else ""
                if f["severite"] == "alerte":
                    st.error(f"{prefix}[{f['regle']}] {f['message']}{suff}")
                else:
                    st.warning(f"{prefix}[{f['regle']}] {f['message']}{suff}")
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
# PAGE VN DIAGNOSTIC : analyse financiere (chiffres = code, conclusions = Opus 4.8)
# Affichage PROGRESSIF (pas d'effet tunnel) : chiffres instantanes + conclusions
# streamees par etape ; executive summary rempli en haut a la fin.
#
# PERSISTANCE : chaque etape terminee est ecrite en base (statut 'partiel'), le
# resume final la passe en 'complet'. Une coupure reseau en cours de generation
# ne fait donc jamais perdre le travail deja paye a Opus, et un diagnostic reste
# consultable indefiniment (y compris depuis un autre appareil).
# --------------------------------------------------------------------------
def _fmt_date_iso(s) -> str:
    """'2025-12-31' -> '31/12/2025' (tolerant : renvoie '' ou l'entree si non ISO)."""
    try:
        y, m, d = str(s)[:10].split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return str(s) if s else ""


def _rendre_etape_chiffres(etape: dict, date_exo: str = "", date_maj: str = "") -> None:
    # Tableau HTML : chaque libelle porte un tooltip <abbr> (definition) et, pour
    # les indicateurs calcules, la colonne Source porte un tooltip avec la FORMULE
    # + la date de la donnee (cloture d'exercice, ou jour de recuperation pour les
    # multiples de valorisation qui refletent le cours du jour).
    st.markdown(f"#### {etape['titre']}")
    date_txt = date_maj if etape.get("id") == "valorisation" else date_exo
    lignes = []
    for ligne in etape["lignes"]:
        valeur = f"🚬 {ligne['valeur']}" if ligne.get("doute") else ligne["valeur"]
        src = "yfinance" if ligne["source"] == "yfinance" else "calculé"
        src_html = glossaire.formule_abbr(ligne["label"], src)  # tooltip formule si calculé
        if date_txt:
            src_html += f" · {date_txt}"
        lignes.append(
            "<tr>"
            f"<td style='padding:3px 16px 3px 0'>{glossaire.abbr(ligne['label'])}</td>"
            f"<td style='padding:3px 16px;font-family:monospace;white-space:nowrap'>{valeur}</td>"
            f"<td style='padding:3px 0;color:#8a8a8a;font-size:0.82em'>{src_html}</td>"
            "</tr>"
        )
    st.markdown("<table style='width:100%;border-collapse:collapse'>"
                + "".join(lignes) + "</table>", unsafe_allow_html=True)
    if etape.get("note"):  # mise en garde propre a l'etape (ex : societe financiere)
        st.caption(f"🚬 {etape['note']}")


def _entete_diag(diag: dict) -> None:
    st.subheader(f"{diag.get('nom')} ({diag.get('ticker')})"
                 + (f" · {diag['devise']}" if diag.get("devise") else ""))
    annee, dref = diag.get("annee"), diag.get("date_reference")
    exo = (f"Exercice {annee}" + (f" (cloture {_fmt_date_iso(dref)})" if dref else "")) if annee else (dref or "date n/d")
    d_maj = _fmt_date_iso(diag.get("date_recuperation"))
    h = diag.get("hypotheses", {})
    st.caption(f"📅 Comptable : **{exo}** · Cours/multiples recuperes le **{d_maj}** (yfinance) · "
               f"WACC : sans risque {h.get('taux_sans_risque', 0) * 100:.1f}% · "
               f"prime {h.get('prime_marche', 0) * 100:.1f}% · "
               f"beta {h.get('beta') if h.get('beta') is not None else 'n/d'}.")
    if diag.get("note_fiabilite"):
        st.warning(diag["note_fiabilite"])


def _rendre_diag_statique(r: dict) -> None:
    diag = r["diag"]
    _entete_diag(diag)
    genere = r.get("generated_at")
    if genere:
        st.caption(f"💾 Diagnostic conserve, genere le **{fmt_dt(genere)}** "
                   "(aucun appel Claude pour l'afficher).")
    if r.get("statut") == "partiel":
        st.warning("⚠️ Diagnostic incomplet : la generation a ete interrompue (coupure "
                   "reseau ?). Les etapes ci-dessous sont conservees ; relance « Analyser » "
                   "pour la version complete.")
    if r.get("resume"):
        st.markdown("### Executive summary")
        st.caption("🤖 LLM · Claude Opus 4.8")
        afficher_badge_reco(r.get("reco", ""))
        st.markdown(r["resume"])
    st.divider()
    d_exo = _fmt_date_iso(diag.get("date_reference"))
    d_maj = _fmt_date_iso(diag.get("date_recuperation"))
    for etape in diag["etapes"]:
        _rendre_etape_chiffres(etape, d_exo, d_maj)
        concl = r["conclusions"].get(etape["id"])
        if concl:
            st.caption("🤖 Conclusion — LLM · Claude Opus 4.8")
            st.markdown(concl)


def _diag_depuis_ligne(row: dict | None) -> dict | None:
    """Ligne de diagnostic_cache -> dict au format attendu par l'UI et l'export."""
    if not row or not row.get("diag"):
        return None
    try:
        return {
            "diag": json.loads(row["diag"]),
            "conclusions": json.loads(row.get("conclusions") or "{}"),
            "resume": row.get("resume") or "",
            "reco": row.get("reco") or "",
            "generated_at": row.get("generated_at"),
            "statut": row.get("statut"),
        }
    except Exception as e:
        log(f"[UI] diagnostic en cache illisible ({row.get('ticker')}): {e}", "error")
        return None


def _sauver_diag(diag: dict, conclusions: dict, resume: str,
                 generated_at: str, statut: str, reco: str = "") -> None:
    """Ecrit l'etat courant du diagnostic en base (appele apres CHAQUE etape)."""
    try:
        db.save_diagnostic(
            ticker=diag.get("ticker") or "?", nom=diag.get("nom"),
            generated_at=generated_at, statut=statut,
            diag_json=json.dumps(diag, ensure_ascii=False, default=str),
            conclusions_json=json.dumps(conclusions, ensure_ascii=False),
            resume=resume or "", reco=reco or "",
        )
    except Exception as e:  # une ecriture ratee ne doit jamais casser l'affichage
        log(f"[UI] sauvegarde diagnostic impossible: {type(e).__name__}: {e}", "error")


def page_diagnostic():
    titre_page("🔬", "VN Diagnostic",
               "Analyse financiere complete d'une action (Claude Opus 4.8), conservee en base.")
    if not config.secrets.anthropic_api_key:
        st.info("ANTHROPIC_API_KEY absente : le diagnostic necessite Claude Opus 4.8.")

    # --- Reprise apres coupure : si la session est vide (nouvel appareil, onglet
    # recharge, connexion perdue puis revenue), on remonte le dernier diagnostic
    # ecrit en base. Aucun appel Claude.
    if not st.session_state.get("diag_result"):
        repris = _diag_depuis_ligne(db.dernier_diagnostic())
        if repris:
            st.session_state["diag_result"] = repris

    # --- Diagnostics conserves : rechargement instantane et gratuit. ---
    conserves = db.list_diagnostics()
    if conserves:
        courant = ((st.session_state.get("diag_result") or {}).get("diag") or {}).get("ticker")
        libelles = {
            f"{d['ticker']} — {d.get('nom') or d['ticker']} · {fmt_dt(d['generated_at'])}"
            + (" ⚠️ incomplet" if d.get("statut") == "partiel" else ""): d["ticker"]
            for d in conserves
        }
        cles = list(libelles)
        idx = next((i for i, k in enumerate(cles) if libelles[k] == courant), 0)
        cc1, cc2 = st.columns([4, 1])
        choix_conserve = cc1.selectbox(f"💾 {len(conserves)} diagnostic(s) conserve(s)",
                                       cles, index=idx, key="diag_conserves")
        if cc2.button("🗑️ Oublier", use_container_width=True,
                      help="Supprime ce diagnostic de la base"):
            db.supprimer_diagnostic(libelles[choix_conserve])
            st.session_state["diag_result"] = None
            # Le libelle memorise par le selectbox n'existe plus dans les options.
            st.session_state.pop("diag_conserves", None)
            st.rerun()
        if libelles[choix_conserve] != courant:
            st.session_state["diag_result"] = _diag_depuis_ligne(
                db.get_diagnostic(libelles[choix_conserve]))
            st.rerun()

    # Etape 1 : recherche (le formulaire => Entree declenche la recherche).
    with st.form("form_diag", clear_on_submit=False):
        q_diag = st.text_input("Analyser une (autre) entreprise", key="diag_q",
                               placeholder="ex : NVDA, Alibaba, ASML...")
        rechercher = st.form_submit_button("🔎 Rechercher", use_container_width=True)
    if rechercher and q_diag.strip():
        with st.spinner("Recherche (Yahoo)..."):
            st.session_state["diag_results"] = search_instruments(
                q_diag.strip(), max_results=8,
                finnhub_key=config.secrets.finnhub_api_key)

    # Etape 2 : selection + bouton Analyser.
    analyser, ticker_sel = False, None
    results = st.session_state.get("diag_results")
    if results:
        opts = {libelle_resultat(r): r["symbol"] for r in results}
        pick = st.selectbox("Selectionne l'entreprise a analyser", list(opts.keys()), key="diag_pick")
        ticker_sel = opts.get(pick)
        # Deja analysee ? On le dit AVANT de relancer un appel Opus (le plus cher).
        deja = db.get_diagnostic(ticker_sel) if ticker_sel else None
        if deja:
            st.caption(f"ℹ️ {ticker_sel} a deja ete analyse le **{fmt_dt(deja['generated_at'])}** "
                       "(disponible dans la liste ci-dessus, sans nouvel appel Claude).")
        analyser = st.button("🔬 Analyser" + (" a nouveau" if deja else ""),
                             use_container_width=True, type="primary",
                             disabled=not config.secrets.anthropic_api_key or not ticker_sel)
    elif results == []:
        st.caption("Aucun resultat. Essaie un autre nom, ou le ticker exact (ex : NVDA).")

    # Etape 3 : analyse (affichage progressif) ou rendu du dernier diagnostic.
    if analyser and ticker_sel:
        with st.spinner("Recuperation des etats financiers..."):
            diag = construire_diagnostic(config, ticker_sel)
        if "erreur" in diag:
            st.error(diag["erreur"])
        else:
            genere_le = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _entete_diag(diag)
            summary_ph = st.empty()  # exec summary EN HAUT, rempli a la fin
            summary_ph.info("Executive summary : genere apres les etapes ci-dessous...")
            st.divider()
            conclusions = {}
            d_exo = _fmt_date_iso(diag.get("date_reference"))
            d_maj = _fmt_date_iso(diag.get("date_recuperation"))
            # Les chiffres seuls valent deja d'etre conserves : on ecrit avant meme
            # la 1re conclusion, puis apres chaque etape (resilience reseau).
            _sauver_diag(diag, conclusions, "", genere_le, "partiel")
            for etape in diag["etapes"]:
                _rendre_etape_chiffres(etape, d_exo, d_maj)
                st.caption("🤖 Conclusion — LLM · Claude Opus 4.8")
                conclusions[etape["id"]] = st.write_stream(
                    llm.conclusion_etape_stream(config.secrets, etape["titre"],
                                                etape["lignes"], etape.get("note", ""))
                )
                _sauver_diag(diag, conclusions, "", genere_le, "partiel")
            with summary_ph.container():
                st.markdown("### Executive summary")
                st.caption("🤖 LLM · Claude Opus 4.8")
                resume = st.write_stream(
                    llm.exec_summary_diagnostic_stream(config.secrets, diag, conclusions)
                )
            # Opus termine par une ligne « RECO: ACHAT ». On la detache une fois le
            # flux fini, puis on re-rend le bloc avec le badge EN TETE : pendant le
            # streaming l'utilisateur lit l'analyse, pas un marqueur technique.
            reco_diag, resume = llm.extraire_reco_resume(resume)
            if reco_diag:
                with summary_ph.container():
                    st.markdown("### Executive summary")
                    st.caption("🤖 LLM · Claude Opus 4.8")
                    afficher_badge_reco(reco_diag)
                    st.markdown(resume)
            _sauver_diag(diag, conclusions, resume, genere_le, "complet", reco_diag)
            st.session_state["diag_result"] = {
                "diag": diag, "conclusions": conclusions, "resume": resume,
                "reco": reco_diag, "generated_at": genere_le, "statut": "complet",
            }
            # Le libelle de ce ticker dans la liste des conserves porte l'ancienne
            # date : on oublie la selection memorisee pour eviter un decalage.
            st.session_state.pop("diag_conserves", None)
    elif st.session_state.get("diag_result"):
        _rendre_diag_statique(st.session_state["diag_result"])


# --------------------------------------------------------------------------
# SECTION SUGGESTIONS : candidats a l'ajout, rendue DANS la page Watchlist
# --------------------------------------------------------------------------
# Fusionnee avec la watchlist : proposer un instrument et l'ajouter etaient deux
# pages alors que c'est un seul geste. Le bouton « Ajouter » des suggestions
# ecrit dans la liste editee juste au-dessus.
#
# Deux sources de candidats : pairs Finnhub (deterministe) + trous thematiques
# (Claude Sonnet, texte uniquement). CHAQUE ticker candidat est ensuite VALIDE
# (recherche Yahoo) et CHIFFRE en direct par le code avant tout affichage -
# aucun chiffre ni ticker invente n'atteint l'utilisateur sans verification.
#
# Section et non expander : elle contient deja un expander par candidat, et
# Streamlit interdit de les imbriquer.
# --------------------------------------------------------------------------
def _section_suggestions():
    st.markdown("### 💡 Suggestions d'ajout")
    st.caption("Entreprises comparables (pairs Finnhub) + suggestions Claude pour "
               "combler des trous de diversification. Chaque candidat est verifie et "
               "chiffre par le code avant affichage.")
    if not config.secrets.finnhub_api_key and not config.secrets.anthropic_api_key:
        st.info("Sans cle Finnhub ni cle Claude, aucune source de candidats n'est "
                "disponible. Ajoute au moins l'une des deux dans `.env`.")

    # Flash des doublons detectes au dernier ajout (survit au st.rerun).
    for tk, jumeau in st.session_state.pop("idees_doublons_flash", []):
        st.warning(f"⚠️ {tk} ressemble a {jumeau} deja suivi (meme societe, cotation "
                   "differente ?). Supprime l'un des deux dans la liste ci-dessus si "
                   "c'est un doublon.")

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
    btn_idees = st.button(
        "💡 Generer des suggestions", use_container_width=True,
        disabled=not config.watchlist
        or not (config.secrets.finnhub_api_key or config.secrets.anthropic_api_key),
    )

    if btn_idees:
        log(f"[UI] clic 'Generer des suggestions' (avec_thematiques={avec_them})")
        with st.spinner("Recherche de candidats (pairs + Claude) puis verification "
                        "des chiffres..."):
            candidats = generer_candidats(config, avec_thematiques=avec_them)
        st.session_state["idees_candidats"] = candidats
        log(f"[UI] Generer des suggestions: {len(candidats)} candidat(s) retenu(s)")

    candidats = st.session_state.get("idees_candidats")
    if candidats is None:
        st.caption("Clique sur « Generer des suggestions » pour voir des candidats.")
    elif not candidats:
        st.info("Aucun candidat retenu : soit aucune source disponible, soit tous les "
                "tickers trouves/suggeres etaient deja suivis ou introuvables.")
    else:
        st.markdown(f"**{len(candidats)} candidat(s)**")
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
            ajoutes, doublons = [], []
            with st.spinner("Ajout + detection du theme (secteur / pays)..."):
                for lab in choix_ajout:
                    c = labels[lab]
                    if c.ticker.upper() in existants:
                        continue
                    jumeau = doublon_pour(c.ticker, c.nom, config.watchlist)
                    if jumeau:
                        doublons.append((c.ticker, jumeau))
                    rows.append({"ticker": c.ticker, "nom": c.nom, "type": c.type,
                                 "theme": suggest_theme(c.ticker, c.type)})
                    existants.add(c.ticker.upper())
                    ajoutes.append(c.ticker)
            save_watchlist(rows)
            st.session_state["idees_candidats"] = None
            st.session_state["idees_doublons_flash"] = doublons
            st.success(
                f"{len(ajoutes)} instrument(s) ajoute(s) : {', '.join(ajoutes)}. "
                "Theme detecte automatiquement (ajustable dans la page Watchlist). "
                "Les donnees se rempliront a la premiere visite de la page Donnees."
            )
            st.rerun()

        st.divider()
        for c in candidats:
            with st.expander(f"{c.ticker} — {c.nom} ({c.type} · {c.bourse or 'n/d'})"):
                st.caption(f"Origine : {c.origine}. {c.raison}")
                bloc_metriques([
                    ("Cours", _fmt(c.last_price)),
                    ("Seance %", _fmt(c.change_pct, 1)),
                    ("RSI 14", _fmt(c.rsi_14, 0)),
                    ("Tendance", c.tendance),
                    ("Drawdown 52s", _fmt(c.drawdown_pct, 1)),
                ])
                if c.type.lower() == "action":
                    bloc_metriques([
                        ("Secteur", c.sector or "n/d"),
                        ("PER (trailing)", _fmt(c.per)),
                        ("Croissance CA", _pct_frac(c.revenue_growth)),
                        ("Marge nette", _pct_frac(c.net_margin)),
                    ])
                    if c.consensus_achat is not None:
                        st.caption(
                            f"Consensus analystes : achat {c.consensus_achat:.0f} · "
                            f"conserver {(c.consensus_conserver or 0):.0f} · "
                            f"vendre {(c.consensus_vendre or 0):.0f}."
                        )


# --------------------------------------------------------------------------
# PAGE WATCHLIST : edition de la liste + suggestions d'ajout
# --------------------------------------------------------------------------
# Ancienne page « Idees » fusionnee ici (voir _section_suggestions) : chercher un
# instrument, se faire suggerer des candidats et editer la liste sont trois faces
# du meme geste — les separer obligeait a naviguer entre deux pages pour un ajout.
# --------------------------------------------------------------------------
def page_watchlist():
    titre_page("✏️", "Watchlist",
               "Ajouter, retirer et re-theminer les instruments suivis.")
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
    # Formulaire : sur telephone, la touche "OK" du clavier lance la recherche.
    st.markdown("#### 🔎 Rechercher un instrument")
    with st.form("form_wl_search", clear_on_submit=False):
        q = st.text_input("Nom ou ticker", key="wl_search_q", label_visibility="collapsed",
                          placeholder="ex : air liquide, alibaba, nasdaq, semiconducteurs...")
        btn_search = st.form_submit_button("🔎 Rechercher", use_container_width=True)
    if btn_search and q.strip():
        with st.spinner("Recherche (Yahoo)..."):
            st.session_state["search_results"] = search_instruments(
                q.strip(), finnhub_key=config.secrets.finnhub_api_key)

    # Flash des doublons detectes au dernier ajout par recherche (survit au rerun).
    for tk, jumeau in st.session_state.pop("wl_doublons_flash", []):
        st.warning(f"⚠️ {tk} ressemble a {jumeau} deja suivi (meme societe, cotation "
                   "differente ?). Supprime l'un des deux ci-dessous si c'est un doublon.")

    results = st.session_state.get("search_results")
    if results:
        labels = {libelle_resultat(r): r for r in results}
        choix = st.multiselect("Resultats — coche ce que tu veux ajouter :", list(labels.keys()))
        if st.button("➕ Ajouter a la watchlist", disabled=not choix):
            existants = {i.ticker.upper() for i in config.watchlist}
            rows = [{"ticker": i.ticker, "nom": i.nom, "type": i.type, "theme": i.theme}
                    for i in config.watchlist]
            ajout, doublons = 0, []
            with st.spinner("Ajout + detection du theme (secteur / pays)..."):
                for lab in choix:
                    r = labels[lab]
                    if r["symbol"].upper() in existants:
                        continue
                    jumeau = doublon_pour(r["symbol"], r["nom"], config.watchlist)
                    if jumeau:
                        doublons.append((r["symbol"], jumeau))
                    rows.append({"ticker": r["symbol"], "nom": r["nom"], "type": r["type"],
                                 "theme": suggest_theme(r["symbol"], r["type"])})
                    existants.add(r["symbol"].upper())
                    ajout += 1
            save_watchlist(rows)
            st.session_state["search_results"] = None
            st.session_state["wl_doublons_flash"] = doublons
            st.session_state["wl_flash_ok"] = (
                f"{ajout} instrument(s) ajoute(s), theme detecte automatiquement "
                "(ajuste-le ci-dessous si besoin).")
            st.rerun()
    elif results == []:
        st.caption("Aucun resultat (action/ETF). Pour un ETF, cherche le nom du fonds ou "
                   "son ticker (ex : « invesco qqq », « QQQ ») — les indices sont exclus.")

    with st.expander("➕ Ajouter manuellement un ticker (si la recherche ne trouve pas)"):
        with st.form("wl_ajout_manuel", clear_on_submit=True):
            mc1, mc2, mc3, mc4 = st.columns([2, 3, 2, 3])
            m_ticker = mc1.text_input("Ticker *", placeholder="NVDA, ASML.AS, CW8.PA")
            m_nom = mc2.text_input("Nom", placeholder="Nvidia")
            m_type = mc3.selectbox("Type", ["action", "ETF"])
            m_theme = mc4.text_input("Theme", placeholder="Tech, Emergents...")
            if st.form_submit_button("Ajouter", use_container_width=True):
                tk = m_ticker.strip()
                if not tk:
                    st.warning("Le ticker est obligatoire.")
                elif tk.upper() in {i.ticker.upper() for i in config.watchlist}:
                    st.warning(f"{tk} est deja dans la watchlist.")
                else:
                    rows = [{"ticker": i.ticker, "nom": i.nom, "type": i.type, "theme": i.theme}
                            for i in config.watchlist]
                    theme = m_theme.strip()
                    if not theme:
                        with st.spinner("Detection du theme (secteur / pays)..."):
                            theme = suggest_theme(tk, m_type)
                    rows.append({"ticker": tk, "nom": m_nom.strip() or tk,
                                 "type": m_type, "theme": theme})
                    save_watchlist(rows)
                    st.session_state["wl_flash_ok"] = f"{tk} ajoute a la watchlist."
                    st.rerun()

    st.divider()

    # Audit permanent des doublons cross-listing (meme societe, cotation differente).
    _paires = doublons_probables(config.watchlist)
    if _paires:
        st.warning("⚠️ Doublons probables (meme societe suivie plusieurs fois — double "
                   "consommation d'API/Claude) : "
                   + " · ".join(f"**{a}** ↔ **{b}**" for a, b in _paires)
                   + ". Supprime l'une des lignes ci-dessous si c'est bien un doublon.")

    # Flash de confirmation apres ajout/suppression (survit au st.rerun).
    _flash = st.session_state.pop("wl_flash_ok", None)
    if _flash:
        st.success(f"✅ {_flash}")

    st.markdown(f"##### 📋 Watchlist actuelle — {len(config.watchlist)} instrument(s)")

    if not config.watchlist:
        st.info("Watchlist vide. Utilise la recherche ci-dessus ou « Ajouter "
                "manuellement » pour ajouter ton premier instrument.")
    else:
        st.caption("Modifie le **theme** de chaque ligne puis clique 💾 en bas. "
                   "Le bouton 🗑️ retire immediatement la ligne.")

        def _wl_rows(skip: str | None = None) -> list[dict]:
            """Reconstruit la liste depuis les champs edites (session_state)."""
            out = []
            for it in config.watchlist:
                if skip is not None and it.ticker == skip:
                    continue
                out.append({
                    "ticker": it.ticker,
                    "nom": st.session_state.get(f"wl_nom_{it.ticker}", it.nom),
                    "type": st.session_state.get(f"wl_type_{it.ticker}", it.type),
                    "theme": st.session_state.get(f"wl_theme_{it.ticker}", it.theme),
                })
            return out

        # Une ligne = un conteneur horizontal (et NON st.columns) : sur telephone
        # les champs s'enroulent en 2 lignes au lieu de s'empiler en 5 champs
        # anonymes. Chaque champ porte un placeholder qui l'identifie une fois
        # empile, et le ticker (non modifiable) ouvre toujours la ligne.
        for it in config.watchlist:
            with st.container(horizontal=True, vertical_alignment="center"):
                # Largeurs calees pour qu'un telephone (375 px) affiche la ligne en
                # 2 rangees : [ticker][nom] puis [type][theme][🗑️].
                st.text_input("Ticker", value=it.ticker, key=f"wl_tk_{it.ticker}",
                              disabled=True, label_visibility="collapsed", width=100)
                st.text_input("Nom", value=it.nom, key=f"wl_nom_{it.ticker}",
                              placeholder="Nom", label_visibility="collapsed", width=210)
                st.selectbox("Type", ["action", "ETF"],
                             index=1 if str(it.type).lower() in ("etf", "fund", "fonds") else 0,
                             key=f"wl_type_{it.ticker}", label_visibility="collapsed", width=105)
                st.text_input("Theme", value=it.theme, key=f"wl_theme_{it.ticker}",
                              placeholder="Theme (ex : Tech)", label_visibility="collapsed",
                              width=170)
                if st.button("🗑️", key=f"wl_del_{it.ticker}",
                             help=f"Retirer {it.ticker} de la watchlist"):
                    save_watchlist(_wl_rows(skip=it.ticker))
                    st.session_state["wl_flash_ok"] = f"{it.ticker} retire de la watchlist."
                    st.rerun()

        st.write("")
        btn_save = st.button("💾 Enregistrer les modifications",
                             use_container_width=True, type="primary")
        st.caption("Enregistre les changements de nom / type / theme. Seule la liste "
                   "est reecrite dans config.yaml ; les seuils et regles sont preserves.")

        if btn_save:
            n = save_watchlist(_wl_rows())
            st.session_state["wl_flash_ok"] = f"Watchlist enregistree : {n} instrument(s)."
            st.rerun()  # recharge config.yaml pour rafraichir toute l'app

    st.divider()
    _section_suggestions()


# --------------------------------------------------------------------------
# PAGE A PROPOS : toutes les explications, en un seul endroit
# --------------------------------------------------------------------------
# Les pages de travail ne gardent qu'une ligne de contexte : sur un ecran de
# telephone, chaque paragraphe explicatif en dur repousse les chiffres hors de
# l'ecran, et on ne relit pas une methodologie a chaque consultation.
# --------------------------------------------------------------------------
def page_about():
    st.markdown("### ℹ️ A propos de Sam_Invest")
    st.caption("Toutes les explications de l'application sont regroupees ici.")

    st.warning(
        "**Ceci n'est pas un conseil financier.** Les recommandations "
        "ACHAT / GARDER / VENDRE sont des heuristiques de lecture generees par un LLM "
        "a partir de chiffres publics — une aide a la reflexion, jamais un ordre. "
        "La decision finale reste humaine — la tienne."
    )

    with st.expander("🎯 A quoi sert l'application", expanded=True):
        st.markdown(
            "Sam_Invest est un outil de **watchlist** : on surveille des instruments "
            "(actions, ETF), on n'en possede aucun ici — pas de PRU, pas d'allocation, "
            "pas de suivi de portefeuille.\n\n"
            "**Tout est manuel.** Aucune tache planifiee, aucun appel automatique : "
            "rien ne part vers une API tant que tu n'as pas clique. C'est ce qui permet "
            "de maitriser la facture Claude."
        )

    with st.expander("🧭 Principe : ce que calcule le code, ce que redige Claude"):
        st.markdown(
            "La separation est **stricte** et c'est la garantie de fiabilite de l'outil :\n\n"
            "| | Qui | Quoi |\n|---|---|---|\n"
            "| **Chiffres & signaux** | Code deterministe (pandas) | Cours, RSI, moyennes "
            "mobiles, drawdown, ratios, flags. Reproductible, verifiable, jamais invente. |\n"
            "| **Textes** | Claude (LLM) | Traductions, resumes, conclusions, "
            "recommandations. |\n\n"
            "Un LLM ne calcule **jamais** un chiffre affiche par l'application. Dans la "
            "section Suggestions, meme les tickers proposes par Claude sont revalides par le code "
            "(recherche Yahoo) et chiffres en direct avant d'apparaitre a l'ecran."
        )

    with st.expander("📱 Usage mobile & coupures de connexion"):
        st.markdown(
            "L'application est pensee pour un telephone, en deplacement :\n\n"
            "- **Menu ☰** (en haut a gauche) : une seule page est chargee a la fois, "
            "la page reste donc legere.\n"
            "- **L'adresse porte la page courante** : si la connexion tombe et revient, "
            "tu reviens exactement ou tu etais.\n"
            "- **Rien de coûteux n'est perdu.** Briefing et diagnostics sont ecrits dans "
            "une base locale (SQLite), pas seulement dans la session du navigateur. Un "
            "diagnostic est meme sauvegarde **apres chaque etape** : si la connexion "
            "coupe en pleine generation, ce qui a deja ete paye a Claude est conserve "
            "et reaffichable (marque « ⚠️ incomplet »).\n"
            "- **Consultation hors ligne partielle** : toutes les donnees deja "
            "recuperees restent lisibles sans reseau ; seules les mises a jour et les "
            "generations Claude en ont besoin."
        )

    with st.expander("📄 Les pages, une par une"):
        st.markdown(
            "**📈 Donnees** — Vue d'ensemble de la watchlist : tableau des cours et "
            "signaux (Actions / ETF), calendrier des resultats et estimations. Chaque "
            "section est **pliable** : replie ce que tu ne lis pas, l'etat est conserve "
            "pendant la session. **Aucun appel Claude.**\n\n"
            "**🔎 Par instrument** — Le detail d'**un seul** instrument : graphique de "
            "cours, indicateurs, fondamentaux et avis d'analystes. Si ses donnees ne sont "
            "pas du jour, elles sont recuperees automatiquement (une seule tentative par "
            "jour). **Aucun appel Claude.**\n\n"
            "**📰 News** — Recupere les news puis les fait **classer et traduire par "
            "Claude Haiku** (categorie + tonalite 🟢/⚪/🔴). Sans cle Claude, les news "
            "s'affichent en clair, non classees.\n\n"
            "**🧠 Briefing** — Vue d'ensemble + une section par instrument en 3 parties "
            "(analyse des chiffres, analyse des news, conclusion) + une **recommandation** "
            "ACHAT / GARDER / VENDRE. "
            "**Un seul appel Claude Sonnet** pour tout. Le briefing exige des donnees et "
            "des news de moins de "
            f"{FRAICHEUR_MAX_H} h ; si elles sont perimees, un bouton unique rafraichit "
            "ce qui manque puis genere. Si rien n'a change depuis la derniere fois, le "
            "texte est repris du cache sans nouvel appel.\n\n"
            "**🔬 VN Diagnostic** — Analyse financiere complete d'une **action** en plusieurs "
            "etapes (rentabilite, structure financiere, creation de valeur, valorisation). "
            "Les chiffres viennent du code, **Claude Opus 4.8** redige une conclusion par "
            "etape, un executive summary et une **recommandation** (meme code que le "
            "Briefing). C'est l'appel "
            "le plus cher : les diagnostics sont conserves et rechargeables gratuitement. "
            "« VN » renvoie a **Veronique Nguyen**, dont la methode d'analyse financiere "
            "inspire le deroule en etapes.\n\n"
            "**✏️ Watchlist** — Tout ce qui touche a la liste suivie, au meme endroit :\n"
            "- **Rechercher et ajouter** un instrument par son nom (pas besoin de "
            "connaitre le ticker). Le theme est detecte automatiquement a l'ajout "
            "(secteur / pays) et reste modifiable.\n"
            "- **Editer la liste** : nom, type, theme. Les modifications ne sont ecrites "
            "qu'au clic sur 💾 ; le retrait 🗑️ est immediat.\n"
            "- **💡 Suggestions d'ajout** (ex-page « Idees ») : candidats issus des "
            "entreprises comparables (pairs Finnhub, deterministe) et de suggestions "
            "thematiques (Claude Sonnet) pour combler des trous de diversification. "
            "Chaque candidat est verifie et chiffre par le code avant affichage, puis "
            "s'ajoute a la liste en un clic."
        )

    with st.expander("🎨 Codes couleur et symboles"):
        st.markdown(
            "**Recommandation (Claude)** — le mot est toujours ecrit en toutes lettres, "
            "la couleur ne fait que le renforcer :\n"
            "- **ACHAT** (vert) — le dossier parait attractif au niveau actuel\n"
            "- **GARDER** (orange) — rien qui justifie d'agir\n"
            "- **VENDRE** (rouge) — signaux de degradation\n\n"
            "C'est une lecture des chiffres et des news, **pas un ordre** : elle "
            "s'accompagne toujours des arguments qui l'ont produite, a lire avant de "
            "decider quoi que ce soit.\n\n"
            "**Flags (deterministes, calcules par le code)** :\n"
            "- **Alerte** (bandeau rouge) — chute brutale, degradation, signal "
            "technique fort\n"
            "- **Info** (bandeau orange) — a noter, sans urgence\n"
            "- 🆕 — flag apparu lors de la **derniere** mise a jour des donnees ; sans le "
            "badge, il est persistant (deja signale, avec sa date de premiere apparition)\n\n"
            "**News** : 🟢 positif · ⚪ neutre · 🔴 negatif.\n\n"
            "**VN Diagnostic** : 🚬 signale un chiffre douteux — aberration comptable, ou "
            "ratio fausse par un ecart de devise entre les comptes et la cotation.\n\n"
            "**Fraicheur** : ⚠️ a cote d'une date = plus vieux que "
            f"{FRAICHEUR_MAX_H} heures."
        )

    with st.expander("💸 Modeles Claude et cout de chaque bouton"):
        st.markdown(
            "| Bouton | Modele | Ordre de grandeur |\n|---|---|---|\n"
            "| Mettre a jour les donnees | *aucun* | gratuit |\n"
            "| Mettre a jour les news | Claude Haiku | tres faible |\n"
            "| Generer le briefing | Claude Sonnet | 1 appel pour toute la watchlist |\n"
            "| Generer des suggestions | Claude Sonnet | 1 appel (si thematiques activees) |\n"
            "| Analyser (VN Diagnostic) | Claude Opus 4.8 | le plus cher : 1 appel par "
            "etape + 1 resume |\n"
            "| Tout mettre a jour | Haiku (news) | donnees + news, **pas** le briefing |\n\n"
            "Deux garde-fous evitent de payer deux fois : le briefing est repris du cache "
            "si les donnees et les news n'ont pas bouge, et la page VN Diagnostic previent "
            "qu'une entreprise a deja ete analysee avant de relancer Opus."
        )

    with st.expander("🔌 Sources de donnees & stockage"):
        cles = [
            ("Claude (ANTHROPIC_API_KEY)", bool(config.secrets.anthropic_api_key),
             "News classees, briefing, suggestions thematiques, VN Diagnostic"),
            ("Finnhub (FINNHUB_API_KEY)", bool(config.secrets.finnhub_api_key),
             "News de repli, entreprises comparables (pairs)"),
        ]
        st.markdown(
            "**Repli en cascade** pour les cours et fondamentaux : "
            "`yfinance` → `Finnhub` → `FMP`. Si une source ne repond pas, la suivante "
            "prend le relais ; une colonne vide signifie qu'aucune n'a repondu.\n\n"
            "**Stockage** : tout est ecrit dans une base SQLite locale "
            "(`data/`) — cours, fondamentaux, news, flags, briefing, diagnostics. "
            "La watchlist, elle, vit dans `config.yaml`.\n\n"
            "**Cles configurees :**"
        )
        for nom, presente, usage in cles:
            st.markdown(f"- {'✅' if presente else '❌'} **{nom}** — {usage}")
        st.caption("Les cles se declarent dans `.env` en local, ou dans les secrets "
                   "Streamlit Cloud en ligne.")
        if CONFIG_PATH.exists():
            st.caption("⚠️ En ligne (Streamlit Cloud), le disque est reinitialise a chaque "
                       "redeploiement : telecharge `config.yaml` depuis la page Watchlist "
                       "apres toute modification.")

    with st.expander("📖 Glossaire"):
        st.caption("Tous ces termes sont aussi disponibles en infobulle dans "
                   "l'application (survole un libelle ou l'en-tete d'une colonne).")
        filtre = st.text_input("Filtrer", key="glo_filtre", placeholder="ex : RSI, ROIC, WACC...",
                               label_visibility="collapsed")
        f = (filtre or "").strip().lower()
        termes = sorted(glossaire.GLOSSAIRE.items())
        if f:
            termes = [(k, v) for k, v in termes if f in k.lower() or f in v.lower()]
        if not termes:
            st.caption("Aucun terme ne correspond.")
        for terme, defi in termes:
            formule = glossaire.FORMULES.get(terme)
            st.markdown(f"**{terme}** — {defi}"
                        + (f"  \n`{formule}`" if formule else ""))


# ==========================================================================
# NAVIGATION (menu burger) + actions globales
# ==========================================================================
# st.navigation plutot que st.tabs : une seule page est rendue par run (page
# legere sur mobile), le menu se replie derriere le burger ☰, et l'URL porte la
# page courante — une reconnexion revient donc pile ou on etait, alors que les
# onglets repartaient toujours du premier.
PAGES = [
    # Page par defaut : Streamlit la sert a la racine « / » (son url_path n'est pas
    # utilise dans les liens). C'est elle qu'on retrouve apres une reconnexion sans
    # chemin explicite.
    st.Page(page_donnees, title="Donnees", icon="📈", url_path="donnees", default=True),
    st.Page(page_instrument, title="Par instrument", icon="🔎", url_path="instrument"),
    st.Page(page_news, title="News", icon="📰", url_path="news"),
    st.Page(page_briefing, title="Briefing", icon="🧠", url_path="briefing"),
    st.Page(page_diagnostic, title="VN Diagnostic", icon="🔬", url_path="vn-diagnostic"),
    st.Page(page_watchlist, title="Watchlist", icon="✏️", url_path="watchlist"),
    st.Page(page_about, title="A propos", icon="ℹ️", url_path="a-propos"),
]
navigation = st.navigation(PAGES, position="sidebar")

# Le menu accueille aussi les actions globales : sur telephone, elles n'ont pas
# a occuper le haut de chaque page.
with st.sidebar:
    st.markdown("## 📊 Sam_Invest")
    st.caption("Watchlist & signaux")
    st.divider()
    btn_global = st.button(
        "🔄 Tout mettre a jour", use_container_width=True, disabled=not config.watchlist,
        help="Donnees + News. Ne genere PAS la synthese Sonnet (cout maitrise).",
    )
    # Emplacement reserve pour l'export : rempli en fin de script (voir plus bas)
    # afin d'inclure le briefing et le diagnostic generes durant ce rerun.
    export_slot = st.empty()
    _f_don, _a_don = fraicheur("donnees")
    _f_new, _a_new = fraicheur("news")
    st.caption(
        f"🕒 Donnees : {fmt_dt(_a_don) if _a_don else 'jamais'}{'' if _f_don else ' ⚠️'}  \n"
        f"🕒 News : {fmt_dt(_a_new) if _a_new else 'jamais'}{'' if _f_new else ' ⚠️'}"
    )
    if config.warnings:
        with st.expander("⚠️ Configuration", expanded=not config.watchlist):
            for w in config.warnings:
                st.warning(w)

# La mise a jour globale s'execute AVANT le rendu de la page pour que les
# tableaux affiches soient deja frais.
if btn_global:
    afficher_compte_rendu(run_update(update_global, "Mise a jour globale"))

navigation.run()

# ==========================================================================
# EXPORT GLOBAL (.md) — bouton dans le menu, rempli ICI (fin de script)
# pour inclure le briefing ET le diagnostic generes durant ce meme rerun.
# Un seul document Markdown, pense pour etre reanalyse par Claude ensuite.
# ==========================================================================
try:
    # Le briefing et le diagnostic vivent en base : l'export reste complet meme
    # si la session courante n'a pas ouvert les pages concernees (reconnexion,
    # autre appareil, export lance depuis la page Donnees).
    _synth_g = st.session_state.get("synth_global")
    _synth_i = st.session_state.get("synth_instruments")
    if not _synth_g:
        _bc = db.get_briefing_cache()
        if _bc and _bc.get("global"):
            _synth_g = _bc["global"]
            try:
                _synth_i = json.loads(_bc.get("instruments") or "{}")
            except Exception:
                _synth_i = {}
    _diag_res = st.session_state.get("diag_result") or _diag_depuis_ligne(db.dernier_diagnostic())
    _md_export = construire_export_md(
        config,
        synth_global=_synth_g,
        synth_instruments=_synth_i,
        diag_result=_diag_res,
        idees_candidats=st.session_state.get("idees_candidats"),
    )
    export_slot.download_button(
        "⬇️ Exporter (.md)",
        data=_md_export.encode("utf-8"),
        file_name=f"sam_invest_export_{datetime.now():%Y%m%d_%H%M}.md",
        mime="text/markdown",
        use_container_width=True,
        disabled=not config.watchlist,
        help="Exporte toutes les donnees (Donnees, News, Briefing, VN Diagnostic, "
             "Suggestions) en un "
             "Markdown unique, pret a coller a Claude pour analyse.",
    )
except Exception as e:  # l'export ne doit jamais casser l'app
    log(f"[UI] export .md indisponible: {type(e).__name__}: {e}", "error")

# Note : suivi de portefeuille (PRU, allocation, DCA) retire -> outil de watchlist.
# Note : fonctionnalite email SMTP reportee (choix utilisateur).
