"""Orchestration des mises a jour, decoupee par type pour maitriser le cout API.

Trois operations independantes :
  - update_donnees : prix + fondamentaux. 100% deterministe, AUCUN appel Claude.
  - update_news    : recup news + classement par Claude Haiku (cout Haiku).
  - update_global  : donnees + news (jamais la synthese Sonnet).

La synthese (Sonnet) est generee separement, a la demande, dans l'onglet Briefing
(voir llm.synthese_briefing). Ainsi l'utilisateur controle precisement sa
consommation : rafraichir les cours ne coute rien, recharger les news coute du
Haiku, et seul le bouton de briefing declenche du Sonnet.

Robuste : un echec sur un ticker n'interrompt pas les autres. Un callback de
progression permet a l'UI Streamlit d'afficher l'avancement.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from . import data_sources as ds
from . import db, llm
from .config import AppConfig
from .logs import log

ProgressFn = Callable[[float, str], None]  # (fraction 0..1, message)


def _noop(_f: float, _m: str) -> None:
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ==========================================================================
# Donnees de marche : prix + fondamentaux (AUCUN appel Claude)
# ==========================================================================
def _maj_donnees_instrument(p, s, progress: ProgressFn, base: float, span: float) -> tuple[dict, list[str]]:
    """Prix + fondamentaux + evenements + avis analystes + profil pour UN instrument.

    `base`/`span` positionnent la progression dans la barre globale (0..1).
    Renvoie (compteurs {prix, fond, evt, avis, prof, action}, details).
    """
    t = p.ticker
    cnt = {"prix": 0, "fond": 0, "evt": 0, "avis": 0, "prof": 0,
           "action": 1 if p.type.lower() == "action" else 0}
    details: list[str] = []

    progress(base, f"{t} : prix...")
    try:
        res = ds.fetch_prices(t, s.finnhub_api_key, s.fmp_api_key)
        if res and res.get("quote"):
            if res.get("history"):
                db.upsert_prices(t, res["history"])
            db.upsert_quote(res["quote"])
            cnt["prix"] = 1
            details.append(f"{t} : prix OK ({res['quote'].get('source')}).")
        else:
            details.append(f"{t} : prix indisponible (aucune source).")
    except Exception as e:
        details.append(f"{t} : erreur prix ({e}).")

    # Fondamentaux + evenements/estimations + avis : actions uniquement.
    if cnt["action"]:
        progress(base + 0.4 * span, f"{t} : fondamentaux...")
        try:
            f = ds.fetch_fundamentals(t, s.finnhub_api_key, s.fmp_api_key)
            if f:
                db.upsert_fundamentals(f)
                cnt["fond"] = 1
                details.append(f"{t} : fondamentaux OK ({f.get('source')}).")
            else:
                details.append(f"{t} : fondamentaux indisponibles.")
        except Exception as e:
            details.append(f"{t} : erreur fondamentaux ({e}).")

        progress(base + 0.7 * span, f"{t} : evenements & estimations...")
        try:
            ev = ds.fetch_events_estimates(t)
            if ev:
                db.upsert_events_estimates(ev)
                cnt["evt"] = 1
                details.append(f"{t} : evenements/estimations OK "
                               f"(resultats {ev.get('earnings_date') or 'n/d'}).")
            else:
                details.append(f"{t} : evenements/estimations indisponibles.")
        except Exception as e:
            details.append(f"{t} : erreur evenements/estimations ({e}).")

        progress(base + 0.8 * span, f"{t} : avis analystes...")
        try:
            ar = ds.fetch_analyst_ratings(t)
            if ar:
                ar = dict(ar)
                ar["trend"] = json.dumps(ar.get("trend") or [], ensure_ascii=False)
                ar["upgrades"] = json.dumps(ar.get("upgrades") or [], ensure_ascii=False)
                db.upsert_analyst_ratings(ar)
                cnt["avis"] = 1
                details.append(f"{t} : avis analystes OK.")
            else:
                details.append(f"{t} : avis analystes indisponibles.")
        except Exception as e:
            details.append(f"{t} : erreur avis analystes ({e}).")

    # Profil / fondamentaux d'affichage : tous les instruments (actions ET ETF).
    progress(base + 0.85 * span, f"{t} : profil...")
    try:
        prof = ds.fetch_profil(t, p.type)
        if prof:
            db.upsert_profile(prof["ticker"], prof["asof"], prof["type"],
                              json.dumps(prof["payload"], ensure_ascii=False), prof["source"])
            cnt["prof"] = 1
        else:
            details.append(f"{t} : profil indisponible.")
    except Exception as e:
        details.append(f"{t} : erreur profil ({e}).")

    return cnt, details


def update_donnees(config: AppConfig, progress: ProgressFn | None = None) -> dict:
    progress = progress or _noop
    db.init_db()
    s = config.secrets
    instruments = config.watchlist
    if not instruments:
        db.log_update(_now(), "donnees", "vide", "Watchlist vide.")
        return {"status": "vide", "kind": "donnees",
                "resume": "Watchlist vide.", "details": ["Remplis config.yaml."]}

    n = len(instruments)
    details: list[str] = []
    tot = {"prix": 0, "fond": 0, "evt": 0, "avis": 0, "prof": 0, "action": 0}

    for i, p in enumerate(instruments):
        cnt, det = _maj_donnees_instrument(p, s, progress, base=i / n, span=1 / n)
        for k in tot:
            tot[k] += cnt[k]
        details += det
        # Sans effet hors d'un cycle suivi ('en_cours') : l'etat n'est ecrit que
        # par un lancement en arriere-plan (voir lancer_refresh_arriere_plan).
        db.maj_progression_refresh(f"Donnees {i + 1}/{n}")

    progress(1.0, "Termine.")
    asof = _now()
    resume = (f"Prix OK {tot['prix']}/{n}, fondamentaux {tot['fond']}/{tot['action']} actions, "
              f"evenements {tot['evt']}/{tot['action']}, avis analystes {tot['avis']}/{tot['action']}, "
              f"profils {tot['prof']}/{n}.")
    db.log_update(asof, "donnees", "ok", resume)
    _historiser_flags(config, asof)
    return {"status": "ok", "kind": "donnees", "asof": asof, "resume": resume, "details": details}


def _historiser_flags(config: AppConfig, asof: str) -> None:
    """Recalcule les flags (deterministe) et les historise pour l'affichage
    'nouveau vs persistant'. Ne doit jamais casser une mise a jour de donnees."""
    try:
        from . import rules, signals
        snaps = signals.construire_snapshots(config)
        flags = rules.tous_les_flags(config, snaps)
        db.enregistrer_flags(
            [{"ticker": f.ticker, "regle": f.regle, "severite": f.severite, "detail": f.detail}
             for f in flags],
            asof,
        )
    except Exception:
        pass


def update_donnees_instrument(config: AppConfig, ticker: str,
                              progress: ProgressFn | None = None) -> dict:
    """Mise a jour des donnees d'UN SEUL instrument (auto-recuperation a la selection).

    N'ecrit PAS dans update_log : la fraicheur globale (briefing, legendes) reste
    celle de la derniere mise a jour complete ; la fraicheur par instrument se lit
    dans quotes.asof / profile.asof.
    """
    progress = progress or _noop
    db.init_db()
    p = next((i for i in config.watchlist if i.ticker == ticker), None)
    if p is None:
        return {"status": "erreur", "kind": "donnees_instrument",
                "resume": f"{ticker} absent de la watchlist.", "details": []}
    cnt, details = _maj_donnees_instrument(p, config.secrets, progress, base=0.0, span=1.0)
    progress(1.0, "Termine.")
    ok = cnt["prix"] or cnt["prof"]
    return {"status": "ok" if ok else "erreur", "kind": "donnees_instrument",
            "asof": _now(), "resume": f"{ticker} : "
            + ("donnees recuperees." if ok else "aucune donnee recuperee."),
            "details": details}


# ==========================================================================
# News : recuperation + classement par Claude Haiku (cout Haiku)
# ==========================================================================
# La recup de news (reseau) et le classement (Claude) sont 100% I/O-bound :
# on les parallelise via des threads (le GIL est relache pendant l'attente
# reseau). Regle de securite SQLite : TOUT le reseau/LLM se fait dans les
# threads, mais TOUTES les ecritures DB restent dans le thread principal
# (SQLite tolere mal les ecritures concurrentes).
def _existing_analysis_map(ticker: str) -> dict[str, dict]:
    """Analyses deja en base pour ce ticker, indexees par titre original."""
    na = db.get_news_analysis(ticker)
    if not na or not na.get("payload"):
        return {}
    try:
        return {a.get("headline", ""): a for a in json.loads(na["payload"])}
    except Exception:
        return {}


def update_news(config: AppConfig, progress: ProgressFn | None = None) -> dict:
    progress = progress or _noop
    db.init_db()
    s = config.secrets
    instruments = config.watchlist
    if not instruments:
        db.log_update(_now(), "news", "vide", "Watchlist vide.")
        return {"status": "vide", "kind": "news",
                "resume": "Watchlist vide.", "details": ["Remplis config.yaml."]}

    n = len(instruments)
    details: list[str] = []
    ok_news = ok_analyse = 0
    classer = bool(s.anthropic_api_key)
    max_items = int(config.news.get("max_par_ticker", 10))
    days = int(config.news.get("anciennete_max_jours", 14))
    workers = max(1, min(int(config.news.get("parallelisme", 8)), n))

    # ---- Progression thread-safe : phase fetch = 0..0.5, phase classement = 0.5..1
    lock = threading.Lock()
    counters = {"fetch": 0, "classe": 0}

    def _tick(phase: str, total: int, label: str) -> None:
        with lock:
            counters[phase] += 1
            done = counters[phase]
        if classer:
            frac = done / total * 0.5 + (0.5 if phase == "classe" else 0.0)
        else:
            frac = done / total
        prefix = "Classement" if phase == "classe" else "News"
        db.maj_progression_refresh(f"{prefix} {done}/{total}")
        progress(min(frac, 1.0), f"{prefix} {done}/{total} — {label}")

    # ======================================================================
    # PHASE 1 : recuperation des news, en parallele
    # ======================================================================
    progress(0.0, "Recuperation des news...")
    fetched: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def _fetch(ticker: str) -> tuple[str, list[dict]]:
        return ticker, ds.fetch_news(ticker, max_items, days, s.finnhub_api_key, s.fmp_api_key)

    # Ecriture DB ticker par ticker, des qu'un fetch aboutit (et non en bloc apres
    # tous les fetchs) : si le rafraichissement est interrompu, les news deja
    # recuperees restent en base. Les workers ne font que du reseau ; seul ce
    # thread (celui qui a appele update_news) ecrit, d'ou une seule ecriture a la fois.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch, p.ticker): p.ticker for p in instruments}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                _, items = fut.result()
            except Exception as e:
                fetched[t] = []
                errors[t] = str(e)
                details.append(f"{t} : erreur news ({e}).")
                _tick("fetch", n, f"{t} : erreur")
                continue
            fetched[t] = items
            try:
                db.replace_news(t, items)
            except Exception as e:
                details.append(f"{t} : erreur enregistrement news ({e}).")
                _tick("fetch", n, f"{t} : erreur")
                continue
            if items:
                ok_news += 1
                details.append(f"{t} : {len(items)} news ({items[0].get('source')}).")
            else:
                details.append(f"{t} : aucune news recente.")
            _tick("fetch", n, f"{t} : {len(items)} trouvees")

    # ======================================================================
    # PHASE 2 : classement Claude Haiku, en parallele
    # Cache (#4) : on ne renvoie a Haiku que les news JAMAIS classees ;
    # les autres sont reprises telles quelles depuis la base.
    # ======================================================================
    if classer:
        targets = [p.ticker for p in instruments if fetched.get(p.ticker) and p.ticker not in errors]
        # Prechargement des analyses existantes dans le thread principal (lecture DB).
        existing = {t: _existing_analysis_map(t) for t in targets}

        def _classify(ticker: str) -> tuple[str, list[dict]]:
            items = fetched[ticker]
            seen = existing.get(ticker, {})
            nouveaux = [it for it in items if it.get("headline", "") not in seen]
            frais: dict[str, dict] = {}
            if nouveaux:
                res = llm.classer_news(s, ticker, nouveaux)
                if res:
                    frais = {a.get("headline", ""): a for a in res}
            # Reconstruction dans l'ordre des news actuelles : analyse fraiche > cache.
            merged = []
            for it in items:
                h = it.get("headline", "")
                a = frais.get(h) or seen.get(h)
                if a:
                    merged.append(a)
            return ticker, merged

        m = len(targets)
        if m:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_classify, t): t for t in targets}
                for fut in as_completed(futs):
                    t = futs[fut]
                    try:
                        _, merged = fut.result()
                        # Ecriture immediate : un classement Haiku deja paye ne doit
                        # pas attendre la fin des autres tickers pour etre sauve.
                        if merged:
                            db.upsert_news_analysis(
                                t, _now(), json.dumps(merged, ensure_ascii=False), s.model_haiku,
                            )
                            ok_analyse += 1
                    except Exception as e:
                        details.append(f"{t} : erreur classement ({e}).")
                    _tick("classe", m, t)

    progress(1.0, "Termine.")
    asof = _now()
    if classer:
        resume = f"News OK {ok_news}/{n}, classees par Claude Haiku {ok_analyse}/{ok_news}."
    else:
        resume = f"News OK {ok_news}/{n} (classement Claude desactive : pas de cle)."
    db.log_update(asof, "news", "ok", resume)
    return {"status": "ok", "kind": "news", "asof": asof, "resume": resume, "details": details}


# ==========================================================================
# Global : donnees + news (jamais la synthese Sonnet)
# ==========================================================================
def update_global(config: AppConfig, progress: ProgressFn | None = None) -> dict:
    progress = progress or _noop

    def p_data(f: float, m: str) -> None:
        progress(f * 0.5, f"[Donnees] {m}")

    def p_news(f: float, m: str) -> None:
        progress(0.5 + f * 0.5, f"[News] {m}")

    d = update_donnees(config, p_data)
    nws = update_news(config, p_news)
    asof = _now()
    resume = f"{d['resume']} | {nws['resume']}"
    db.log_update(asof, "global", "ok", resume)
    return {"status": "ok", "kind": "global", "asof": asof, "resume": resume,
            "details": d.get("details", []) + nws.get("details", [])}


# ==========================================================================
# Lancement en arriere-plan : survit a la coupure de la connexion du navigateur
# ==========================================================================
def generer_synthese(config: AppConfig, progress: Callable[..., None] | None = None) -> dict:
    """UN appel Sonnet (ou reprise du cache si donnees/news inchangees), SANS Streamlit.

    Extraite de l'UI pour pouvoir tourner dans le thread de rafraichissement :
    le resultat est ecrit en base (`briefing_cache`), donc independant de la
    session qui l'a declenchee. Ne rafraichit rien elle-meme : elle lit l'etat
    courant de la base.

    Renvoie {"status": "ok" | "info" | "erreur", "resume": str, ...} :
      - ok     : + "global", "instruments", "synthese_asof" (texte frais, deja en base) ;
      - info   : + "cache" (donnees/news inchangees : rien regenere, texte du cache) ;
      - erreur : Claude n'a pas repondu (rien ecrit).
    `progress(n_caracteres, texte)` est relaye tel quel au flux Sonnet.
    """
    from .briefing import construire_briefing

    ad = (db.last_update("donnees") or {}).get("asof")
    an = (db.last_update("news") or {}).get("asof")
    dd = construire_briefing(config)
    cache = db.get_briefing_cache()
    if (cache is not None and cache.get("donnees_asof") == ad
            and cache.get("news_asof") == an):
        log("[synthese] donnees/news inchangees -> cache reutilise (pas d'appel Sonnet).")
        return {"status": "info", "cache": cache,
                "resume": "Donnees et news inchangees depuis la derniere synthese : "
                          "texte recupere sans nouvel appel Claude."}

    log(f"[synthese] generation (watchlist={len(config.watchlist)}, "
        f"instruments={len(dd.get('instruments', []))})")
    res = llm.synthese_et_reco(config.secrets, dd, progress=progress)
    if res:
        m = db.last_update()
        synthese_asof = m["asof"] if m else None
        db.save_briefing_cache(
            generated_at=_now(), donnees_asof=ad, news_asof=an, synthese_asof=synthese_asof,
            global_text=res.get("global") or "",
            instruments_json=json.dumps(res.get("instruments") or {}, ensure_ascii=False),
        )
        log(f"[synthese] stockee: global={len(res.get('global') or '')} chars, "
            f"instruments={len(res.get('instruments') or {})}")
        return {"status": "ok", "global": res.get("global"),
                "instruments": res.get("instruments", {}), "synthese_asof": synthese_asof,
                "resume": "Synthese ecrite. Elle est enregistree : tu la retrouveras meme "
                          "apres une coupure ou depuis un autre appareil."}

    log("[synthese] resultat vide -> rien a afficher", "error")
    return {"status": "erreur",
            "resume": "Claude n'a pas repondu : la synthese n'a pas pu etre ecrite. "
                      "Le plus souvent c'est le reseau qui a lache pendant la redaction "
                      "(elle dure 2 a 3 minutes) — reessaie, les cours et actualites "
                      "recuperes, eux, sont deja enregistres. Detail dans "
                      "`data/sam_invest.log`."}


def _synthese_du_cycle(config: AppConfig) -> str:
    """Etape synthese d'un cycle 'global_analyse'. Renvoie le fragment de resume."""
    if not config.secrets.anthropic_api_key:
        return "Synthese manquante (cle Claude absente)."
    tickers = [i.ticker for i in config.watchlist]
    total = max(len(tickers), 1)
    vu = {"n": -1}

    def _prog(_n_chars: int, texte: str = "") -> None:
        done = min(sum(1 for tk in tickers if f'"{tk}"' in texte), total)
        if done != vu["n"]:
            vu["n"] = done
            db.maj_progression_refresh(f"Synthese {done}/{total}")

    db.maj_progression_refresh("Synthese...")
    try:
        cr = generer_synthese(config, _prog)
    except Exception as e:
        log(f"[refresh] synthese en echec : {e}", "error")
        return f"Synthese manquante ({e})."
    if cr["status"] in ("ok", "info"):
        return "Synthese OK."
    return "Synthese manquante (Claude n'a pas repondu)."


def lancer_refresh_arriere_plan(config: AppConfig, kind: str = "global") -> threading.Thread | None:
    """Lance un rafraichissement dans un thread serveur, sans l'attendre.

    Pourquoi : appele dans le script Streamlit du clic, un rafraichissement de
    3-4 min est lie a la connexion du navigateur ; sur mobile (appli mise en
    arriere-plan, wifi -> 4G) cette connexion tombe et le travail est perdu.
    Le thread vit dans le process serveur, partage par toutes les sessions : il
    continue et ecrit en base quoi qu'il arrive au navigateur. L'etat du cycle
    (en_cours / interrompu / termine) est en base, donc lisible depuis n'importe
    quelle session.

    Ce thread devient l'UNIQUE ecrivain SQLite pendant le cycle ; le verrou
    `db.demarrer_refresh` empeche un 2e cycle de lui faire concurrence.

    Renvoie le thread demarre, ou None si un cycle vivant existe deja (aucun
    nouveau travail n'est lance).
    """
    travaux = {"donnees": update_donnees, "news": update_news, "global": update_global,
               "global_analyse": update_global}
    if kind not in travaux:
        raise ValueError(f"kind inconnu : {kind!r}")
    db.init_db()
    if not db.demarrer_refresh(kind):
        return None

    def _travail() -> None:
        statut, resume, details = "interrompu", "Interrompu avant la fin.", None
        try:
            cr = travaux[kind](config)
            statut, resume = "termine", cr.get("resume", "")
            details = json.dumps(cr.get("details", []), ensure_ascii=False)
            if kind == "global_analyse":
                # Les donnees sont deja en base : un echec de synthese ne rend pas
                # le cycle 'interrompu', il est dit dans le resume.
                resume = f"{resume} | {_synthese_du_cycle(config)}"
        except Exception as e:  # le thread ne doit jamais mourir sans clore le cycle
            resume = f"Interrompu : {e}"
            log(f"[refresh] {kind} interrompu : {e}", "error")
        finally:
            try:
                db.terminer_refresh(statut, resume, details=details)
            except Exception as e:
                log(f"[refresh] cloture du cycle impossible : {e}", "error")

    th = threading.Thread(target=_travail, name=f"refresh-{kind}", daemon=True)
    th.start()
    return th
