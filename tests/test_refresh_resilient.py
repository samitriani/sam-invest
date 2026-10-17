"""Test de non-regression : un rafraichissement survit aux coupures de connexion.

Lancement : python -m tests.test_refresh_resilient

Contexte : sur mobile, le rafraichissement complet (3 a 4 min) reussissait une
fois sur deux. Il tournait dans le script Streamlit du clic, lie a la connexion
du telephone, et les news + leur classement Haiku n'etaient ecrits en base
qu'une fois TOUS les tickers traites : une coupure a la derniere seconde
perdait tout ce qui avait ete recupere et paye.

Verifie que :
  - le rafraichissement part dans un thread et rend la main tout de suite ;
  - les news (puis leur classement) sont en base ticker par ticker, PENDANT la
    phase, pas seulement a la fin ;
  - l'etat est 'en_cours' avec une progression pendant le travail, 'termine'
    a la fin ;
  - un 2e lancement pendant un cycle vivant est refuse ;
  - une coupure brutale en pleine phase laisse l'etat 'interrompu', garde ce
    qui etait deja ecrit, et libere le verrou pour un nouveau lancement.

Aucun reseau (sources et Claude simules), base SQLite TEMPORAIRE : ne touche
jamais data/sam_invest.db.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from sam_invest import briefing, db, update


class _Coupure(BaseException):
    """Simule une mort brutale du travail (pas une erreur reseau recuperable)."""


def _attendre(condition, delai: float = 10.0) -> bool:
    fin = time.time() + delai
    while time.time() < fin:
        if condition():
            return True
        time.sleep(0.02)
    return False


def _config(tickers: list[str], cle_claude: str = "") -> SimpleNamespace:
    wl = [SimpleNamespace(ticker=t, nom=t, type="ETF") for t in tickers]
    secrets = SimpleNamespace(finnhub_api_key="", fmp_api_key="",
                              anthropic_api_key=cle_claude, model_haiku="haiku-test")
    return SimpleNamespace(watchlist=wl, secrets=secrets,
                           news={"parallelisme": 2, "max_par_ticker": 5,
                                 "anciennete_max_jours": 14})


def _news(t: str) -> list[dict]:
    return [{"id": f"{t}-1", "ticker": t, "datetime": 1, "headline": f"{t} headline",
             "summary": "", "url": "", "source": "test"}]


def _run() -> None:
    sauve = {
        "ds": {k: getattr(update.ds, k) for k in
               ("fetch_prices", "fetch_profil", "fetch_news")},
        "classer": update.llm.classer_news,
        "sonnet": update.llm.synthese_et_reco,
        "briefing": briefing.construire_briefing,
        "hook": threading.excepthook,
    }
    # Instruments ETF : seuls prix + profil sont interroges (None = indisponible).
    update.ds.fetch_prices = lambda *a, **k: None
    update.ds.fetch_profil = lambda *a, **k: None
    threading.excepthook = lambda args: None   # la _Coupure du scenario B est voulue

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db.DB_PATH = Path(tmp) / "test_sam_invest.db"
            db.init_db()

            # ==============================================================
            # A. Persistance PENDANT la phase fetch, etat, verrou, fin normale
            # ==============================================================
            tickers = ["AAA", "BBB", "CCC", "DDD"]
            bloque = threading.Event()   # CCC et DDD attendent ici

            def fetch_news_a(t, *a, **k):
                if t in ("CCC", "DDD"):
                    bloque.wait(15)
                return _news(t)

            update.ds.fetch_news = fetch_news_a
            cfg = _config(tickers)

            t0 = time.time()
            th = update.lancer_refresh_arriere_plan(cfg, "news")
            assert th is not None, "le lancement doit etre accepte sur base vierge"
            assert time.time() - t0 < 2, "le lancement ne doit pas attendre la fin du travail"

            # AAA et BBB sont ecrits alors que CCC/DDD n'ont PAS fini : c'est tout
            # l'enjeu (avant, rien n'etait ecrit tant que la phase n'etait pas close).
            assert _attendre(lambda: len(db.get_news("AAA")) == 1 and len(db.get_news("BBB")) == 1), \
                "AAA/BBB doivent etre en base avant la fin de la phase"
            assert db.get_news("CCC") == [] and db.get_news("DDD") == []

            e = db.etat_refresh()
            assert e["statut"] == "en_cours" and e["kind"] == "news"
            assert _attendre(lambda: db.etat_refresh()["progression"] == "News 2/4"), \
                f"progression attendue 'News 2/4', lue {db.etat_refresh()['progression']!r}"

            # Verrou : un 2e lancement pendant le cycle vivant est refuse.
            assert update.lancer_refresh_arriere_plan(cfg, "news") is None

            bloque.set()
            th.join(15)
            assert not th.is_alive()
            e = db.etat_refresh()
            assert e["statut"] == "termine", e
            assert all(len(db.get_news(t)) == 1 for t in tickers)

            # ==============================================================
            # B. Classement Haiku ecrit ticker par ticker (phase 2)
            # ==============================================================
            attente_d = threading.Event()
            update.ds.fetch_news = lambda t, *a, **k: _news(t)

            def classer(secrets, ticker, items):
                if ticker == "DDD":
                    attente_d.wait(15)
                return [{"headline": it["headline"], "categorie": "x", "sentiment": "neutre",
                         "resume": ticker} for it in items]

            update.llm.classer_news = classer
            th = update.lancer_refresh_arriere_plan(_config(tickers, cle_claude="cle-test"), "news")
            assert th is not None
            assert _attendre(lambda: all(db.get_news_analysis(t) for t in ("AAA", "BBB", "CCC"))), \
                "les classements deja payes doivent etre en base avant la fin de la phase"
            assert db.get_news_analysis("DDD") is None
            attente_d.set()
            th.join(15)
            assert db.etat_refresh()["statut"] == "termine"
            assert db.get_news_analysis("DDD") is not None

            # ==============================================================
            # C. Coupure brutale en pleine phase : 'interrompu', acquis conserves,
            #    verrou libere
            # ==============================================================
            for t in tickers:
                db.replace_news(t, [])   # on repart d'une base de news vide

            def fetch_news_c(t, *a, **k):
                if t == "DDD":
                    time.sleep(0.5)       # laisse AAA/BBB/CCC se terminer d'abord
                    raise _Coupure()
                return _news(t)

            update.ds.fetch_news = fetch_news_c
            update.llm.classer_news = sauve["classer"]
            th = update.lancer_refresh_arriere_plan(_config(tickers), "news")
            assert th is not None
            th.join(15)
            assert not th.is_alive()
            e = db.etat_refresh()
            assert e["statut"] == "interrompu", e
            for t in ("AAA", "BBB", "CCC"):
                assert len(db.get_news(t)) == 1, f"{t} : acquis perdu apres la coupure"
            assert db.get_news("DDD") == []

            # Le cycle interrompu ne bloque pas : on peut relancer et finir.
            update.ds.fetch_news = lambda t, *a, **k: _news(t)
            th = update.lancer_refresh_arriere_plan(_config(tickers), "news")
            assert th is not None, "un cycle interrompu doit liberer le verrou"
            th.join(15)
            assert db.etat_refresh()["statut"] == "termine"
            assert len(db.get_news("DDD")) == 1

            # ==============================================================
            # D. Cycle 'global_analyse' : la synthese Sonnet tourne dans le meme
            #    thread et finit en base (briefing_cache), sans la session
            # ==============================================================
            briefing.construire_briefing = lambda config: {"instruments": []}
            fige = threading.Event()

            def sonnet_ok(secrets, donnees, progress=None):
                progress(10, '{"global": "x", "AAA": {}')   # 1 ticker apparu dans le flux
                fige.wait(15)
                return {"global": "Vue d'ensemble", "instruments": {"AAA": {"reco": "garder"}}}

            update.llm.synthese_et_reco = sonnet_ok
            cfg = _config(tickers, cle_claude="cle-test")
            update.llm.classer_news = classer     # classement Haiku non bloquant (DDD libre)
            attente_d.set()
            th = update.lancer_refresh_arriere_plan(cfg, "global_analyse")
            assert th is not None
            assert _attendre(lambda: db.etat_refresh()["progression"] == "Synthese 1/4"), \
                f"progression synthese attendue, lue {db.etat_refresh()['progression']!r}"
            assert db.etat_refresh()["statut"] == "en_cours"
            fige.set()
            th.join(15)
            e = db.etat_refresh()
            assert e["statut"] == "termine" and "Synthese OK" in e["resume"], e
            cache = db.get_briefing_cache()
            assert cache and cache["global"] == "Vue d'ensemble"

            # Sonnet n'a pas repondu : les donnees restent, le cycle le dit.
            for t in tickers:
                db.replace_news(t, [])
            db.save_briefing_cache("2000-01-01T00:00:00+00:00", None, None, None, "ancien", "{}")
            update.llm.synthese_et_reco = lambda *a, **k: None
            th = update.lancer_refresh_arriere_plan(cfg, "global_analyse")
            assert th is not None
            th.join(15)
            e = db.etat_refresh()
            assert e["statut"] == "termine" and "Synthese manquante" in e["resume"], e
            assert all(len(db.get_news(t)) == 1 for t in tickers), "les donnees doivent rester"
    finally:
        for k, f in sauve["ds"].items():
            setattr(update.ds, k, f)
        update.llm.classer_news = sauve["classer"]
        update.llm.synthese_et_reco = sauve["sonnet"]
        briefing.construire_briefing = sauve["briefing"]
        threading.excepthook = sauve["hook"]

    print("OK - test_refresh_resilient : tous les cas passent.")


if __name__ == "__main__":
    _run()
