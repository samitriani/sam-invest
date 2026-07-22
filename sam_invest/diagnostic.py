"""Diagnostic financier - DETERMINISTE, sans LLM.

Toutes les metriques (marges, ROE/ROA/ROIC, WACC, EVA, ratios de structure,
cash, croissance, valorisation) sont calculees ici par du code, a partir des
etats financiers yfinance. Un LLM (Opus 4.8, dans llm.py) ne fait qu'ECRIRE
une conclusion a partir de ces chiffres — il n'en calcule aucun.

Garde-fous : chaque valeur peut etre None (donnee manquante) ; les ratios
manifestement aberrants (bugs de devise cote yfinance) sont neutralises.
Fiabilite reduite si la devise des etats differe de la devise de cotation.
"""

from __future__ import annotations

import math
from datetime import date

from . import data_sources as ds
from .config import AppConfig

# Hypotheses WACC par defaut (modifiables via config.yaml section [diagnostic]).
RF_DEFAUT = 0.04      # taux sans risque
PRIME_DEFAUT = 0.05   # prime de risque marche


# --------------------------------------------------------------------------
# Helpers numeriques / extraction
# --------------------------------------------------------------------------
def _num(x):
    try:
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return None


def _val(df, label, col=0):
    """Valeur d'une ligne d'etat financier a la colonne `col` (0 = plus recent)."""
    try:
        if df is None or label not in df.index:
            return None
        row = df.loc[label]
        return _num(row.iloc[col]) if col < len(row) else None
    except Exception:
        return None


def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _pct(x, dec=1):
    return f"{x * 100:.{dec}f}%" if isinstance(x, (int, float)) else "n/d"


def _ratio(x, dec=2):
    return f"{x:.{dec}f}" if isinstance(x, (int, float)) else "n/d"


def _x(x, dec=1):
    return f"{x:.{dec}f}x" if isinstance(x, (int, float)) else "n/d"


def _money(x, devise=""):
    if not isinstance(x, (int, float)):
        return "n/d"
    a = abs(x)
    unite = f" {devise}".rstrip()
    if a >= 1e12:
        return f"{x / 1e12:.2f} T{unite}"
    if a >= 1e9:
        return f"{x / 1e9:.2f} Md{unite}"
    if a >= 1e6:
        return f"{x / 1e6:.2f} M{unite}"
    return f"{x:.0f}{unite}"


def _cagr(df, label, latest):
    """CAGR entre la valeur la plus recente et la plus ancienne DISPONIBLE (>0).

    Renvoie (cagr, nb_annees). yfinance laisse parfois la colonne la plus ancienne
    vide -> on remonte jusqu'a trouver une valeur exploitable.
    """
    if df is None or latest is None or latest <= 0:
        return (None, 0)
    for c in range(df.shape[1] - 1, 0, -1):
        old = _val(df, label, c)
        if old is not None and old > 0:
            try:
                return ((latest / old) ** (1 / c) - 1, c)
            except Exception:
                return (None, 0)
    return (None, 0)


def L(label, valeur, source, doute=False):
    """Une ligne de resultat. source ∈ {'yfinance','calcule'} ; doute -> drapeau 🚬."""
    return {"label": label, "valeur": valeur, "source": source, "doute": doute}


def _etape(id_, titre, lignes):
    """lignes = liste de dicts (voir L())."""
    return {"id": id_, "titre": titre, "lignes": lignes}


# --------------------------------------------------------------------------
# Diagnostic complet
# --------------------------------------------------------------------------
def construire_diagnostic(config: AppConfig, ticker: str) -> dict:
    """Renvoie {ticker, nom, devise, note_fiabilite, etapes:[...]} ou {erreur:...}."""
    data = ds.fetch_etats_financiers(ticker)
    if not data:
        return {"erreur": f"Etats financiers indisponibles pour {ticker} "
                          "(ticker inconnu, ETF, ou couverture yfinance insuffisante)."}

    inc, bal, cf, info = data["income"], data["balance"], data["cashflow"], data["info"]
    dev = data.get("devise_etats") or ""
    diag_cfg = config.raw.get("diagnostic", {}) or {}
    rf = float(diag_cfg.get("taux_sans_risque", RF_DEFAUT))
    prime = float(diag_cfg.get("prime_marche", PRIME_DEFAUT))

    # --- Extraction des postes (annee la plus recente = colonne 0) ---
    revenue = _val(inc, "Total Revenue")
    gross = _val(inc, "Gross Profit")
    op_income = _val(inc, "Operating Income")
    ebit = _val(inc, "EBIT") or op_income
    ebitda = _val(inc, "EBITDA")
    net_income = _val(inc, "Net Income")
    pretax = _val(inc, "Pretax Income")
    tax = _val(inc, "Tax Provision")
    interest = _val(inc, "Interest Expense")

    assets = _val(bal, "Total Assets")
    equity = _val(bal, "Stockholders Equity")
    debt = _val(bal, "Total Debt")
    cash = _val(bal, "Cash And Cash Equivalents")
    invested = _val(bal, "Invested Capital")
    cur_assets = _val(bal, "Current Assets")
    cur_liab = _val(bal, "Current Liabilities")

    ocf = _val(cf, "Operating Cash Flow")
    capex = _val(cf, "Capital Expenditure")
    fcf = _val(cf, "Free Cash Flow")
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf + capex  # capex est negatif chez yfinance

    beta = _num(info.get("beta"))
    market_cap = _num(info.get("marketCap"))
    per = _num(info.get("trailingPE"))

    # Capital investi de repli si absent du bilan.
    if invested is None and equity is not None and debt is not None:
        invested = equity + debt - (cash or 0)

    tax_rate = _div(tax, pretax)
    if tax_rate is not None:
        tax_rate = min(max(tax_rate, 0.0), 0.5)  # borne raisonnable

    # Devise & annee de l'exercice le plus recent (colonne 0 des etats).
    dc = data.get("devise_cotation")
    mismatch = bool(dev and dc and dev != dc)  # change -> ratios capi/comptable douteux
    annee, date_ref = None, None
    try:
        col0 = inc.columns[0]
        annee = getattr(col0, "year", None)
        date_ref = str(col0.date())[:10] if hasattr(col0, "date") else str(col0)[:10]
    except Exception:
        pass

    etapes = []

    # 1) Activite & marges
    rev_prev = _val(inc, "Total Revenue", 1)
    croissance_ca = _div(revenue, rev_prev) - 1 if (revenue and rev_prev) else None
    etapes.append(_etape("marges", "1. Activite & marges", [
        L("Chiffre d'affaires", _money(revenue, dev), "yfinance"),
        L("Croissance du CA (YoY)", _pct(croissance_ca), "calcule"),
        L("Marge brute", _pct(_div(gross, revenue)), "calcule"),
        L("Marge operationnelle", _pct(_div(op_income, revenue)), "calcule"),
        L("Marge nette", _pct(_div(net_income, revenue)), "calcule"),
    ]))

    # 2) Rentabilite
    roe = _div(net_income, equity)
    roa = _div(net_income, assets)
    nopat = ebit * (1 - tax_rate) if (ebit is not None and tax_rate is not None) else ebit
    roic = _div(nopat, invested)
    etapes.append(_etape("rentabilite", "2. Rentabilite", [
        L("ROE (rentabilite des capitaux propres)", _pct(roe), "calcule"),
        L("ROA (rentabilite des actifs)", _pct(roa), "calcule"),
        L("ROIC (rentabilite du capital investi)", _pct(roic), "calcule"),
        L("NOPAT", _money(nopat, dev), "calcule"),
    ]))

    # 3) Creation de valeur -- sensible aux hypotheses (beta) et au change
    cout_capitaux = rf + beta * prime if beta is not None else None
    cout_dette = _div(interest, debt)
    if cout_dette is not None:
        cout_dette = abs(cout_dette)
    cout_dette_net = cout_dette * (1 - (tax_rate or 0)) if cout_dette is not None else None
    wacc = None
    if cout_capitaux is not None and market_cap and debt is not None:
        e, d = market_cap, debt
        cd = cout_dette_net if cout_dette_net is not None else 0.0
        wacc = (e / (e + d)) * cout_capitaux + (d / (e + d)) * cd
    spread = roic - wacc if (roic is not None and wacc is not None) else None
    eva = spread * invested if (spread is not None and invested is not None) else None
    dv = mismatch or beta is None  # doute sur le bloc WACC
    etapes.append(_etape("valeur", "3. Creation de valeur", [
        L("Cout des capitaux propres (CAPM)", _pct(cout_capitaux), "calcule", doute=(beta is None)),
        L("Cout de la dette (apres impot)", _pct(cout_dette_net), "calcule"),
        L("WACC estime", _pct(wacc), "calcule", doute=dv),
        L("ROIC - WACC (spread)", _pct(spread), "calcule", doute=dv),
        L("EVA (creation de valeur)", _money(eva, dev), "calcule", doute=dv),
    ]))

    # 4) Structure financiere / solvabilite
    net_debt = debt - cash if (debt is not None and cash is not None) else None
    etapes.append(_etape("structure", "4. Structure financiere", [
        L("Dette / capitaux propres", _x(_div(debt, equity)), "calcule"),
        L("Dette nette", _money(net_debt, dev), "calcule"),
        L("Dette nette / EBITDA", _x(_div(net_debt, ebitda)), "calcule"),
        L("Couverture des interets (EBIT / interets)",
          _x(_div(ebit, abs(interest)) if interest else None), "calcule"),
        L("Current ratio", _x(_div(cur_assets, cur_liab)), "calcule"),
    ]))

    # 5) Generation de cash
    fcf_src = "yfinance" if _val(cf, "Free Cash Flow") is not None else "calcule"
    etapes.append(_etape("cash", "5. Generation de cash", [
        L("Flux de tresorerie operationnel", _money(ocf, dev), "yfinance"),
        L("Free cash flow (FCF)", _money(fcf, dev), fcf_src),
        L("Marge de FCF", _pct(_div(fcf, revenue)), "calcule"),
        L("FCF / dette nette", _x(_div(fcf, net_debt) if (net_debt and net_debt > 0) else None), "calcule"),
    ]))

    # 6) Croissance (CAGR sur l'historique reellement disponible)
    cagr_ca, ans_ca = _cagr(inc, "Total Revenue", revenue)
    cagr_ni, ans_ni = _cagr(inc, "Net Income", net_income)
    etapes.append(_etape("croissance", "6. Croissance", [
        L(f"CAGR du CA (sur {ans_ca} ans)" if ans_ca else "CAGR du CA", _pct(cagr_ca), "calcule"),
        L(f"CAGR du benefice net (sur {ans_ni} ans)" if ans_ni else "CAGR du benefice net", _pct(cagr_ni), "calcule"),
    ]))

    # 7) Valorisation (garde-fous anti-aberrations de devise)
    pbr = _div(market_cap, equity)
    if pbr is not None and (pbr <= 0 or pbr > 200):  # bug de devise probable
        pbr = None
    ps = _div(market_cap, revenue)
    if ps is not None and (ps <= 0 or ps > 200):
        ps = None
    ev_ebitda = _num(info.get("enterpriseToEbitda"))
    if ev_ebitda is not None and (ev_ebitda <= 0 or ev_ebitda > 200):
        ev_ebitda = None
    fcf_yield = _div(fcf, market_cap)
    etapes.append(_etape("valorisation", "7. Valorisation", [
        L("PER (cours / benefice)", _ratio(per) if (per and 0 < per < 1000) else "n/d", "yfinance"),
        L("Price / Book (cours / capitaux propres)", _ratio(pbr), "calcule", doute=mismatch),
        L("Price / Sales", _ratio(ps), "calcule", doute=mismatch),
        L("VE / EBITDA", _x(ev_ebitda), "yfinance", doute=mismatch),
        L("Rendement du FCF", _pct(fcf_yield), "calcule", doute=mismatch),
    ]))

    note = None
    if mismatch:
        note = (f"Fiabilite reduite : etats financiers en {dev} mais cotation en {dc}. "
                "Les ratios melant capitalisation et postes comptables (WACC, PBR, P/S) "
                "peuvent etre fausses par le change (marques 🚬).")

    return {
        "ticker": ticker.upper(),
        "nom": data.get("nom", ticker),
        "devise": dev,
        "annee": annee,
        "date_reference": date_ref,       # cloture de l'exercice (base des chiffres comptables)
        "date_recuperation": date.today().isoformat(),  # jour de la recuperation (cours/multiples live)
        "note_fiabilite": note,
        "hypotheses": {"taux_sans_risque": rf, "prime_marche": prime, "beta": beta},
        "etapes": etapes,
    }
