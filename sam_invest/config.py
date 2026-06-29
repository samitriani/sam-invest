"""Chargement et validation de config.yaml (watchlist) + secrets .env.

Tolerant : ne plante jamais l'app pour un detail. Les problemes (placeholders,
type invalide) sont remontes sous forme de liste d'avertissements affichables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Racine du projet (dossier contenant app.py)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"


@dataclass
class Instrument:
    ticker: str
    nom: str
    type: str          # "action" ou "ETF"
    theme: str = ""    # etiquette libre de regroupement


@dataclass
class Secrets:
    anthropic_api_key: str = ""
    model_haiku: str = "claude-haiku-4-5"
    model_sonnet: str = "claude-sonnet-4-6"
    finnhub_api_key: str = ""
    fmp_api_key: str = ""


@dataclass
class AppConfig:
    devise: str
    watchlist: list[Instrument]
    raw: dict                       # config yaml brute (pour acces aux regles)
    secrets: Secrets
    warnings: list[str] = field(default_factory=list)

    # --- helpers regles : on lit directement dans `raw` pour rester simple ---
    @property
    def flag_chute(self) -> dict:
        return self.raw.get("flag_chute", {})

    @property
    def signaux_techniques(self) -> dict:
        return self.raw.get("signaux_techniques", {})

    @property
    def degradation_actions(self) -> dict:
        return self.raw.get("degradation_actions", {})

    @property
    def evenements(self) -> dict:
        return self.raw.get("evenements", {"resultats_dans_jours": 7, "exdiv_dans_jours": 7})

    @property
    def revisions_estimations(self) -> dict:
        return self.raw.get("revisions_estimations", {"actif": True, "seuil_net_revisions_30j": 0})

    @property
    def news(self) -> dict:
        return self.raw.get("news", {"max_par_ticker": 10, "anciennete_max_jours": 14})

    @property
    def briefing(self) -> dict:
        return self.raw.get("briefing", {"delta_variation_notable_pct": 3})

    def actions(self) -> list[Instrument]:
        return [i for i in self.watchlist if i.type.lower() == "action"]

    def etfs(self) -> list[Instrument]:
        return [i for i in self.watchlist if i.type.lower() == "etf"]


def _is_placeholder(value) -> bool:
    """Detecte un placeholder non rempli (<...>) laisse dans le template."""
    return isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">")


def _norm_type(t: str) -> str:
    t = str(t).strip().lower()
    if t in ("action", "stock", "equity"):
        return "action"
    if t in ("etf", "fund", "fonds"):
        return "ETF"
    return ""


def _watchlist_block(rows: list[dict]) -> str:
    """Genere le YAML du bloc `watchlist:` a partir de lignes {ticker,nom,type,theme}.

    Utilise json.dumps pour produire des scalaires entre guillemets surs (accents
    conserves, guillemets internes echappes), compatibles YAML.
    """
    import json
    out = ["watchlist:"]
    for r in rows:
        ticker = str(r.get("ticker", "") or "").strip()
        if not ticker:
            continue
        nom = str(r.get("nom", "") or ticker).strip()
        typ = _norm_type(r.get("type", "")) or "action"
        theme = str(r.get("theme", "") or "").strip()
        out.append(f"  - ticker: {json.dumps(ticker, ensure_ascii=False)}")
        out.append(f"    nom: {json.dumps(nom, ensure_ascii=False)}")
        out.append(f"    type: {json.dumps(typ, ensure_ascii=False)}")
        out.append(f"    theme: {json.dumps(theme, ensure_ascii=False)}")
    return "\n".join(out)


def save_watchlist(rows: list[dict]) -> int:
    """Reecrit UNIQUEMENT le bloc `watchlist:` de config.yaml.

    Le reste du fichier (commentaires + sections de regles) est preserve a
    l'identique. Renvoie le nombre d'instruments ecrits.
    """
    text = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else "devise: \"EUR\"\n"
    lines = text.splitlines()

    block = _watchlist_block(rows)
    n = block.count("\n  - ticker:")

    # Localise la ligne 'watchlist:' en colonne 0.
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("watchlist:") and (len(ln) == 10 or not ln[10:11].isalnum()):
            start = i
            break

    if start is None:
        # Pas de section existante : on l'ajoute a la fin.
        new_text = text.rstrip() + "\n\n" + block + "\n"
    else:
        # Fin du bloc = prochaine ligne en colonne 0 non vide (cle ou commentaire).
        end = len(lines)
        for j in range(start + 1, len(lines)):
            ln = lines[j]
            if ln and not ln[0].isspace():
                end = j
                break
        new_lines = lines[:start] + block.splitlines() + [""] + lines[end:]
        new_text = "\n".join(new_lines).rstrip() + "\n"

    CONFIG_PATH.write_text(new_text, encoding="utf-8")
    return n


def load_secrets() -> Secrets:
    """Charge les secrets depuis .env (jamais depuis config.yaml)."""
    load_dotenv(ENV_PATH)
    return Secrets(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        model_haiku=os.getenv("CLAUDE_MODEL_HAIKU", "claude-haiku-4-5").strip(),
        model_sonnet=os.getenv("CLAUDE_MODEL_SONNET", "claude-sonnet-4-6").strip(),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY", "").strip(),
        fmp_api_key=os.getenv("FMP_API_KEY", "").strip(),
    )


def load_config() -> AppConfig:
    """Charge et valide config.yaml (watchlist). Remonte des warnings sans planter."""
    warnings: list[str] = []
    secrets = load_secrets()

    if not CONFIG_PATH.exists():
        warnings.append(
            "config.yaml introuvable. Copie config.template.yaml en config.yaml "
            "et remplis ta watchlist."
        )
        return AppConfig(devise="EUR", watchlist=[], raw={}, secrets=secrets, warnings=warnings)

    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        warnings.append(f"config.yaml illisible (erreur YAML) : {e}")
        return AppConfig(devise="EUR", watchlist=[], raw={}, secrets=secrets, warnings=warnings)

    watchlist: list[Instrument] = []
    vus: set[str] = set()
    for i, item in enumerate(raw.get("watchlist", []) or [], start=1):
        ticker = item.get("ticker", "")
        if _is_placeholder(ticker) or not ticker:
            warnings.append(f"Instrument #{i} ignore : ticker non renseigne (placeholder).")
            continue
        ticker = ticker.strip()
        typ = _norm_type(item.get("type", ""))
        if not typ:
            warnings.append(
                f"Instrument '{ticker}' : type '{item.get('type')}' invalide (attendu action ou ETF)."
            )
            continue
        if ticker.upper() in vus:
            warnings.append(f"Instrument '{ticker}' en double : ignore.")
            continue
        vus.add(ticker.upper())
        watchlist.append(
            Instrument(
                ticker=ticker,
                nom=str(item.get("nom", ticker)).strip(),
                type=typ,
                theme=str(item.get("theme", "")).strip(),
            )
        )

    if not watchlist:
        warnings.append("Watchlist vide. Remplis config.yaml avec des instruments.")

    if not secrets.anthropic_api_key:
        warnings.append(
            "ANTHROPIC_API_KEY absente du .env : le Briefing affichera les chiffres "
            "mais sans la synthese Claude."
        )

    return AppConfig(
        devise=str(raw.get("devise", "EUR")),
        watchlist=watchlist,
        raw=raw,
        secrets=secrets,
        warnings=warnings,
    )
