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

Quatre pages, nommees par la QUESTION a laquelle elles repondent plutot que par
l'etage du pipeline qui les alimente. Chacune porte son bouton de mise a jour,
plus une mise a jour globale dans le menu (consommation d'API maitrisee).
  - Aujourd'hui    : cours + signaux + echeances, puis le fil d'actualites
                     (Haiku pour le classement). Les cours sont gratuits.
  - Ma liste       : edition de la liste + suggestions d'ajout (Sonnet, au clic).
  - Une entreprise : la fiche complete d'UNE valeur — cours, actualites, chiffres,
                     analystes — puis l'analyse approfondie (Opus, au clic).
  - Ma synthese    : la lecture redigee par Claude, et elle seule (Sonnet, au clic).

Les alertes (deterministes, gratuites) ne vivent sur aucune page : elles sont
rassemblees dans le menu ☰, avec une croix par alerte et un « Tout supprimer ».
Supprimer = masquer jusqu'a la prochaine actualisation des cours (voir
rendre_menu_alertes et db.masquer_flags) : un flag est recalcule a chaque
affichage, il ne peut pas etre efface.

Quand la watchlist est vide, la navigation est court-circuitee au profit d'un
ecran de demarrage : rien d'autre n'a de sens tant qu'aucune valeur n'est suivie.

Separation stricte : chiffres/signaux = code deterministe ; texte = Claude (llm.py).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from sam_invest import db, llm, rules, signals
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

REGLE_LABEL = {"chute": "chute brutale", "technique": "signal technique",
               "degradation": "degradation", "evenement": "evenement",
               "revision": "revision d'estimations"}


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


# ==========================================================================
# MENU DES ALERTES (dans le menu burger) — UN SEUL endroit pour tous les flags
# --------------------------------------------------------------------------
# Les alertes vivaient sur « Ma synthese », a deux endroits : un bloc « Vue
# d'ensemble » en tete de page, et les MEMES flags repetes plus bas dans chaque
# fiche d'instrument. La page melangeait donc deux lectures — le texte redige par
# Claude et la surveillance chiffree — en affichant deux fois la meme alerte.
#
# Elles sont desormais rassemblees ici, dans le menu, qui porte deja tout ce qui
# vaut pour la watchlist entiere (mise a jour globale, export, fraicheur) et
# reste atteignable depuis les six pages. « Ma synthese » ne contient plus que la
# lecture de Claude.
#
# « Supprimer » = MASQUER, jamais effacer : un flag n'est pas une ligne stockee,
# rules.tous_les_flags le RECALCULE a chaque affichage. Le masquage est donc un
# report qui expire a la prochaine actualisation des cours (cf. db.masquer_flags).
# C'est ecrit en toutes lettres sous la liste : sans ca, la croix passerait pour
# definitive et une vraie degradation pourrait etre manquee.
# ==========================================================================
def flag_cle(f: dict) -> str:
    """Identite d'un flag — la meme que celle historisee par db.enregistrer_flags."""
    return f"{f['ticker']}|{f['regle']}|{f['severite']}"


def flag_est_nouveau(f: dict, anc: dict, asof: str | None) -> bool:
    """Vrai si le flag est apparu lors de la DERNIERE mise a jour des donnees."""
    a = anc.get(flag_cle(f))
    return bool(a and asof and a["premiere_vue"] == asof)


def rendre_menu_alertes(flags: list[dict]) -> None:
    """Menu repliable des alertes, avec « Tout supprimer » et une croix par ligne."""
    anc = db.anciennete_flags()
    asof = (db.last_update("donnees") or {}).get("asof")
    masquees = db.cles_masquees(asof)
    maintenant = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _nouveau(f: dict) -> bool:
        return flag_est_nouveau(f, anc, asof)

    visibles = [f for f in flags if flag_cle(f) not in masquees]
    alertes = [f for f in visibles if f["severite"] == "alerte"]
    infos = [f for f in visibles if f["severite"] == "info"]
    n_masquees = len(masquees & {flag_cle(f) for f in flags})

    # Le libelle porte le compte : sur telephone le menu est ferme par defaut, et
    # c'est la seule chose qu'on lit avant de decider de l'ouvrir.
    if alertes:
        label = f"🔔 {len(alertes)} alerte(s)"
    elif infos:
        label = f"🟡 {len(infos)} info(s)"
    elif n_masquees:
        label = f"🔕 {n_masquees} alerte(s) masquee(s)"
    else:
        label = "✅ Aucune alerte"

    with st.expander(label, expanded=False):
        if not visibles and not n_masquees:
            st.caption("Aucun signal en cours sur tes valeurs.")
            return

        if visibles:
            if st.button("🗑️ Tout supprimer", use_container_width=True,
                         key="alertes_tout_supprimer",
                         help="Masque toutes les alertes ci-dessous."):
                db.masquer_flags([flag_cle(f) for f in visibles], asof, maintenant)
                st.rerun()

        # Alertes d'abord, infos ensuite : deux severites, un seul menu.
        #
        # Chaque alerte tient sur deux lignes : une EN-TETE COURTE (ticker +
        # regle) posee a cote de la croix, puis le detail chiffre en dessous, sur
        # toute la largeur. Le message complet ne peut pas cohabiter avec un
        # bouton dans une sidebar de ~240 px : il repoussait la croix sur sa
        # propre ligne, en gros bouton pleine largeur. Cette forme se scanne
        # aussi mieux quand il y a dix alertes : on lit des tickers, pas de la
        # prose. Le detail reste affiche : un flag doit TOUJOURS montrer la
        # valeur observee et son seuil.
        for f in alertes + infos:
            cle = flag_cle(f)
            puce = "🔴" if f["severite"] == "alerte" else "🟡"
            neuf = "🆕 " if _nouveau(f) else ""
            regle = REGLE_LABEL.get(f["regle"], f["regle"])
            # Le message commence par « TICKER : » ; l'en-tete le porte deja.
            detail = f["message"]
            prefixe = f"{f['ticker']} : "
            if detail.startswith(prefixe):
                detail = detail[len(prefixe):]
            depuis = (anc.get(cle) or {}).get("premiere_vue")
            if depuis and not _nouveau(f):
                detail += f" · depuis le {fmt_dt(depuis)}"
            with st.container(horizontal=True, vertical_alignment="center"):
                if st.button("✕", key=f"alerte_x_{cle}", width=40,
                             help="Supprimer cette alerte"):
                    db.masquer_flags([cle], asof, maintenant)
                    st.rerun()
                # Largeur explicite : sans elle le markdown reclame toute la
                # place et fait passer la croix a la ligne.
                st.markdown(f"{puce} {neuf}**{f['ticker']}** — {regle}", width=168)
            st.caption(detail)

        # Le compte des masquees n'est rappele ici que s'il reste des alertes
        # visibles : quand tout est masque, le libelle du menu le dit deja.
        if n_masquees and visibles:
            st.caption(f"🔕 {n_masquees} alerte(s) masquee(s) pour l'instant.")
        st.caption("Une alerte supprimee revient a la prochaine actualisation des "
                   "cours, si la situation qui l'a declenchee tient toujours.")


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
    """Badge de reco, precede de sa mise en garde.

    La mise en garde est du TEXTE VISIBLE, au-dessus du badge, et non une
    infobulle : sur telephone il n'y a pas de survol, et elle doit etre lue
    AVANT la recommandation — a fortiori si l'app est ouverte a d'autres
    personnes, qui n'ont pas le contexte de celui qui l'a construite.
    """
    entree = RECO_LABEL.get(reco or "")
    if entree:
        mot, couleur = entree
        st.caption("Lecture des chiffres et des actualites par Claude — "
                   "**ce n'est pas un conseil en investissement.** "
                   "La decision reste la tienne.")
        st.badge(mot, color=couleur)


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


# --------------------------------------------------------------------------
# ACTUALITES : un fil date sur "Aujourd'hui", un bloc sur la fiche entreprise
# --------------------------------------------------------------------------
# L'ancienne page News redressait une SECONDE liste de la watchlist, un volet
# par valeur. Les actualites ne sont pas une facon d'enumerer le portefeuille :
# elles sont datees. Elles vivent donc en fil chronologique la ou on demande
# « quoi de neuf ? », et par entreprise sur la fiche de l'entreprise.
# --------------------------------------------------------------------------
def _analyses_news(ticker: str) -> dict:
    """Classement Claude des news d'un ticker, indexe par titre original. {} si absent."""
    na = db.get_news_analysis(ticker)
    if not na or not na.get("payload"):
        return {}
    try:
        return {a.get("headline", ""): a for a in json.loads(na["payload"])}
    except Exception:
        return {}


def bloc_news_entreprise(ticker: str) -> None:
    """Toutes les actualites connues d'UNE entreprise."""
    raw = db.get_news(ticker)
    if not raw:
        st.caption("Aucune actualite en base pour cette entreprise. "
                   "Lance « Actualiser les actualites » sur Aujourd'hui.")
        return
    analyses = _analyses_news(ticker)
    for i, n in enumerate(raw):
        if i:
            st.divider()
        rendre_news(n, analyses.get(n.get("headline", "")), compact=False)


def fil_actualites(limite: int = 12) -> None:
    """Fil chronologique tous instruments confondus, du plus recent au plus ancien."""
    items = []
    for inst in config.watchlist:
        analyses = _analyses_news(inst.ticker)
        for n in db.get_news(inst.ticker) or []:
            items.append((n.get("datetime", ""), inst.ticker, n,
                          analyses.get(n.get("headline", ""))))
    if not items:
        st.caption("Aucune actualite en base. Clique sur « Actualiser les actualites ».")
        return
    items.sort(key=lambda x: x[0], reverse=True)
    for i, (_, ticker, n, a) in enumerate(items[:limite]):
        if i:
            st.divider()
        st.caption(f"**{ticker}**")
        rendre_news(n, a, compact=False)
    if len(items) > limite:
        st.caption(f"{len(items) - limite} actualite(s) plus ancienne(s) : "
                   "elles restent sur la fiche de chaque entreprise.")


def titre_page(icone: str, titre: str, accroche: str) -> None:
    """En-tete compact de page : une ligne de titre + une ligne de contexte.

    L'accroche dit ce qu'on vient CHERCHER sur la page, pas comment elle est
    fabriquee : ni nom de modele, ni cout, ni source. Ce detail vit dans l'Aide,
    et le renvoi systematique vers celle-ci a ete retire — une page qui a besoin
    de pointer son mode d'emploi a chaque en-tete ne s'explique pas toute seule.
    """
    st.markdown(f"### {icone} {titre}")
    st.caption(accroche)


# ==========================================================================
# PAGES (une seule est rendue par run -> page legere sur mobile)
# ==========================================================================
# --------------------------------------------------------------------------
# PAGE DONNEES : prix + fondamentaux + signaux (aucun appel Claude)
# --------------------------------------------------------------------------
def page_donnees():
    titre_page("📈", "Aujourd'hui", "Ce qui a bouge sur tes valeurs, et l'actualite du jour.")
    if st.button("🔄 Actualiser les cours", use_container_width=True,
                 disabled=not config.watchlist):
        afficher_compte_rendu(run_update(update_donnees, "Mise a jour des donnees"))
    caption_derniere_maj("donnees", "cours")

    st.subheader("Mes valeurs")
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
        st.caption("Colonnes vides = lance une actualisation des cours.")
    else:
        st.warning("Aucune valeur suivie : ajoute-en dans « Ma liste ».")

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

    # --- Actualites du jour (ex-page News) ---
    st.subheader("📰 Actualites recentes")
    if st.button("🔄 Actualiser les actualites", use_container_width=True,
                 disabled=not config.watchlist):
        afficher_compte_rendu(run_update(update_news, "Mise a jour des news"))
    caption_derniere_maj("news", "actualites")
    if not config.secrets.anthropic_api_key:
        st.info("Sans cle Claude, les actualites s'affichent en clair mais ne sont "
                "ni classees ni traduites.")
    fil_actualites()


# --------------------------------------------------------------------------
# PAGE UNE ENTREPRISE : la fiche d'une valeur SUIVIE
# --------------------------------------------------------------------------
# Portee : la watchlist, et elle seule. Tout ce que cette page affiche (cours,
# chiffres cles, analystes, actualites) sort de la base locale, qui n'existe que
# parce que le ticker est suivi et actualise. C'est de la SURVEILLANCE.
#
# L'analyse approfondie, elle, s'applique a n'importe quelle societe cotee et ne
# suppose aucun etat prealable : c'est de l'EXPLORATION. Deux portees, deux
# activites, donc deux pages (voir page_analyser).
# --------------------------------------------------------------------------
def bloc_marche(choix: str) -> None:
    """Cours, indicateurs, fondamentaux et avis d'analystes d'une valeur suivie.

    Reserve aux valeurs de la watchlist : c'est la seule base qui alimente
    l'historique de prix et les fondamentaux en local.
    """
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
        with st.spinner(f"Cours de {choix} pas a jour : recuperation automatique..."):
            cr_auto = update_donnees_instrument(config, choix)
        if cr_auto.get("status") == "ok":
            st.caption(f"✅ Cours de {choix} recuperes a l'instant.")
        else:
            st.warning(f"Impossible de recuperer les cours de {choix} "
                       "(reseau/source ?). Reessaie via « Actualiser les cours ».")
    q_sel = db.get_quote(choix)
    if q_sel and q_sel.get("asof"):
        st.caption(f"🕒 Cours de {choix} : maj {fmt_dt(q_sel['asof'])}.")

    # --- Sous-partie 1 : cours ---
    st.markdown("#### Son cours")
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
        st.caption("Pas encore d'historique. Lance « Actualiser les cours » sur Aujourd'hui.")

    # --- Sous-partie 2 : fondamentaux ---
    st.markdown("#### Ses chiffres cles")
    afficher_fondamentaux(choix)

    # --- Sous-partie 3 : avis des analystes (actions uniquement) ---
    type_choix = next((i.type for i in config.watchlist if i.ticker == choix), "action")
    if type_choix.lower() == "action":
        st.markdown("#### Ce qu'en disent les analystes")
        afficher_avis_analystes(choix)


def page_instrument():
    titre_page("🔎", "Une entreprise",
               "Le detail d'une valeur que tu suis : cours, chiffres, actualites.")
    noms = {i.ticker: i.nom for i in config.watchlist}
    if not noms:
        st.warning("Aucune valeur suivie. Ajoute-en dans « Ma liste ».")
        return

    choix = st.selectbox("Entreprise", list(noms),
                         format_func=lambda t: f"{t} — {noms[t]}",
                         key="entreprise_ticker")
    bloc_marche(choix)

    # Replie par defaut : les actualites d'une valeur representent l'essentiel du
    # poids de la page (une dizaine d'articles avec leur resume). On vient le plus
    # souvent voir un cours ; les lire est un second geste.
    nb_news = len(db.get_news(choix) or [])
    with st.expander(f"📰 Ses actualites ({nb_news})" if nb_news else "📰 Ses actualites"):
        bloc_news_entreprise(choix)

    # Passerelle vers l'exploration : l'analyse s'applique a n'importe quelle
    # societe cotee, elle n'a donc pas sa place sur une fiche de suivi.
    st.divider()
    try:
        st.page_link(PAGE_ANALYSER, label="Analyser les comptes de cette entreprise",
                     use_container_width=True)
    except Exception:  # rendu degrade si l'objet de page n'est pas disponible
        st.caption("🔬 Pour une analyse financiere complete, va sur « Analyser ».")
    st.caption("L'analyse financiere complete vit sur sa propre page : elle "
               "s'applique a toute entreprise cotee, pas seulement a celles que "
               "tu suis.")


# --------------------------------------------------------------------------
# PAGE MA SYNTHESE : alertes (gratuites) + synthese redigee (a la demande)
# --------------------------------------------------------------------------
def page_briefing():
    titre_page("🧠", "Ma synthese",
               "La lecture d'ensemble de tes valeurs, ecrite pour toi.")

    # La synthese reprend cours et actualites : on verifie leur fraicheur.
    donnees_fraiches, asof_donnees = fraicheur("donnees")
    news_fraiches, asof_news = fraicheur("news")
    manquants = ([] if donnees_fraiches else ["cours"]) + ([] if news_fraiches else ["actualites"])

    # --- UN SEUL bouton, qui fait TOUT ce qu'il faut ---------------------------
    # Il y en avait deux : « Ecrire ma synthese », qui REFUSAIT d'ecrire des que
    # les donnees avaient plus de FRAICHEUR_MAX_H heures, et un second bouton
    # (affiche seulement dans ce cas) qui actualisait puis ecrivait. Sur telephone
    # c'est le premier qu'on lit et qu'on touche, et il ne repond jamais : les
    # cours ont presque toujours plus de deux heures quand on rouvre l'app en
    # transports. Un bouton qui ne rend pas le service qu'annonce son libelle est
    # un bouton casse, meme quand il affiche un avertissement en dessous.
    # Desormais le libelle ANNONCE le travail reel, et le bouton le fait.
    btn_synthese = st.button(
        "🧠 Ecrire ma synthese" if not manquants
        else f"🧠 Actualiser {' + '.join(manquants)} puis ecrire ma synthese",
        use_container_width=True,
        disabled=not config.watchlist or not config.secrets.anthropic_api_key,
    )

    def _tag_fraicheur(frais: bool, asof: str | None) -> str:
        return f"**{fmt_dt(asof) if asof else 'jamais'}**" + ("" if frais else " ⚠️")

    # La duree est annoncee AVANT le clic, en texte visible : une redaction dure
    # 2 a 3 minutes, et sur telephone on referme l'ecran bien avant si personne ne
    # l'a dit. Pas d'infobulle : il n'y a pas de survol sur un telephone.
    st.caption(
        f"🕒 Cours : {_tag_fraicheur(donnees_fraiches, asof_donnees)} · "
        f"Actualites : {_tag_fraicheur(news_fraiches, asof_news)} "
        f"(⚠️ = plus vieux que {FRAICHEUR_MAX_H} h). "
        + ("⏱️ Compte 3 a 4 min et garde l'app ouverte."
           if manquants else "⏱️ Compte 2 a 3 min et garde l'app ouverte.")
    )

    # --- Le compte rendu du dernier clic, rejoue APRES le rerun ---------------
    # La generation se termine par st.rerun() (pour que la page se redessine avec
    # les donnees fraiches). Or un rerun efface tout ce qui a ete ecrit pendant le
    # run : les st.error / st.info emis par _generer_briefing ne parvenaient
    # jamais a l'ecran. Un echec Claude se traduisait donc, cote utilisateur, par
    # « j'appuie et il ne se passe rien ». On fait transiter le message par
    # session_state pour qu'il survive au rerun, et on l'affiche une fois.
    for _niveau, _texte in st.session_state.pop("synthese_messages", []):
        getattr(st, _niveau)(_texte)

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

    def _message(niveau: str, texte: str) -> None:
        """Empile un message a afficher au prochain run (survit au st.rerun)."""
        st.session_state.setdefault("synthese_messages", []).append((niveau, texte))

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
            _message("info", f"ℹ️ Donnees et news inchangees depuis la derniere synthese "
                             f"(ecrite le {fmt_dt(_cache['generated_at'])}) : texte recupere "
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
            _message("success", "✅ Synthese ecrite. Elle est enregistree : tu la "
                                "retrouveras meme apres une coupure ou depuis un autre "
                                "appareil.")
        else:
            _message("error",
                     "❌ Claude n'a pas repondu : la synthese n'a pas pu etre ecrite. "
                     "Le plus souvent c'est le reseau qui a lache pendant la redaction "
                     "(elle dure 2 a 3 minutes) — reessaie, les cours et actualites "
                     "recuperes, eux, sont deja enregistres. Detail dans "
                     "`data/sam_invest.log`.")
            log("[UI] briefing = None -> rien a afficher", "error")

    # --- Le clic : actualiser ce qui manque, PUIS ecrire. Toujours les deux. ---
    # Le rerun final redessine la page avec les donnees fraiches ; les messages du
    # run precedent sont rejoues plus haut, donc rien ne se perd en route.
    if btn_synthese:
        log(f"[UI] clic 'Ecrire ma synthese' (a actualiser: {manquants or 'rien'})")
        for _besoin, _fn, _lib in (("cours", update_donnees, "Mise a jour des donnees"),
                                   ("actualites", update_news, "Mise a jour des news")):
            if _besoin in manquants:
                _cr = run_update(_fn, _lib)
                if _cr.get("status") != "ok":
                    # On ecrit quand meme la synthese : mieux vaut une lecture sur des
                    # chiffres un peu vieux, en le disant, que rien du tout.
                    _message("warning", f"⚠️ Actualisation des {_besoin} incomplete "
                                        f"({_cr.get('resume', 'raison inconnue')}). La synthese "
                                        "est ecrite sur les dernieres donnees disponibles.")
        _generer_briefing()
        st.rerun()

    # =====================================================================
    # SECTION GLOBAL (big picture)
    # =====================================================================
    st.markdown("## 🌍 Global")

    # Les alertes ne sont plus ici : elles vivent dans le menu ☰ (voir
    # rendre_menu_alertes). Cette page ne porte plus que la lecture redigee par
    # Claude — melanger le texte et la surveillance chiffree revenait a afficher
    # deux fois la meme alerte sur un seul ecran.
    if st.session_state.get("synth_global"):
        if st.session_state.get("synthese_asof"):
            st.caption(f"Synthese basee sur les donnees du {fmt_dt(st.session_state['synthese_asof'])}.")
        st.markdown(st.session_state["synth_global"])
    elif config.secrets.anthropic_api_key:
        st.caption("Clique sur « Ecrire ma synthese » en haut de la page pour la vue "
                   "d'ensemble et les commentaires valeur par valeur.")
    else:
        st.info("ANTHROPIC_API_KEY absente : briefing desactive, mais les donnees "
                "par instrument ci-dessous restent valables, et les alertes du "
                "menu ☰ aussi (elles sont calculees par le code, sans IA).")

    # =====================================================================
    # SECTION PAR INSTRUMENT
    # =====================================================================
    st.markdown("## 📋 Valeur par valeur")
    snaps_by = {s.instrument.ticker: s for s in signals.construire_snapshots(config)}
    evby = {e["ticker"]: e for e in data.get("evenements", [])}
    synth_inst = st.session_state.get("synth_instruments") or {}
    log(f"[UI] rendu 'Par instrument': synth_inst={len(synth_inst)} cles, "
        f"watchlist={len(config.watchlist)}, "
        f"cles_communes={len([i for i in config.watchlist if i.ticker in synth_inst])}")

    if not synth_inst and config.secrets.anthropic_api_key:
        st.info("💡 Le commentaire valeur par valeur apparait apres "
                "« Ecrire ma synthese » en haut de la page. Les chiffres et les "
                "actualites ci-dessous sont deja disponibles sans appel Claude ; "
                "les alertes sont dans le menu ☰.")

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
    #
    # Les flags ne sont plus AFFICHES ici mais continuent de porter le tri : ils
    # sont deja calcules pour le menu (FLAGS_COURANTS), donc cela ne coute rien.
    # Le masquage est volontairement ignore : cacher une alerte du menu ne veut
    # pas dire que la valeur cesse de meriter d'etre regardee en premier.
    _flags_by: dict[str, list] = {}
    for _f in FLAGS_COURANTS:
        _flags_by.setdefault(_f["ticker"], []).append(_f)
    _anc = db.anciennete_flags()
    _asof_don = (db.last_update("donnees") or {}).get("asof")

    def _tri_instrument(inst):
        fl = _flags_by.get(inst.ticker, [])
        has_al = any(f["severite"] == "alerte" for f in fl)
        nouvelle_al = any(flag_est_nouveau(f, _anc, _asof_don)
                          for f in fl if f["severite"] == "alerte")
        sev_rank = 0 if nouvelle_al else (1 if has_al else (2 if fl else 3))
        reco = (synth_inst.get(inst.ticker) or {}).get("reco", "")
        reco_rank = {"vendre": 0, "garder": 1, "achat": 2}.get(reco, 3)
        return (sev_rank, reco_rank, inst.ticker)

    for inst in sorted(config.watchlist, key=_tri_instrument):
        t = inst.ticker
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
                st.caption("📝 Pas encore de commentaire — clique « Ecrire ma synthese » "
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
            # Les flags de cet instrument ne sont plus repetes ici : ils etaient
            # deja affiches en tete de page, et le sont maintenant dans le menu ☰.
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
# ANALYSE APPROFONDIE : chiffres = code, conclusions = Claude (page Analyser)
# Affichage PROGRESSIF (pas d'effet tunnel) : chiffres instantanes + conclusions
# streamees par etape ; conclusion generale remplie en haut a la fin.
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
        valeur = f"⚠️ {ligne['valeur']}" if ligne.get("doute") else ligne["valeur"]
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
        st.caption(f"⚠️ {etape['note']}")


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
        st.caption(f"💾 Analyse conservee, faite le **{fmt_dt(genere)}** "
                   "(aucun appel Claude pour l'afficher).")
    if r.get("statut") == "partiel":
        st.warning("⚠️ Analyse incomplete : la generation a ete interrompue (coupure "
                   "reseau ?). Les etapes ci-dessous sont conservees ; relance "
                   "« Refaire l'analyse » pour la version complete.")
    if r.get("resume"):
        st.markdown("### Conclusion generale")
        st.caption("🤖 Ecrite par Claude")
        afficher_badge_reco(r.get("reco", ""))
        st.markdown(r["resume"])
    st.divider()
    d_exo = _fmt_date_iso(diag.get("date_reference"))
    d_maj = _fmt_date_iso(diag.get("date_recuperation"))
    for etape in diag["etapes"]:
        _rendre_etape_chiffres(etape, d_exo, d_maj)
        concl = r["conclusions"].get(etape["id"])
        if concl:
            st.caption("🤖 Conclusion ecrite par Claude")
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


def section_analyse(ticker: str) -> None:
    """Analyse financiere approfondie d'UNE entreprise, a la demande.

    Rendue par page_analyser. Une analyse deja produite se rouvre telle quelle,
    sans nouvel appel paye.
    """
    stocke = _diag_depuis_ligne(db.get_diagnostic(ticker))
    if not config.secrets.anthropic_api_key:
        st.info("Cle Claude absente : l'analyse approfondie n'est pas disponible.")
        if stocke:
            _rendre_diag_statique(stocke)
        return

    if stocke:
        c1, c2 = st.columns([3, 1])
        relancer = c1.button("🔄 Refaire l'analyse", use_container_width=True,
                             help="Reprend les comptes les plus recents. Compte une minute.")
        if c2.button("🗑️ Oublier", use_container_width=True,
                     help="Supprime l'analyse conservee pour cette entreprise"):
            db.supprimer_diagnostic(ticker)
            st.rerun()
        lancer = relancer
    else:
        st.caption("Sept etapes — marges, rentabilite, creation de valeur, solidite, "
                   "cash, croissance, valorisation — puis une conclusion ecrite. "
                   "Compte environ une minute.")
        lancer = st.button("🔬 Analyser cette entreprise", use_container_width=True,
                           type="primary")

    if lancer:
        _lancer_analyse(ticker)
    elif stocke:
        _rendre_diag_statique(stocke)


# --------------------------------------------------------------------------
# PAGE ANALYSER : l'analyse financiere de N'IMPORTE QUELLE entreprise cotee
# --------------------------------------------------------------------------
# Portee volontairement DIFFERENTE de « Une entreprise » : ici on explore le
# marche entier, sans rien supposer d'une watchlist. Aucune donnee locale n'est
# requise — les comptes sont recuperes en direct. C'est aussi ce qui alimente la
# decision de suivre une valeur : d'ou le bouton d'ajout apres l'analyse.
# --------------------------------------------------------------------------
def selecteur_analyse() -> str | None:
    """Entreprise a analyser. La recherche ouvre tout le marche ; les valeurs
    suivies et les analyses deja faites sont proposees en raccourci."""
    noms = {}
    for d in db.list_diagnostics():                    # deja analysees d'abord
        noms[d["ticker"]] = d.get("nom") or d["ticker"]
    for i in config.watchlist:                         # puis les valeurs suivies
        noms.setdefault(i.ticker, i.nom)
    noms.update(st.session_state.get("analyse_trouvees") or {})

    with st.form("form_analyse_recherche", clear_on_submit=False):
        q = st.text_input("Quelle entreprise ?", key="analyse_q",
                          placeholder="ex : Peugeot, NVDA, Alibaba, ASML...")
        btn = st.form_submit_button("🔎 Chercher", use_container_width=True)
    if btn and q.strip():
        with st.spinner("Recherche..."):
            st.session_state["analyse_results"] = search_instruments(
                q.strip(), max_results=8,
                finnhub_key=config.secrets.finnhub_api_key)

    res = st.session_state.get("analyse_results")
    if res:
        opts = {libelle_resultat(r): r for r in res}
        pick = st.selectbox("Resultat de la recherche", list(opts), key="analyse_pick")
        if st.button("Choisir cette entreprise", use_container_width=True):
            r = opts[pick]
            trouvees = dict(st.session_state.get("analyse_trouvees") or {})
            trouvees[r["symbol"]] = r["nom"]
            st.session_state["analyse_trouvees"] = trouvees
            st.session_state["analyse_ticker"] = r["symbol"]
            st.session_state["analyse_results"] = None
            st.rerun()
    elif res == []:
        st.caption("Aucun resultat. Essaie un autre nom, ou le ticker exact (ex : NVDA).")

    if not noms:
        return None
    return st.selectbox("Entreprise a analyser", list(noms),
                        format_func=lambda t: f"{t} — {noms[t]}",
                        key="analyse_ticker")


def page_analyser():
    titre_page("🔬", "Analyser",
               "L'analyse financiere complete de n'importe quelle entreprise cotee.")
    ticker = selecteur_analyse()
    if not ticker:
        st.info("Cherche une entreprise ci-dessus pour l'analyser. Pas besoin de la "
                "suivre : l'analyse fonctionne sur toute societe cotee.")
        return

    st.divider()
    section_analyse(ticker)

    # L'analyse sert souvent a decider si on suit la valeur : le geste est ici.
    if not any(i.ticker == ticker for i in config.watchlist):
        st.divider()
        nom = (st.session_state.get("analyse_trouvees") or {}).get(ticker, ticker)
        if st.button(f"➕ Suivre {ticker} au quotidien", use_container_width=True):
            rows = [{"ticker": i.ticker, "nom": i.nom, "type": i.type, "theme": i.theme}
                    for i in config.watchlist]
            with st.spinner("Ajout a ta liste..."):
                rows.append({"ticker": ticker, "nom": nom, "type": "action",
                             "theme": suggest_theme(ticker, "action")})
                save_watchlist(rows)
            st.success(f"✅ {ticker} ajoute. Ses cours et actualites se rempliront "
                       "a la prochaine actualisation.")
            st.rerun()
        st.caption("Tu ne suis pas encore cette entreprise : l'ajouter fera "
                   "apparaitre son cours, ses actualites et sa fiche.")


def _lancer_analyse(ticker: str) -> None:
    """Genere l'analyse etape par etape, en ecrivant en base au fur et a mesure."""
    with st.spinner("Recuperation des comptes..."):
        diag = construire_diagnostic(config, ticker)
    if "erreur" in diag:
        st.error(diag["erreur"])
        return

    genere_le = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _entete_diag(diag)
    summary_ph = st.empty()  # conclusion generale EN HAUT, remplie a la fin
    summary_ph.info("Conclusion generale : ecrite apres les etapes ci-dessous...")
    st.divider()
    conclusions = {}
    d_exo = _fmt_date_iso(diag.get("date_reference"))
    d_maj = _fmt_date_iso(diag.get("date_recuperation"))
    # Les chiffres seuls valent deja d'etre conserves : on ecrit avant meme la
    # 1re conclusion, puis apres chaque etape (resilience reseau).
    _sauver_diag(diag, conclusions, "", genere_le, "partiel")
    for etape in diag["etapes"]:
        _rendre_etape_chiffres(etape, d_exo, d_maj)
        st.caption("🤖 Conclusion ecrite par Claude")
        conclusions[etape["id"]] = st.write_stream(
            llm.conclusion_etape_stream(config.secrets, etape["titre"],
                                        etape["lignes"], etape.get("note", ""))
        )
        _sauver_diag(diag, conclusions, "", genere_le, "partiel")
    with summary_ph.container():
        st.markdown("### Conclusion generale")
        st.caption("🤖 Ecrite par Claude")
        resume = st.write_stream(
            llm.exec_summary_diagnostic_stream(config.secrets, diag, conclusions)
        )
    # Claude termine par une ligne « RECO: ACHAT ». On la detache une fois le flux
    # fini, puis on re-rend le bloc avec le badge EN TETE : pendant le streaming
    # l'utilisateur lit l'analyse, pas un marqueur technique.
    reco_diag, resume = llm.extraire_reco_resume(resume)
    if reco_diag:
        with summary_ph.container():
            st.markdown("### Conclusion generale")
            st.caption("🤖 Ecrite par Claude")
            afficher_badge_reco(reco_diag)
            st.markdown(resume)
    _sauver_diag(diag, conclusions, resume, genere_le, "complet", reco_diag)


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
    titre_page("✏️", "Ma liste",
               "Les valeurs que tu suis : en ajouter, en retirer, les reclasser.")
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

    st.markdown(f"##### 📋 Ma liste actuelle — {len(config.watchlist)} instrument(s)")

    if not config.watchlist:
        st.info("Liste vide. Utilise la recherche ci-dessus ou « Ajouter "
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

    with st.expander("📄 Les cinq pages"):
        st.markdown(
            "Quatre pages parlent des valeurs **que tu suis**. La cinquieme, "
            "**Analyser**, s'applique a **n'importe quelle entreprise cotee** — "
            "c'est la difference de portee qui justifie qu'elle soit a part.\n\n"
            "**📈 Aujourd'hui** — Ce qui a bouge sur tes valeurs : cours et signaux, "
            "echeances a venir, puis le fil des actualites recentes. C'est la page "
            "d'accueil parce que c'est la question qu'on se pose en ouvrant l'app.\n\n"
            "**✏️ Ma liste** — Les valeurs que tu suis : ajouter, retirer, "
            "re-thematiser, et les suggestions d'ajout. C'est par la qu'on commence.\n\n"
            "**🔎 Une entreprise** — Le detail d'UNE valeur suivie : son cours, ses "
            "chiffres cles, l'avis des analystes, ses actualites. Tout vient de la "
            "base locale, donc uniquement pour les valeurs de ta liste.\n\n"
            "**🧠 Ma synthese** — La lecture d'ensemble de tes valeurs, ecrite par "
            "Claude, avec une recommandation par valeur.\n\n"
            "**🔬 Analyser** — L'analyse financiere complete de **toute entreprise "
            "cotee**, suivie ou non : sept etapes (marges, rentabilite, creation de "
            "valeur, solidite, cash, croissance, valorisation) puis une conclusion "
            "ecrite. Rien n'a besoin d'etre en base : les comptes sont recuperes en "
            "direct. C'est aussi la page ou l'on decide de suivre une valeur — le "
            "bouton d'ajout est juste sous l'analyse.\n\n"
            "_Le deroule en etapes suit la methode d'analyse financiere de "
            "**Veronique Nguyen**._"
        )

    with st.expander("🎨 Codes couleur et symboles"):
        st.markdown(
            "**Recommandation** — le mot est toujours ecrit en toutes lettres, "
            "la couleur ne fait que le renforcer :\n"
            "- **ACHAT** (vert) — le dossier parait attractif au niveau actuel\n"
            "- **GARDER** (orange) — rien qui justifie d'agir\n"
            "- **VENDRE** (rouge) — signaux de degradation\n\n"
            "C'est une lecture des chiffres et des actualites, **pas un ordre** : elle "
            "s'accompagne toujours des arguments qui l'ont produite, a lire avant de "
            "decider quoi que ce soit.\n\n"
            "**Alertes (calculees par le code, sans IA)** — toutes regroupees dans "
            "le menu ☰, le meme depuis n'importe quelle page :\n"
            "- 🔴 **Alerte** — chute brutale, degradation, signal technique fort\n"
            "- 🟡 **Info** — a noter, sans urgence\n"
            "- 🆕 — alerte apparue lors de la **derniere** actualisation ; sans le "
            "badge, elle est persistante (deja signalee, avec sa date d'apparition)\n"
            "- **✕** masque une alerte, **🗑️ Tout supprimer** les masque toutes. "
            "Elles ne sont pas effacees pour autant : une alerte est **recalculee** "
            "a partir des cours a chaque affichage, donc elle revient a la prochaine "
            "actualisation si la situation qui l'a declenchee tient toujours.\n\n"
            "**Actualites** : 🟢 positif · ⚪ neutre · 🔴 negatif.\n\n"
            "**Analyse approfondie** : ⚠️ signale un chiffre a verifier — aberration "
            "comptable, ratio fausse par un ecart de devise, ou ratio peu pertinent "
            "pour une societe financiere.\n\n"
            "**Fraicheur** : ⚠️ a cote d'une date = plus vieux que "
            f"{FRAICHEUR_MAX_H} heures."
        )

    with st.expander("💸 Ce que coute chaque bouton"):
        st.markdown(
            "L'app appelle Claude sur certains boutons seulement. Ordre de grandeur :\n\n"
            "| Bouton | Duree | Cout |\n|---|---|---|\n"
            "| Actualiser les cours | ~30 s pour 20 valeurs | gratuit |\n"
            "| Actualiser les actualites | ~30 s pour 20 valeurs | tres faible |\n"
            "| Ecrire ma synthese | **2 a 3 min** (+1 min si les cours ou les "
            "actualites doivent etre actualises d'abord) | 1 appel pour toute la liste |\n"
            "| Generer des suggestions | ~20 s | 1 appel |\n"
            "| Analyser (page Analyser) | ~1 min | le plus cher : 1 appel par etape |\n"
            "| Tout mettre a jour | ~1 min | cours + actualites, **pas** la "
            "synthese |\n\n"
            "Les durees les plus longues valent la peine d'etre connues d'avance : "
            "la synthese ecrit un commentaire par valeur, et l'app doit rester "
            "ouverte le temps qu'elle s'ecrive.\n\n"
            "Deux garde-fous evitent de payer deux fois : la synthese est reprise du "
            "cache si rien n'a bouge, et une analyse deja produite se rouvre "
            "gratuitement au lieu d'etre refaite.\n\n"
            "_Sous le capot : Haiku pour les actualites, Sonnet pour la synthese et les "
            "suggestions, Opus pour l'analyse approfondie._"
        )

    with st.expander("🔌 Sources de donnees & stockage"):
        cles = [
            ("Claude (ANTHROPIC_API_KEY)", bool(config.secrets.anthropic_api_key),
             "Classement des actualites, synthese, suggestions, analyse approfondie"),
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
def ecran_demarrage() -> None:
    """Premier ecran quand aucune valeur n'est suivie.

    Une liste vide produisait un tableau vide et des boutons grises sur toutes
    les pages : on montrait une machine a l'arret. On montre desormais la seule
    chose a faire, et le menu n'apparait qu'une fois la liste garnie.
    """
    st.markdown("## 📊 Bienvenue sur Sam_Invest")
    st.markdown(
        "Suis quelques entreprises, et l'app te donnera leurs cours, leurs "
        "actualites et une analyse de leurs comptes.\n\n"
        "**Ajoute trois entreprises que tu connais pour commencer.**"
    )
    with st.form("form_demarrage", clear_on_submit=False):
        q = st.text_input("Nom ou ticker", key="demarrage_q",
                          placeholder="ex : Air Liquide, Nvidia, ASML...",
                          label_visibility="collapsed")
        lance = st.form_submit_button("🔎 Chercher", use_container_width=True,
                                      type="primary")
    st.caption("Des idees : Air Liquide · Nvidia · LVMH · ASML · Microsoft")

    if lance and q.strip():
        with st.spinner("Recherche..."):
            st.session_state["demarrage_results"] = search_instruments(
                q.strip(), finnhub_key=config.secrets.finnhub_api_key)

    res = st.session_state.get("demarrage_results")
    if res:
        labels = {libelle_resultat(r): r for r in res}
        choix = st.multiselect("Coche ce que tu veux suivre :", list(labels))
        if st.button("➕ Ajouter et commencer", disabled=not choix,
                     use_container_width=True, type="primary"):
            rows = []
            with st.spinner("Ajout en cours..."):
                for lab in choix:
                    r = labels[lab]
                    rows.append({"ticker": r["symbol"], "nom": r["nom"],
                                 "type": r["type"],
                                 "theme": suggest_theme(r["symbol"], r["type"])})
            save_watchlist(rows)
            st.session_state["demarrage_results"] = None
            st.rerun()
    elif res == []:
        st.caption("Aucun resultat. Essaie un autre nom, ou le ticker exact (ex : NVDA).")

    if config.warnings:
        with st.expander("⚠️ Configuration"):
            for w in config.warnings:
                st.warning(w)


# Liste vide : on court-circuite toute la navigation. Rien d'autre n'a de sens
# tant qu'aucune valeur n'est suivie.
if not config.watchlist:
    ecran_demarrage()
    st.stop()


# Chaque page est nommee par la QUESTION a laquelle elle repond, et l'ordre suit
# celui dans lequel on s'en sert : quoi de neuf, ce que je suis, une valeur en
# particulier, la lecture d'ensemble — puis l'exploration hors watchlist.
#
# Defini avant PAGES : page_instrument y renvoie via st.page_link, et les pages
# ne sont rendues qu'apres navigation.run(), donc la variable existe a temps.
PAGE_ANALYSER = st.Page(page_analyser, title="Analyser", icon="🔬",
                        url_path="analyser")

PAGES = [
    # Page par defaut : Streamlit la sert a la racine « / » (son url_path n'est pas
    # utilise dans les liens). C'est elle qu'on retrouve apres une reconnexion sans
    # chemin explicite.
    st.Page(page_donnees, title="Aujourd'hui", icon="📈", url_path="aujourdhui", default=True),
    st.Page(page_watchlist, title="Ma liste", icon="✏️", url_path="ma-liste"),
    st.Page(page_instrument, title="Une entreprise", icon="🔎", url_path="entreprise"),
    st.Page(page_briefing, title="Ma synthese", icon="🧠", url_path="synthese"),
    # Portee differente des quatre precedentes : tout le marche, pas la watchlist.
    PAGE_ANALYSER,
    st.Page(page_about, title="Aide", icon="ℹ️", url_path="aide"),
]
navigation = st.navigation(PAGES, position="sidebar")

# Le menu accueille aussi les actions globales : sur telephone, elles n'ont pas
# a occuper le haut de chaque page.
with st.sidebar:
    st.markdown("## 📊 Sam_Invest")
    st.caption("Mes valeurs, suivies au jour le jour")
    st.divider()
    # Les alertes AVANT les actions : en ouvrant le menu on lit d'abord ce qui
    # ne va pas, on agit ensuite. C'est la meme logique qui fait d'« Aujourd'hui »
    # la page par defaut. Placees plus bas, elles tombaient sous la ligne de
    # flottaison d'un ecran de telephone et il fallait scroller pour les voir.
    # L'emplacement est reserve ici mais REMPLI plus bas, apres l'eventuelle mise
    # a jour globale : sinon le menu afficherait les flags d'avant l'actualisation.
    alertes_slot = st.container()
    st.divider()
    btn_global = st.button(
        "🔄 Tout mettre a jour", use_container_width=True, disabled=not config.watchlist,
        help="Cours + actualites. N'ecrit PAS la synthese (le bouton le plus cher).",
    )
    # Emplacement reserve pour l'export : rempli en fin de script (voir plus bas)
    # afin d'inclure le briefing et le diagnostic generes durant ce rerun.
    export_slot = st.empty()
    _f_don, _a_don = fraicheur("donnees")
    _f_new, _a_new = fraicheur("news")
    st.caption(
        f"🕒 Cours : {fmt_dt(_a_don) if _a_don else 'jamais'}{'' if _f_don else ' ⚠️'}  \n"
        f"🕒 Actualites : {fmt_dt(_a_new) if _a_new else 'jamais'}{'' if _f_new else ' ⚠️'}"
    )
    if config.warnings:
        with st.expander("⚠️ Configuration", expanded=not config.watchlist):
            for w in config.warnings:
                st.warning(w)

# La mise a jour globale s'execute AVANT le rendu de la page pour que les
# tableaux affiches soient deja frais.
if btn_global:
    afficher_compte_rendu(run_update(update_global, "Mise a jour globale"))

# Flags calcules UNE fois par run, apres l'eventuelle mise a jour : ils
# alimentent le menu des alertes ci-dessous et le tri de « Ma synthese ».
# Deterministe et local (lecture SQLite + pandas) : aucun appel reseau ni Claude.
FLAGS_COURANTS = [
    {"ticker": f.ticker, "regle": f.regle, "severite": f.severite, "message": f.message}
    for f in rules.tous_les_flags(config, signals.construire_snapshots(config))
]
with alertes_slot:
    rendre_menu_alertes(FLAGS_COURANTS)

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
    _diag_res = _diag_depuis_ligne(db.dernier_diagnostic())
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
        help="Exporte tout (cours, actualites, synthese, analyse approfondie, "
             "Suggestions) en un "
             "Markdown unique, pret a coller a Claude pour analyse.",
    )
except Exception as e:  # l'export ne doit jamais casser l'app
    log(f"[UI] export .md indisponible: {type(e).__name__}: {e}", "error")

# Note : suivi de portefeuille (PRU, allocation, DCA) retire -> outil de watchlist.
# Note : fonctionnalite email SMTP reportee (choix utilisateur).
