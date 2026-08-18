"""Test de non-regression : deux flags distincts ne doivent jamais partager
la meme cle (ticker|regle|detail|severite).

Lancement : python -m tests.test_flags_cles_uniques

Contexte : le menu d'alertes (app.rendre_menu_alertes) donne a chaque flag un
bouton Streamlit `key=f"alerte_x_{cle}"`. Si deux flags calculent la meme cle,
Streamlit plante en production avec StreamlitDuplicateElementKey (cf. incident
du 2026-08-18 : NVDA en chute brutale a la fois sur la seance ET depuis son
plus-haut 52 semaines produisait deux flags "NVDA|chute|alerte" identiques
avant l'ajout du champ `Flag.detail`).

Ce test declenche volontairement plusieurs conditions d'une meme regle pour un
seul ticker (chute seance + drawdown, RSI + proche du plus-bas 52s,
degradation CA + marge + dette) et verifie que rules.tous_les_flags() ne
produit jamais deux fois la meme cle. Utilise une base SQLite TEMPORAIRE et ne
touche jamais data/sam_invest.db.
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

from sam_invest import db, rules
from sam_invest.config import AppConfig, Instrument, Secrets
from sam_invest.signals import Snapshot


def _cle(f: rules.Flag) -> str:
    """Meme formule que app.flag_cle / db.enregistrer_flags."""
    return f"{f.ticker}|{f.regle}|{f.detail}|{f.severite}"


def _run() -> None:
    # ignore_cleanup_errors : sur Windows, sqlite3.Connection utilisee comme
    # context manager ne FERME pas le fichier (seul le commit/rollback est
    # gere) -- le handle reste verrouille jusqu'au passage du GC, ce qui fait
    # parfois echouer le rmtree du tempdir. Sans lien avec ce test : meme
    # comportement latent dans test_masquage_alertes.py.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db.DB_PATH = Path(tmp) / "test_sam_invest.db"
        db.init_db()

        inst = Instrument(ticker="NVDA", nom="Nvidia", type="action")
        config = AppConfig(
            devise="USD",
            watchlist=[inst],
            raw={
                "flag_chute": {"seuil_seance_pct": -7, "seuil_drawdown_52s_pct": -20},
                "signaux_techniques": {
                    "flags_actifs": True, "rsi_survente": 30, "rsi_surachat": 70,
                    "seuil_proche_bas_52s_pct": 10,
                },
                "degradation_actions": {
                    "croissance_ca_min_pct": {"actif": True, "seuil": 0},
                    "marge_nette_min_pct": {"actif": True, "seuil": 0},
                    "dette_sur_capitaux_max": {"actif": True, "seuil": 1},
                },
            },
            secrets=Secrets(),
        )

        # Snapshot qui declenche SIMULTANEMENT les deux conditions de "chute"
        # (seance ET drawdown 52s) et les deux conditions de "technique" (RSI
        # survendu ET proche du plus-bas 52s) pour le meme ticker.
        snap = Snapshot(
            instrument=inst, last_price=50.0, change_pct=-10.0, drawdown_pct=-30.0,
            high_52w=100.0, low_52w=45.0, sma_50=60.0, sma_200=70.0, rsi_14=20.0,
            tendance="baissiere", rsi_etat="survendu", position_52w_pct=5.0,
        )

        # Fondamentaux qui declenchent les 3 alarmes de degradation (CA, marge,
        # dette) EN MEME TEMPS pour le meme ticker.
        db.upsert_fundamentals({
            "ticker": "NVDA", "asof": "2026-08-18T10:00:00+00:00",
            "revenue_ttm": None, "revenue_prev": None,
            "revenue_growth": -5.0, "net_margin": -2.0, "debt_to_equity": 5.0,
            "source": "test",
        })

        flags = rules.tous_les_flags(config, [snap])

        # On s'attend a plusieurs flags "chute"/"alerte" et plusieurs flags
        # "degradation"/"alerte" pour NVDA : c'est le cas qui faisait planter
        # Streamlit avant l'ajout de Flag.detail.
        assert sum(1 for f in flags if f.regle == "chute") >= 2
        assert sum(1 for f in flags if f.regle == "degradation") >= 3

        cles = [_cle(f) for f in flags]
        doublons = {c for c in cles if cles.count(c) > 1}
        assert not doublons, f"cles dupliquees (plantage Streamlit garanti) : {doublons}"

        gc.collect()  # libere les connexions sqlite avant le rmtree du tempdir

    print("OK - test_flags_cles_uniques : toutes les cles sont uniques.")


if __name__ == "__main__":
    _run()
