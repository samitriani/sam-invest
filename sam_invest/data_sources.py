"""Couche d'acces aux donnees de marche - DETERMINISTE, sans LLM.

Chaine de repli demandee : yfinance -> Finnhub -> Financial Modeling Prep.
On essaie yfinance en premier (gratuit, sans cle, bonne couverture Euronext .PA),
et on ne sollicite les API a cle qu'en repli, pour menager les quotas.

Regle d'or : AUCUNE fonction ne doit planter l'app. En cas d'echec (rate-limit,
ticker introuvable, reseau, cle absente), on renvoie None / liste vide et on
laisse l'appelant continuer. Les chiffres sont calcules ici par du code, jamais
par un LLM.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

# yfinance peut emettre des warnings bruyants ; on isole l'import.
try:
    import yfinance as yf
except Exception:  # pragma: no cover - import de secours
    yf = None

HTTP_TIMEOUT = 15
FINNHUB_BASE = "https://finnhub.io/api/v1"
FMP_BASE = "https://financialmodelingprep.com/api/v3"
YAHOO_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"


# ==========================================================================
# Utilitaires
# ==========================================================================
def _safe_float(x) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _news_id(ticker: str, headline: str, url: str) -> str:
    h = hashlib.md5(f"{ticker}|{headline}|{url}".encode("utf-8")).hexdigest()
    return h


def _quote_from_history(ticker: str, closes: pd.Series, source: str) -> dict:
    """Calcule le snapshot quote (variation seance, 52s, drawdown) a partir des cloture.

    100% deterministe (pandas), pas de LLM.
    """
    closes = closes.dropna()
    last = _safe_float(closes.iloc[-1]) if len(closes) else None
    prev = _safe_float(closes.iloc[-2]) if len(closes) >= 2 else None
    change_pct = None
    if last is not None and prev not in (None, 0):
        change_pct = (last / prev - 1.0) * 100.0

    # Fenetre 52 semaines (~252 seances de bourse).
    window = closes.tail(252)
    high_52w = _safe_float(window.max()) if len(window) else None
    low_52w = _safe_float(window.min()) if len(window) else None
    drawdown_pct = None
    if last is not None and high_52w not in (None, 0):
        drawdown_pct = (last / high_52w - 1.0) * 100.0

    return {
        "ticker": ticker,
        "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_price": last,
        "prev_close": prev,
        "change_pct": change_pct,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "drawdown_pct": drawdown_pct,
        "source": source,
    }


# ==========================================================================
# PRIX (historique + snapshot)  --  yfinance -> Finnhub -> FMP
# ==========================================================================
def fetch_prices(ticker: str, finnhub_key: str = "", fmp_key: str = "") -> dict | None:
    """Renvoie {'history': [(date, close)...], 'quote': {...}} ou None si tout echoue."""
    # --- 1) yfinance ------------------------------------------------------
    res = _prices_yfinance(ticker)
    if res:
        return res
    # --- 2) Finnhub -------------------------------------------------------
    if finnhub_key:
        res = _prices_finnhub(ticker, finnhub_key)
        if res:
            return res
    # --- 3) FMP -----------------------------------------------------------
    if fmp_key:
        res = _prices_fmp(ticker, fmp_key)
        if res:
            return res
    return None


def _prices_yfinance(ticker: str) -> dict | None:
    if yf is None:
        return None
    try:
        df = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=False)
        if df is None or df.empty or "Close" not in df:
            return None
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        history = [(idx.strftime("%Y-%m-%d"), _safe_float(v)) for idx, v in closes.items()]
        history = [(d, c) for d, c in history if c is not None]
        quote = _quote_from_history(ticker, closes, "yfinance")
        return {"history": history, "quote": quote}
    except Exception:
        return None


def _prices_finnhub(ticker: str, key: str) -> dict | None:
    """Repli Finnhub. Les chandeliers actions sont souvent restreints en free tier ;
    on tente l'historique, et a defaut on construit un snapshot depuis /quote."""
    try:
        # Historique (peut renvoyer 'no_access' sur le free tier)
        now = int(datetime.now(timezone.utc).timestamp())
        frm = int((datetime.now(timezone.utc) - timedelta(days=730)).timestamp())
        r = requests.get(
            f"{FINNHUB_BASE}/stock/candle",
            params={"symbol": ticker, "resolution": "D", "from": frm, "to": now, "token": key},
            timeout=HTTP_TIMEOUT,
        )
        if r.ok:
            data = r.json()
            if data.get("s") == "ok" and data.get("c"):
                ts = data["t"]
                closes_list = data["c"]
                idx = [datetime.fromtimestamp(t, tz=timezone.utc) for t in ts]
                closes = pd.Series(closes_list, index=pd.DatetimeIndex(idx)).dropna()
                history = [(d.strftime("%Y-%m-%d"), _safe_float(v)) for d, v in closes.items()]
                history = [(d, c) for d, c in history if c is not None]
                return {"history": history, "quote": _quote_from_history(ticker, closes, "finnhub")}

        # Repli snapshot seul via /quote (pas d'historique pour les graphiques).
        rq = requests.get(
            f"{FINNHUB_BASE}/quote", params={"symbol": ticker, "token": key}, timeout=HTTP_TIMEOUT
        )
        if rq.ok:
            q = rq.json()
            last = _safe_float(q.get("c"))
            prev = _safe_float(q.get("pc"))
            if last:
                change_pct = (last / prev - 1.0) * 100.0 if prev else _safe_float(q.get("dp"))
                quote = {
                    "ticker": ticker,
                    "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "last_price": last, "prev_close": prev, "change_pct": change_pct,
                    "high_52w": None, "low_52w": None, "drawdown_pct": None, "source": "finnhub",
                }
                return {"history": [], "quote": quote}
    except Exception:
        return None
    return None


def _prices_fmp(ticker: str, key: str) -> dict | None:
    try:
        r = requests.get(
            f"{FMP_BASE}/historical-price-full/{ticker}",
            params={"serietype": "line", "timeseries": 500, "apikey": key},
            timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            return None
        data = r.json()
        hist = data.get("historical", []) if isinstance(data, dict) else []
        if not hist:
            return None
        # FMP renvoie du plus recent au plus ancien -> on inverse.
        hist = sorted(hist, key=lambda x: x.get("date", ""))
        dates = [h["date"] for h in hist if h.get("close") is not None]
        vals = [_safe_float(h["close"]) for h in hist if h.get("close") is not None]
        closes = pd.Series(vals, index=pd.DatetimeIndex(pd.to_datetime(dates))).dropna()
        history = [(d.strftime("%Y-%m-%d"), _safe_float(v)) for d, v in closes.items()]
        return {"history": history, "quote": _quote_from_history(ticker, closes, "fmp")}
    except Exception:
        return None


# ==========================================================================
# FONDAMENTAUX (CTO surtout)  --  yfinance -> Finnhub -> FMP
# ==========================================================================
def fetch_fundamentals(ticker: str, finnhub_key: str = "", fmp_key: str = "") -> dict | None:
    res = _fundamentals_yfinance(ticker)
    if res and _fundamentals_usable(res):
        return res
    if finnhub_key:
        r2 = _fundamentals_finnhub(ticker, finnhub_key)
        if r2 and _fundamentals_usable(r2):
            return r2
    if fmp_key:
        r3 = _fundamentals_fmp(ticker, fmp_key)
        if r3 and _fundamentals_usable(r3):
            return r3
    # On renvoie au moins le partiel yfinance s'il existe (mieux que rien).
    return res


def _fundamentals_usable(f: dict) -> bool:
    """Au moins une metrique exploitable disponible."""
    return any(f.get(k) is not None for k in ("revenue_growth", "net_margin", "debt_to_equity"))


def _base_fund(ticker: str, source: str) -> dict:
    return {
        "ticker": ticker,
        "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revenue_ttm": None, "revenue_prev": None, "revenue_growth": None,
        "net_margin": None, "debt_to_equity": None, "source": source,
    }


def _fundamentals_yfinance(ticker: str) -> dict | None:
    if yf is None:
        return None
    try:
        t = yf.Ticker(ticker)
        f = _base_fund(ticker, "yfinance")
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}

        # Marge nette et croissance CA via info quand dispo.
        nm = _safe_float(info.get("profitMargins"))
        if nm is not None:
            f["net_margin"] = nm * 100.0
        rg = _safe_float(info.get("revenueGrowth"))
        if rg is not None:
            f["revenue_growth"] = rg * 100.0
        f["debt_to_equity"] = _safe_float(info.get("debtToEquity"))
        # yfinance exprime parfois debtToEquity en % (ex: 180 = 1.8x).
        if f["debt_to_equity"] is not None and f["debt_to_equity"] > 10:
            f["debt_to_equity"] = f["debt_to_equity"] / 100.0
        f["revenue_ttm"] = _safe_float(info.get("totalRevenue"))

        # Repli sur les etats financiers annuels pour la croissance CA si absente.
        if f["revenue_growth"] is None:
            try:
                fin = t.financials  # colonnes = annees, lignes = postes
                if fin is not None and not fin.empty and "Total Revenue" in fin.index:
                    rev = fin.loc["Total Revenue"].dropna()
                    if len(rev) >= 2:
                        cur, prev = _safe_float(rev.iloc[0]), _safe_float(rev.iloc[1])
                        f["revenue_ttm"] = f["revenue_ttm"] or cur
                        f["revenue_prev"] = prev
                        if cur is not None and prev not in (None, 0):
                            f["revenue_growth"] = (cur / prev - 1.0) * 100.0
            except Exception:
                pass
        return f
    except Exception:
        return None


def _fundamentals_finnhub(ticker: str, key: str) -> dict | None:
    try:
        r = requests.get(
            f"{FINNHUB_BASE}/stock/metric",
            params={"symbol": ticker, "metric": "all", "token": key},
            timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            return None
        m = (r.json() or {}).get("metric", {})
        f = _base_fund(ticker, "finnhub")
        f["net_margin"] = _safe_float(m.get("netProfitMarginTTM"))
        f["revenue_growth"] = _safe_float(m.get("revenueGrowthTTMYoy"))
        f["debt_to_equity"] = _safe_float(m.get("totalDebt/totalEquityQuarterly")) or \
            _safe_float(m.get("longTermDebt/equityQuarterly"))
        return f
    except Exception:
        return None


def _fundamentals_fmp(ticker: str, key: str) -> dict | None:
    try:
        f = _base_fund(ticker, "fmp")
        # Croissance CA via income statement annuel (2 dernieres annees).
        ri = requests.get(
            f"{FMP_BASE}/income-statement/{ticker}",
            params={"period": "annual", "limit": 2, "apikey": key},
            timeout=HTTP_TIMEOUT,
        )
        if ri.ok:
            inc = ri.json()
            if isinstance(inc, list) and len(inc) >= 1:
                cur = _safe_float(inc[0].get("revenue"))
                ni = _safe_float(inc[0].get("netIncome"))
                f["revenue_ttm"] = cur
                if cur not in (None, 0) and ni is not None:
                    f["net_margin"] = (ni / cur) * 100.0
                if len(inc) >= 2:
                    prev = _safe_float(inc[1].get("revenue"))
                    f["revenue_prev"] = prev
                    if cur is not None and prev not in (None, 0):
                        f["revenue_growth"] = (cur / prev - 1.0) * 100.0
        # Ratio dette/capitaux propres via ratios-ttm.
        rr = requests.get(
            f"{FMP_BASE}/ratios-ttm/{ticker}", params={"apikey": key}, timeout=HTTP_TIMEOUT
        )
        if rr.ok:
            rat = rr.json()
            if isinstance(rat, list) and rat:
                f["debt_to_equity"] = _safe_float(rat[0].get("debtEquityRatioTTM"))
        return f
    except Exception:
        return None


# ==========================================================================
# RECHERCHE D'INSTRUMENTS par nom/ticker  --  Yahoo Search (gratuit, sans cle)
# --------------------------------------------------------------------------
# Permet d'alimenter la watchlist sans connaitre les tickers par coeur. Un
# ticker est un FAIT : il vient d'une source de donnees, jamais d'un LLM.
# ==========================================================================
def _map_quote_type(qt: str | None) -> str | None:
    """Mappe le quoteType Yahoo vers notre type ; ignore le reste (indices, devises...)."""
    qt = (qt or "").upper()
    if qt == "EQUITY":
        return "action"
    if qt == "ETF":
        return "ETF"
    return None


def _norm_result(symbol, nom, bourse, qtype) -> dict | None:
    t = _map_quote_type(qtype)
    if not t or not symbol:
        return None
    return {"symbol": symbol, "nom": nom or symbol, "bourse": bourse or "", "type": t}


def search_instruments(query: str, max_results: int = 8) -> list[dict]:
    """Cherche des instruments par nom ou ticker. Renvoie [{symbol, nom, bourse, type}].

    yfinance.Search en primaire, endpoint Yahoo brut en repli. Filtre actions/ETF.
    Ne plante jamais : renvoie [] en cas d'echec.
    """
    query = (query or "").strip()
    if not query:
        return []
    out = _search_yfinance(query, max_results)
    if out:
        return out
    return _search_yahoo_endpoint(query, max_results)


def _search_yfinance(query: str, max_results: int) -> list[dict]:
    if yf is None:
        return []
    try:
        s = yf.Search(query, max_results=max_results * 2)
        out = []
        for r in (getattr(s, "quotes", None) or []):
            row = _norm_result(r.get("symbol"),
                               r.get("shortname") or r.get("longname"),
                               r.get("exchDisp") or r.get("exchange"),
                               r.get("quoteType"))
            if row:
                out.append(row)
            if len(out) >= max_results:
                break
        return out
    except Exception:
        return []


def _search_yahoo_endpoint(query: str, max_results: int) -> list[dict]:
    try:
        r = requests.get(
            YAHOO_SEARCH,
            params={"q": query, "quotesCount": max_results * 2, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            return []
        out = []
        for q in (r.json().get("quotes", []) or []):
            row = _norm_result(q.get("symbol"),
                               q.get("shortname") or q.get("longname"),
                               q.get("exchDisp") or q.get("exchange"),
                               q.get("quoteType"))
            if row:
                out.append(row)
            if len(out) >= max_results:
                break
        return out
    except Exception:
        return []


# ==========================================================================
# EVENEMENTS & ESTIMATIONS (actions)  --  yfinance
# ==========================================================================
def _to_date_str(v) -> str | None:
    """Normalise une date (date/datetime/Timestamp/str) en 'YYYY-MM-DD'."""
    try:
        if v is None:
            return None
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        return str(v)[:10]
    except Exception:
        return None


def fetch_events_estimates(ticker: str) -> dict | None:
    """Dates de resultats / ex-dividende + revisions d'EPS + objectif de cours.

    Source : yfinance (le spike a confirme une bonne couverture pour les actions
    US/EU/ADR). N'a de sens que pour les actions ; renvoie None si rien d'exploitable.
    """
    if yf is None:
        return None
    try:
        tk = yf.Ticker(ticker)
        out = {
            "ticker": ticker,
            "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "earnings_date": None, "exdiv_date": None,
            "eps_rev_up_30": None, "eps_rev_down_30": None,
            "eps_rev_up_7": None, "eps_rev_down_7": None, "eps_rev_period": None,
            "pt_mean": None, "pt_high": None, "pt_low": None, "pt_current": None,
            "source": "yfinance",
        }

        # --- Calendrier : prochaine date de resultats + ex-dividende ---
        try:
            cal = tk.calendar or {}
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)):
                ed = ed[0] if ed else None
            out["earnings_date"] = _to_date_str(ed)
            out["exdiv_date"] = _to_date_str(cal.get("Ex-Dividend Date"))
        except Exception:
            pass

        # --- Revisions d'EPS (combien d'analystes relevent/abaissent) ---
        try:
            er = tk.eps_revisions
            if er is not None and not er.empty:
                period = None
                for per in ("0y", "+1q", "0q", "+1y"):
                    if per in er.index:
                        period = per
                        break
                if period is None:
                    period = er.index[0]
                row = er.loc[period]
                out["eps_rev_period"] = str(period)
                out["eps_rev_up_30"] = _safe_float(row.get("upLast30days"))
                out["eps_rev_down_30"] = _safe_float(row.get("downLast30days"))
                out["eps_rev_up_7"] = _safe_float(row.get("upLast7days"))
                out["eps_rev_down_7"] = _safe_float(row.get("downLast7Days"))
        except Exception:
            pass

        # --- Objectif de cours analystes ---
        try:
            pt = tk.analyst_price_targets or {}
            out["pt_mean"] = _safe_float(pt.get("mean"))
            out["pt_high"] = _safe_float(pt.get("high"))
            out["pt_low"] = _safe_float(pt.get("low"))
            out["pt_current"] = _safe_float(pt.get("current"))
        except Exception:
            pass

        usable = any(out[k] is not None for k in
                     ("earnings_date", "exdiv_date", "eps_rev_up_30", "eps_rev_down_30", "pt_mean"))
        return out if usable else None
    except Exception:
        return None


# ==========================================================================
# PROFIL / FONDAMENTAUX D'AFFICHAGE  --  yfinance (.info + .funds_data)
# --------------------------------------------------------------------------
# Set focalise pour l'onglet "Donnees par instrument". Actions = ratios
# financiers ; ETF = caracteristiques du fonds. Une seule requete .info.
# ==========================================================================
def fetch_profil(ticker: str, type_: str) -> dict | None:
    """Renvoie {'ticker','asof','type','payload':dict,'source'} ou None."""
    if yf is None:
        return None
    try:
        tk = yf.Ticker(ticker)
        try:
            info = tk.info or {}
        except Exception:
            info = {}

        if type_.lower() == "action":
            de = _safe_float(info.get("debtToEquity"))
            if de is not None and de > 10:  # yfinance exprime parfois en % (180 = 1.8x)
                de = de / 100.0
            payload = {
                "marketCap": _safe_float(info.get("marketCap")),
                "sector": info.get("sector"),
                "trailingPE": _safe_float(info.get("trailingPE")),
                "forwardPE": _safe_float(info.get("forwardPE")),
                "priceToBook": _safe_float(info.get("priceToBook")),
                "profitMargins": _safe_float(info.get("profitMargins")),
                "returnOnEquity": _safe_float(info.get("returnOnEquity")),
                "revenueGrowth": _safe_float(info.get("revenueGrowth")),
                "earningsGrowth": _safe_float(info.get("earningsGrowth")),
                "debtToEquity": de,
                "currentRatio": _safe_float(info.get("currentRatio")),
                "freeCashflow": _safe_float(info.get("freeCashflow")),
                "dividendYield": _safe_float(info.get("dividendYield")),
                "targetMeanPrice": _safe_float(info.get("targetMeanPrice")),
                "currentPrice": _safe_float(info.get("currentPrice")
                                            or info.get("regularMarketPrice")),
            }
        else:  # ETF
            top = []
            try:
                th = getattr(tk.funds_data, "top_holdings", None)
                if th is not None and not th.empty:
                    for sym, row in th.head(5).iterrows():
                        top.append({"symbol": str(sym),
                                    "name": row.get("Name"),
                                    "pct": _safe_float(row.get("Holding Percent"))})
            except Exception:
                pass
            payload = {
                "category": info.get("category"),
                "totalAssets": _safe_float(info.get("totalAssets")),
                "expenseRatio": _safe_float(info.get("netExpenseRatio")),  # en % (ex 0.18)
                "yield": _safe_float(info.get("yield")),                   # fraction (ex 0.0038)
                "ytdReturn": _safe_float(info.get("ytdReturn")),
                "top_holdings": top,
            }

        if any(v not in (None, [], "") for v in payload.values()):
            return {
                "ticker": ticker,
                "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "type": "action" if type_.lower() == "action" else "ETF",
                "payload": payload,
                "source": "yfinance",
            }
        return None
    except Exception:
        return None


# ==========================================================================
# NEWS  --  yfinance -> Finnhub -> FMP
# ==========================================================================
def fetch_news(ticker: str, max_items: int = 10, days: int = 14,
               finnhub_key: str = "", fmp_key: str = "") -> list[dict]:
    items = _news_yfinance(ticker, max_items, days)
    if items:
        return items[:max_items]
    if finnhub_key:
        items = _news_finnhub(ticker, max_items, days, finnhub_key)
        if items:
            return items[:max_items]
    if fmp_key:
        items = _news_fmp(ticker, max_items, fmp_key)
        if items:
            return items[:max_items]
    return []


def _within_days(dt: datetime, days: int) -> bool:
    return dt >= datetime.now(timezone.utc) - timedelta(days=days)


def _news_yfinance(ticker: str, max_items: int, days: int) -> list[dict]:
    if yf is None:
        return []
    try:
        raw = yf.Ticker(ticker).news or []
        out = []
        for n in raw:
            # yfinance a deux formats selon les versions (plat ou {'content': {...}}).
            content = n.get("content", n)
            title = content.get("title") or n.get("title") or ""
            if not title:
                continue
            url = ""
            if isinstance(content.get("canonicalUrl"), dict):
                url = content["canonicalUrl"].get("url", "")
            if not url and isinstance(content.get("clickThroughUrl"), dict):
                url = content["clickThroughUrl"].get("url", "")
            if not url:
                url = n.get("link", "")
            # datetime
            dt = None
            ts = n.get("providerPublishTime")
            if ts:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            elif content.get("pubDate"):
                try:
                    dt = datetime.fromisoformat(content["pubDate"].replace("Z", "+00:00"))
                except Exception:
                    dt = None
            if dt and not _within_days(dt, days):
                continue
            out.append({
                "id": _news_id(ticker, title, url),
                "ticker": ticker,
                "datetime": dt.isoformat() if dt else "",
                "headline": title,
                "summary": content.get("summary", "") or "",
                "url": url,
                "source": "yfinance",
            })
        return out
    except Exception:
        return []


def _news_finnhub(ticker: str, max_items: int, days: int, key: str) -> list[dict]:
    try:
        to = datetime.now(timezone.utc).date()
        frm = to - timedelta(days=days)
        r = requests.get(
            f"{FINNHUB_BASE}/company-news",
            params={"symbol": ticker, "from": frm.isoformat(), "to": to.isoformat(), "token": key},
            timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            return []
        out = []
        for n in (r.json() or []):
            title = n.get("headline", "")
            if not title:
                continue
            dt = datetime.fromtimestamp(n["datetime"], tz=timezone.utc) if n.get("datetime") else None
            out.append({
                "id": _news_id(ticker, title, n.get("url", "")),
                "ticker": ticker,
                "datetime": dt.isoformat() if dt else "",
                "headline": title,
                "summary": n.get("summary", "") or "",
                "url": n.get("url", ""),
                "source": "finnhub",
            })
        return out
    except Exception:
        return []


def _news_fmp(ticker: str, max_items: int, key: str) -> list[dict]:
    try:
        r = requests.get(
            f"{FMP_BASE}/stock_news",
            params={"tickers": ticker, "limit": max_items, "apikey": key},
            timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            return []
        out = []
        for n in (r.json() or []):
            title = n.get("title", "")
            if not title:
                continue
            dt = None
            if n.get("publishedDate"):
                try:
                    dt = datetime.fromisoformat(n["publishedDate"]).replace(tzinfo=timezone.utc)
                except Exception:
                    dt = None
            out.append({
                "id": _news_id(ticker, title, n.get("url", "")),
                "ticker": ticker,
                "datetime": dt.isoformat() if dt else "",
                "headline": title,
                "summary": n.get("text", "") or "",
                "url": n.get("url", ""),
                "source": "fmp",
            })
        return out
    except Exception:
        return []
