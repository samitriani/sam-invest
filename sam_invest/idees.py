"""Recommandations d'ajout a la watchlist - DETERMINISTE cote chiffres.

Deux sources de candidats, combinees :
  - Pairs (Finnhub) : entreprises comparables aux actions deja suivies.
    100% deterministe, sans LLM.
  - Trous thematiques (Claude Sonnet) : Claude identifie des trous de
    diversification et propose des tickers pour les combler.

GARDE-FOU ANTI-HALLUCINATION : chaque ticker candidat (des deux sources) est
d'abord VALIDE par une recherche Yahoo (doit resoudre a un instrument reel et
connu) avant d'etre chiffre. Un ticker qui ne se resout pas est silencieusement
ecarte. Les chiffres eux-memes (cours, tendance, RSI, fondamentaux, consensus
analystes) sont calcules EN DIRECT par le meme code que l'onglet Donnees, SANS
ECRITURE en base : ce ne sont que des candidats, pas encore suivis.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import data_sources as ds
from .config import AppConfig, Secrets
from .indicators import compute_indicators


@dataclass
class Candidat:
    ticker: str
    nom: str
    type: str              # "action" | "ETF"
    bourse: str
    origine: str           # "pair de X" | "trou thematique"
    raison: str
    last_price: float | None = None
    change_pct: float | None = None
    drawdown_pct: float | None = None
    rsi_14: float | None = None
    tendance: str = "n/d"
    sector: str | None = None
    per: float | None = None
    revenue_growth: float | None = None
    net_margin: float | None = None
    debt_to_equity: float | None = None
    consensus_achat: float | None = None
    consensus_conserver: float | None = None
    consensus_vendre: float | None = None


def contexte_watchlist(config: AppConfig) -> dict:
    """Resume compact de la watchlist actuelle, pour le prompt Sonnet."""
    themes = Counter((i.theme or "sans theme") for i in config.watchlist)
    return {
        "instruments_suivis": [
            {"ticker": i.ticker, "nom": i.nom, "type": i.type, "theme": i.theme}
            for i in config.watchlist
        ],
        "repartition_themes": dict(themes),
        "nb_actions": len(config.actions()),
        "nb_etf": len(config.etfs()),
    }


def candidats_pairs(config: AppConfig, max_par_action: int = 3,
                    max_total: int = 12) -> dict[str, list[str]]:
    """Finnhub 'peers' pour chaque action suivie. {ticker_source: [peers bruts]}.

    [] partout si pas de cle Finnhub. Exclut les tickers deja suivis.
    """
    key = config.secrets.finnhub_api_key
    if not key:
        return {}
    deja = {i.ticker.upper() for i in config.watchlist}
    out: dict[str, list[str]] = {}
    vus_globalement: set[str] = set()
    for inst in config.actions():
        if len(vus_globalement) >= max_total:
            break
        peers = ds.fetch_peers(inst.ticker, key)
        retenus = []
        for p in peers:
            pu = p.upper()
            if pu == inst.ticker.upper() or pu in deja or pu in vus_globalement:
                continue
            retenus.append(p)
            vus_globalement.add(pu)
            if len(retenus) >= max_par_action:
                break
        if retenus:
            out[inst.ticker] = retenus
    return out


def evaluer_candidat(ticker: str, secrets: Secrets, origine: str, raison: str) -> Candidat | None:
    """Valide le ticker (recherche Yahoo) puis calcule ses chiffres EN DIRECT.

    Renvoie None si le ticker ne se resout pas a un instrument reel connu, ou si
    aucun cours n'est recuperable. Aucune ecriture en base.
    """
    matches = ds.search_instruments(ticker, max_results=5)
    match = next((m for m in matches if m["symbol"].upper() == ticker.upper()), None)
    if not match:
        return None

    prix = ds.fetch_prices(ticker, secrets.finnhub_api_key, secrets.fmp_api_key)
    if not prix or not prix.get("quote"):
        return None
    quote = prix["quote"]
    hist = [{"date": d, "close": c} for d, c in (prix.get("history") or [])]
    ind = compute_indicators(hist)
    sma50, sma200 = ind.get("sma_50"), ind.get("sma_200")
    tendance = "n/d"
    if sma50 is not None and sma200 is not None:
        tendance = "haussiere" if sma50 > sma200 else ("baissiere" if sma50 < sma200 else "neutre")

    cand = Candidat(
        ticker=match["symbol"], nom=match["nom"], type=match["type"], bourse=match["bourse"],
        origine=origine, raison=raison,
        last_price=quote.get("last_price"), change_pct=quote.get("change_pct"),
        drawdown_pct=quote.get("drawdown_pct"), rsi_14=ind.get("rsi_14"), tendance=tendance,
    )

    if match["type"].lower() == "action":
        prof = ds.fetch_profil(ticker, "action")
        if prof and prof.get("payload"):
            p = prof["payload"]
            cand.sector = p.get("sector")
            cand.per = p.get("trailingPE")
            cand.revenue_growth = p.get("revenueGrowth")
            cand.net_margin = p.get("profitMargins")
            cand.debt_to_equity = p.get("debtToEquity")
        ar = ds.fetch_analyst_ratings(ticker)
        if ar:
            cand.consensus_achat = (ar.get("strong_buy") or 0) + (ar.get("buy") or 0)
            cand.consensus_conserver = ar.get("hold")
            cand.consensus_vendre = (ar.get("sell") or 0) + (ar.get("strong_sell") or 0)

    return cand


def generer_candidats(config: AppConfig, avec_thematiques: bool = True,
                      max_evalues: int = 12) -> list[Candidat]:
    """Assemble pairs + suggestions thematiques, valide et chiffre chaque candidat.

    Ne plante jamais : une source indisponible (pas de cle) est simplement vide.
    """
    deja = {i.ticker.upper() for i in config.watchlist}
    a_evaluer: list[tuple[str, str, str]] = []  # (ticker, origine, raison)

    # 1) Pairs (deterministe, Finnhub).
    for source, peers in candidats_pairs(config).items():
        for p in peers:
            a_evaluer.append((p, f"pair de {source}", f"Comparable a {source}, deja suivi."))

    # 2) Trous thematiques (Claude Sonnet) - chaque suggestion sera validee plus bas.
    if avec_thematiques and config.secrets.anthropic_api_key:
        from . import llm
        idees = llm.generer_idees_thematiques(config.secrets, contexte_watchlist(config))
        for idee in idees:
            t = str(idee.get("ticker", "")).strip().upper()
            if not t:
                continue
            trou = str(idee.get("trou_identifie", "")).strip()
            raison = str(idee.get("raison", "")).strip()
            texte = " — ".join(x for x in (trou, raison) if x) or "Suggestion Claude."
            a_evaluer.append((t, "trou thematique", texte))

    # Dedupe (garde la 1ere occurrence), exclut les tickers deja suivis.
    vus: set[str] = set()
    uniques: list[tuple[str, str, str]] = []
    for t, origine, raison in a_evaluer:
        tu = t.upper()
        if tu in deja or tu in vus:
            continue
        vus.add(tu)
        uniques.append((t, origine, raison))
        if len(uniques) >= max_evalues:
            break

    candidats: list[Candidat] = []
    for t, origine, raison in uniques:
        c = evaluer_candidat(t, config.secrets, origine, raison)
        if c:
            candidats.append(c)
    return candidats
