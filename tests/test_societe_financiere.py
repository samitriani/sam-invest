"""Test de non-regression du garde-fou "societe financiere".

Lancement : python -m tests.test_societe_financiere  (aucune dependance externe)

Verifie que :
  - les banques, assureurs et holdings sont detectes, par le secteur Yahoo OU
    par la forme de leurs etats (ni marge brute, ni resultat operationnel, ni
    actif courant) — ce second signal rattrape les tickers ou `.info` est vide ;
  - une societe industrielle n'est JAMAIS marquee (pas de faux positif) ;
  - le drapeau 🚬 ne s'affiche pas sur une valeur absente ("🚬 n/d" = bruit).

Contexte : Peugeot Invest (holding) affichait une "marge nette" de 82,6% sans
avertissement, alors que son resultat vient des variations de juste valeur du
portefeuille et ne transite pas par le chiffre d'affaires.
"""

from __future__ import annotations

from sam_invest.diagnostic import _dt, _est_financiere


def _run() -> None:
    # 1. Detection par le secteur Yahoo, casse et espaces indifferents.
    for secteur in ("Financial Services", "financial services", "  Financials "):
        assert _est_financiere(secteur, 1.0, 2.0, 3.0) is True, secteur

    # 2. Detection par la FORME des etats, secteur absent (cotation secondaire).
    #    Reproduit FFP.MU : Yahoo ne renvoie aucun secteur.
    assert _est_financiere(None, None, None, None) is True

    # 3. Industrielle : ni le secteur, ni la forme des etats ne declenchent.
    #    Profil reel de AI.PA / NVDA / MC.PA : les trois postes sont presents.
    assert _est_financiere("Basic Materials", 1.0, 2.0, 3.0) is False
    assert _est_financiere("Technology", 1.0, 2.0, 3.0) is False

    # 4. Pas de faux positif sur une couverture yfinance partielle : il faut que
    #    les TROIS postes manquent, pas un seul.
    assert _est_financiere("Technology", None, 2.0, 3.0) is False
    assert _est_financiere("Technology", None, None, 3.0) is False

    # 5. _dt : le drapeau exige une condition VRAIE *et* une valeur presente.
    assert _dt(True, 0.826) is True
    assert _dt(True, None) is False      # pas de "🚬 n/d"
    assert _dt(False, 0.826) is False
    assert _dt(True, 0.0) is True        # 0 est une valeur, pas une absence

    print("OK - test_societe_financiere : tous les cas passent.")


if __name__ == "__main__":
    _run()
