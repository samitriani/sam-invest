"""Glossaire du jargon financier - source de verite unique pour les tooltips.

`definition(label)` renvoie la definition d'un terme detecte dans un libelle
(ex: "ROE (rentabilite...)" -> definition de ROE). Utilise pour :
  - st.metric(..., help=...) et st.column_config(..., help=...) : tooltips natifs ;
  - abbr(label) : <abbr title="..."> pour les tableaux HTML (diagnostic).

Matching robuste : insensible aux accents et a la casse, sur frontiere de mot,
plus long terme d'abord (ex: "Dette nette / EBITDA" avant "Dette nette").
"""

from __future__ import annotations

import html
import re
import unicodedata

GLOSSAIRE: dict[str, str] = {
    # --- Marche / technique ---
    "Cours": "Dernier prix cote de l'instrument.",
    "Seance": "Variation du cours sur la derniere seance (cloture vs cloture precedente).",
    "Drawdown 52s": "Baisse depuis le plus haut des 52 dernieres semaines (0 % = au plus haut).",
    "Drawdown": "Baisse depuis un plus haut recent.",
    "Position 52s": "Position du cours dans sa fourchette 52 semaines : 0 % = plus bas, 100 % = plus haut.",
    "Etat RSI": "Interpretation du RSI : survendu (<30), neutre, ou surachete (>70).",
    "RSI 14": "Relative Strength Index sur 14 jours (0 a 100) : <30 = survendu, >70 = surachete.",
    "RSI": "Relative Strength Index : indicateur de momentum de 0 a 100 (survente / surachat).",
    "Tendance": "Sens de la tendance de fond : haussiere si la moyenne 50 jours passe au-dessus de la moyenne 200 jours.",
    "SMA 200": "Moyenne mobile simple des cours sur 200 jours (tendance de long terme).",
    "SMA 50": "Moyenne mobile simple des cours sur 50 jours (tendance de moyen terme).",
    "SMA": "Moyenne mobile simple des cours sur N jours.",
    "Plus-haut 52s": "Cours le plus eleve des 52 dernieres semaines.",
    # --- Valorisation ---
    "PER (trailing)": "PER base sur les benefices des 12 derniers mois (realises).",
    "PER (forward)": "PER base sur les benefices attendus l'annee a venir (previsions analystes).",
    "PER": "Price-Earnings Ratio : cours rapporte au benefice par action (annees de benefices payees par le marche).",
    "Price / Book": "Cours rapporte a la valeur comptable des capitaux propres (>1 = valorise au-dessus de l'actif net).",
    "Price / Sales": "Capitalisation rapportee au chiffre d'affaires.",
    "VE / EBITDA": "Valeur d'entreprise rapportee a l'EBITDA (valorisation independante de la structure financiere).",
    "EV": "Enterprise Value : capitalisation + dette nette (valeur de toute l'entreprise, dette comprise).",
    "VE": "Valeur d'Entreprise : capitalisation + dette nette (valeur de toute l'entreprise, dette comprise).",
    "Objectif": "Objectif de cours moyen des analystes.",
    "Potentiel": "Ecart entre l'objectif de cours moyen des analystes et le cours actuel.",
    "Rendement du FCF": "Free cash flow rapporte a la capitalisation (rendement cash pour l'actionnaire).",
    "Rendement div": "Dividende annuel rapporte au cours.",
    "Capitalisation": "Valeur boursiere totale = cours x nombre d'actions.",
    # --- Rentabilite / creation de valeur ---
    "ROE": "Return on Equity : benefice net rapporte aux capitaux propres (rentabilite pour l'actionnaire).",
    "ROA": "Return on Assets : benefice net rapporte au total des actifs.",
    "ROIC": "Return on Invested Capital : rentabilite du capital reellement investi (NOPAT / capital investi).",
    "NOPAT": "Net Operating Profit After Tax : resultat d'exploitation apres impot (benefice operationnel hors effet de la dette).",
    "WACC": "Cout moyen pondere du capital : rendement minimum attendu par les apporteurs de fonds (actionnaires + creanciers).",
    "EVA": "Economic Value Added : valeur creee au-dela du cout du capital = (ROIC - WACC) x capital investi. Positif = creation de valeur.",
    "CAPM": "Modele du cout des capitaux propres = taux sans risque + beta x prime de risque marche.",
    "ROIC - WACC": "Ecart entre la rentabilite du capital (ROIC) et son cout (WACC). Positif = l'entreprise cree de la valeur.",
    "spread": "Ecart entre la rentabilite du capital (ROIC) et son cout (WACC).",
    "Cout des capitaux propres": "Rendement exige par les actionnaires (estime via le CAPM).",
    "Cout de la dette": "Taux d'interet effectif de la dette, apres economie d'impot.",
    # --- Marges & resultat ---
    "EBITDA": "Benefice avant interets, impots, depreciations et amortissements (rentabilite operationnelle cash).",
    "EBIT": "Benefice avant interets et impots (resultat d'exploitation).",
    "Marge brute": "(CA - cout des ventes) / CA.",
    "Marge operationnelle": "Resultat d'exploitation rapporte au chiffre d'affaires.",
    "Marge nette": "Benefice net rapporte au chiffre d'affaires.",
    "Marge de FCF": "Free cash flow rapporte au chiffre d'affaires.",
    # --- Cash & croissance ---
    "Free cash flow": "Tresorerie generee par l'activite apres investissements (cash reellement disponible).",
    "FCF": "Free Cash Flow : tresorerie disponible apres investissements.",
    "Flux de tresorerie operationnel": "Tresorerie generee par l'activite courante avant investissements.",
    "Croissance du CA": "Evolution du chiffre d'affaires d'une annee sur l'autre.",
    "Croissance CA": "Croissance du chiffre d'affaires d'une annee sur l'autre.",
    "Croissance BPA": "Croissance du benefice par action (BPA) d'une annee sur l'autre.",
    "TER": "Total Expense Ratio : frais de gestion annuels de l'ETF, en % de l'encours.",
    "Perf YTD": "Performance depuis le debut de l'annee (Year To Date).",
    "CAGR": "Taux de croissance annuel moyen compose sur la periode consideree.",
    # --- Structure financiere ---
    "Dette nette / EBITDA": "Annees d'EBITDA necessaires pour rembourser la dette nette (endettement relatif).",
    "Dette nette": "Dette totale moins la tresorerie.",
    "Dette / capitaux": "Dette totale rapportee aux capitaux propres (levier financier).",
    "Couverture des interets": "EBIT rapporte aux charges d'interets (capacite a payer ses interets).",
    "Current ratio": "Actifs courants / passifs courants : liquidite a court terme (>1 = honore ses dettes courtes).",
    # --- Estimations / news ---
    "Revisions": "Nombre d'analystes relevant vs abaissant leurs previsions de benefice (solde net sur 30 jours).",
}

# --------------------------------------------------------------------------
# Formules des indicateurs CALCULES du diagnostic (transparence : mouse-over).
# Cle = terme reconnu dans le libelle (meme matching que le glossaire, plus
# long d'abord). Seuls les indicateurs calcules par le code ont une formule ;
# les donnees brutes yfinance n'en ont pas (source affichee = "yfinance").
# --------------------------------------------------------------------------
FORMULES: dict[str, str] = {
    # Marges & activite
    "Croissance du CA": "CA(N) / CA(N-1) − 1",
    "Marge brute": "marge brute / chiffre d'affaires",
    "Marge operationnelle": "resultat d'exploitation / chiffre d'affaires",
    "Marge nette": "resultat net / chiffre d'affaires",
    # Rentabilite (ROIC - WACC avant ROIC/WACC : plus long d'abord)
    "ROIC - WACC": "ROIC − WACC",
    "ROE": "resultat net / capitaux propres (fin d'exercice)",
    "ROA": "resultat net / total des actifs",
    "ROIC": "NOPAT / capital investi",
    "NOPAT": "EBIT × (1 − taux d'impot)",
    # Creation de valeur
    "Cout des capitaux propres": "taux sans risque + beta × prime de risque (CAPM)",
    "Cout de la dette": "(charges d'interets / dette) × (1 − taux d'impot)",
    "WACC": "poids CP × cout des CP + poids dette × cout de la dette net d'impot",
    "EVA": "(ROIC − WACC) × capital investi",
    # Structure financiere (Dette nette / EBITDA avant Dette nette)
    "Dette / capitaux propres": "dette totale / capitaux propres",
    "Dette nette / EBITDA": "(dette totale − tresorerie) / EBITDA",
    "Dette nette": "dette totale − tresorerie",
    "Couverture des interets": "EBIT / charges d'interets",
    "Current ratio": "actifs courants / passifs courants",
    # Generation de cash (FCF / dette nette avant les autres)
    "FCF / dette nette": "free cash flow / dette nette",
    "Marge de FCF": "free cash flow / chiffre d'affaires",
    "Free cash flow": "flux de tresorerie operationnel + capex (capex negatif)",
    # Croissance
    "CAGR du CA": "(CA final / CA initial)^(1 / nb annees) − 1",
    "CAGR du benefice net": "(resultat net final / initial)^(1 / nb annees) − 1",
    # Valorisation (multiples calcules ; PER et VE/EBITDA viennent bruts de yfinance)
    "Price / Book": "capitalisation / capitaux propres",
    "Price / Sales": "capitalisation / chiffre d'affaires",
    "Rendement du FCF": "free cash flow / capitalisation",
}

# Cles triees par longueur decroissante (plus specifique d'abord).
_KEYS = sorted(GLOSSAIRE.keys(), key=len, reverse=True)
_FORMULE_KEYS = sorted(FORMULES.keys(), key=len, reverse=True)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


_NORM_KEYS = [(k, _norm(k)) for k in _KEYS]
_NORM_FORMULE_KEYS = [(k, _norm(k)) for k in _FORMULE_KEYS]


def definition(label: str | None) -> str | None:
    """Definition du terme financier detecte dans `label`, ou None."""
    if not label:
        return None
    nl = _norm(str(label))
    for term, nk in _NORM_KEYS:
        if re.search(r"\b" + re.escape(nk) + r"\b", nl):
            return GLOSSAIRE[term]
    return None


def formule(label: str | None) -> str | None:
    """Formule de l'indicateur calcule detecte dans `label`, ou None (donnee brute)."""
    if not label:
        return None
    nl = _norm(str(label))
    for term, nk in _NORM_FORMULE_KEYS:
        if re.search(r"\b" + re.escape(nk) + r"\b", nl):
            return FORMULES[term]
    return None


def formule_abbr(label: str, texte: str) -> str:
    """HTML : `texte` avec tooltip <abbr> montrant la formule, si l'indicateur est calcule."""
    f = formule(label)
    if not f:
        return html.escape(str(texte))
    return (f'<abbr title="{html.escape(f, quote=True)}" '
            f'style="text-decoration:underline dotted #9aa;cursor:help">'
            f'{html.escape(str(texte))}</abbr>')


def abbr(label: str) -> str:
    """HTML : libelle avec tooltip <abbr> si un terme est reconnu, sinon texte simple."""
    d = definition(label)
    if not d:
        return html.escape(str(label))
    return (f'<abbr title="{html.escape(d, quote=True)}" '
            f'style="text-decoration:underline dotted #9aa;cursor:help">'
            f'{html.escape(str(label))}</abbr>')
