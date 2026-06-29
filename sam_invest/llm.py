"""Couche JUGEMENT - API Claude. SEUL module autorise a appeler un LLM.

Role strictement limite :
  - Haiku  : resumer/classer les news (categorie + tonalite + resume court).
  - Sonnet : rediger une synthese en langage naturel A PARTIR des chiffres
             deja calcules par le code.

Garde-fous (rappeles dans les prompts) :
  - Claude ne calcule ni n'invente AUCUN chiffre (prix, ratio, %, valeur).
    Il ne fait que reformuler les chiffres qu'on lui donne.
  - Claude ne donne JAMAIS de verdict acheter/vendre/conserver.

Si la cle ANTHROPIC_API_KEY est absente ou l'appel echoue, les fonctions
renvoient une valeur de repli (None / texte d'indisponibilite) sans planter.
"""

from __future__ import annotations

import json
import re

from .config import Secrets

try:
    from anthropic import Anthropic
except Exception:  # pragma: no cover
    Anthropic = None


def _client(secrets: Secrets):
    if Anthropic is None or not secrets.anthropic_api_key:
        return None
    try:
        return Anthropic(api_key=secrets.anthropic_api_key)
    except Exception:
        return None


# ==========================================================================
# Haiku - classification / resume des news
# ==========================================================================
NEWS_SYSTEM = (
    "Tu es un assistant qui classe et resume des actualites financieres pour un "
    "investisseur particulier. REGLES STRICTES : "
    "(1) Tu ne produis, ne calcules et n'inventes AUCUN chiffre (prix, %, ratio). "
    "Si une news contient un chiffre, tu peux le citer tel quel, mais tu n'en "
    "deduis rien de quantitatif. "
    "(2) Tu ne donnes JAMAIS de recommandation acheter/vendre/conserver. "
    "(3) Tu reponds uniquement en JSON valide, sans texte autour."
)


def classer_news(secrets: Secrets, ticker: str, news_items: list[dict]) -> list[dict] | None:
    """Renvoie une liste [{headline, categorie, tonalite, resume}] ou None si indispo.

    tonalite ∈ {positif, neutre, negatif} ; categorie libre courte
    (resultats, produit, reglementaire, macro, dirigeant, autre).
    """
    client = _client(secrets)
    if client is None or not news_items:
        return None

    # On limite le payload envoye au modele (titres + resumes tronques).
    compact = [
        {"headline": n.get("headline", ""), "resume_source": (n.get("summary", "") or "")[:500]}
        for n in news_items
    ]
    user = (
        f"Ticker concerne : {ticker}\n"
        f"Voici des actualites recentes (JSON). Pour CHACUNE, renvoie un objet avec : "
        f'"headline" (reprends le titre), "categorie" (un mot parmi : resultats, produit, '
        f"reglementaire, macro, dirigeant, autre), \"tonalite\" (positif|neutre|negatif du point "
        f'de vue de l\'entreprise), "resume" (1 phrase factuelle, sans chiffre invente).\n'
        f"Reponds STRICTEMENT par un tableau JSON, meme ordre que l'entree.\n\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )
    try:
        resp = client.messages.create(
            model=secrets.model_haiku,
            max_tokens=1500,
            system=NEWS_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text.strip()
        text = _strip_code_fence(text)
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        return None
    return None


# ==========================================================================
# Sonnet - synthese en langage naturel A PARTIR des chiffres
# ==========================================================================
SYNTHESE_SYSTEM = (
    "Tu es un PEDAGOGUE financier qui parle a un investisseur particulier DEBUTANT (il ne "
    "maitrise pas le vocabulaire boursier) au sujet de sa watchlist (actions + ETF, Tech et "
    "pays emergents). On te fournit un ETAT DE FAIT deja chiffre par un programme (cours, "
    "variations, signaux techniques, flags de regles, evenements, revisions d'estimations, "
    "news resumees). "
    "Pour CHAQUE instrument tu produis DEUX choses : "
    "(A) un BRIEFING en langage simple ; (B) une RECOMMANDATION codee par un fruit : "
    "'concombre'=ACHETER, 'orange'=MAINTENIR, 'tomate'=VENDRE. "
    "REGLES STRICTES : "
    "(1) Tu n'inventes, ne calcules ni ne modifies AUCUN chiffre. "
    "(2) Le BRIEFING NE RECITE PAS les chiffres bruts (deja affiches a l'ecran) : il EXPLIQUE "
    "ce qu'ils veulent dire. Vulgarisation maximale, comme a ta grand-mere, sans jargon non "
    "traduit. Exemples : 'RSI 37, neutre' -> 'le titre n'est ni surchauffe ni brade' ; "
    "'drawdown -18%' -> 'il a perdu 18% depuis son plus haut de l'annee' ; 'cours sous la "
    "SMA50' -> 'il evolue sous sa moyenne des dernieres semaines, signe d'un coup de mou' ; "
    "'revisions d'EPS positives' -> 'de plus en plus d'analystes relevent leurs previsions de "
    "benefices, signe de confiance'. "
    "(3) Dis ce que la situation IMPLIQUE et CE QU'IL FAUT SURVEILLER. Signale simplement les "
    "contradictions de signaux (ex: tendance de fond positive mais forte dette). "
    "(4) La RECOMMANDATION (fruit) doit decouler des signaux fournis et rester coherente avec "
    "le briefing (le briefing justifie implicitement le fruit). "
    "Style : francais simple, phrases courtes, ton chaleureux et concret."
)


def synthese_et_reco(secrets: Secrets, donnees_briefing: dict) -> dict | None:
    """UN seul appel Sonnet : vue d'ensemble + par instrument {fruit + briefing}.

    Renvoie {"global": str, "instruments": {ticker: {"fruit": str, "briefing": str}}}
    ou None si indisponible. Combine l'ancien briefing pedagogique et la reco "fruit".
    """
    from .logs import log

    client = _client(secrets)
    if client is None:
        log("synthese_et_reco: client None (ANTHROPIC_API_KEY absente ?)", "warning")
        return None

    n_inst = len(donnees_briefing.get("instruments", []))
    log(f"synthese_et_reco: appel Sonnet (model={secrets.model_sonnet}, instruments={n_inst})")

    user = (
        "Voici l'etat de fait chiffre de la watchlist (JSON). Reponds STRICTEMENT en JSON avec "
        "exactement deux cles :\n"
        "- \"global\" : 3 a 4 phrases de vue d'ensemble en langage simple (ambiance generale + "
        "1-2 points d'attention). Si 'delta_depuis_derniere_visite' n'est pas vide, COMMENCE "
        "par expliquer ce qui a change depuis la derniere visite.\n"
        "- \"instruments\" : un objet {\"<ticker>\": {\"fruit\": \"concombre|orange|tomate\", "
        "\"briefing\": \"<1 a 2 phrases d'interpretation simple>\"}} couvrant CHAQUE ticker du "
        "champ 'instruments', avec les cles dans cet ordre (fruit puis briefing). Le briefing "
        "interprete la situation pour un debutant (sans reciter les chiffres) et justifie "
        "implicitement le fruit. Reste bref pour que la reponse tienne en entier.\n"
        "Ne renvoie que le JSON, sans texte autour.\n\n"
        f"{json.dumps(donnees_briefing, ensure_ascii=False, default=str)}"
    )

    try:
        resp = client.messages.create(
            model=secrets.model_sonnet,
            max_tokens=8000,
            system=SYNTHESE_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        log(f"synthese_et_reco: ERREUR appel API: {type(e).__name__}: {e}", "error")
        return None

    try:
        raw = resp.content[0].text.strip()
    except Exception as e:
        log(f"synthese_et_reco: reponse illisible: {e}", "error")
        return None

    stop = getattr(resp, "stop_reason", None)
    log(f"synthese_et_reco: reponse recue len={len(raw)} stop_reason={stop}")
    if stop == "max_tokens":
        log("synthese_et_reco: ATTENTION reponse TRONQUEE (max_tokens).", "warning")

    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except Exception as e:
        log(f"synthese_et_reco: JSON INVALIDE ({e}) -> recuperation partielle. "
            f"fin={text[-120:]!r}", "warning")
        data = _salvage_combine(text)
        if data["instruments"]:
            log(f"synthese_et_reco: recuperation OK ({len(data['instruments'])} sur {n_inst}).")
            return data
        log("synthese_et_reco: recuperation impossible.", "error")
        return None

    if isinstance(data, dict):
        instruments = _normaliser_instruments(data.get("instruments", {}) or {})
        log(f"synthese_et_reco: parse OK (global={len(data.get('global','') or '')} chars, "
            f"instruments={len(instruments)})")
        return {"global": data.get("global", "") or "", "instruments": instruments}

    log("synthese_et_reco: JSON parse mais ce n'est pas un objet/dict", "error")
    return None


def _normaliser_instruments(raw: dict) -> dict:
    """Garantit que chaque entree est {'fruit': str, 'briefing': str}."""
    out = {}
    for t, v in raw.items():
        if isinstance(v, dict):
            out[t] = {"fruit": str(v.get("fruit", "") or "").lower(),
                      "briefing": v.get("briefing", "") or ""}
        else:  # tolerance si le modele renvoie juste un texte
            out[t] = {"fruit": "", "briefing": str(v)}
    return out


def _salvage_combine(text: str) -> dict:
    """Recupere autant que possible d'un JSON {global, instruments:{t:{fruit,briefing}}} tronque.

    Extrait 'global' et tous les blocs "TICKER": {"fruit": "...", "briefing": "..."} COMPLETS
    (l'entree coupee en fin de reponse est ignoree).
    """
    out: dict = {"global": "", "instruments": {}}

    mg = re.search(r'"global"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if mg:
        try:
            out["global"] = json.loads('"' + mg.group(1) + '"')
        except Exception:
            out["global"] = mg.group(1)

    bloc = re.compile(
        r'"([A-Za-z0-9.\^\-]{1,15})"\s*:\s*\{\s*'
        r'"fruit"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*'
        r'"briefing"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        re.DOTALL,
    )
    for m in bloc.finditer(text):
        key, fruit, brief = m.group(1), m.group(2), m.group(3)
        if key in ("global", "instruments"):
            continue
        try:
            brief = json.loads('"' + brief + '"')
        except Exception:
            pass
        out["instruments"][key] = {"fruit": fruit.lower(), "briefing": brief}
    return out


def _strip_code_fence(text: str) -> str:
    """Retire d'eventuels ```json ... ``` autour de la reponse."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        # enleve un eventuel 'json' restant en tete
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()
