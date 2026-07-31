"""Test de non-regression du classement des resultats de recherche.

Lancement : python -m tests.test_classement_bourses  (aucune dependance externe)

Verifie que :
  - les cotations relais (Boersen regionales allemandes) et marginales (MTF,
    OTC, orderbook international) passent DERRIERE la cotation principale ;
  - une place inconnue est presumee principale (liste noire, jamais blanche) :
    on n'enterre pas une cotation legitime non prevue ;
  - le tri final classe par rang puis par pertinence Yahoo decroissante.

Contexte : "Peugeot" proposait FFP.MU (Munich) en premier, dont le `.info`
Yahoo est vide -> diagnostic avec WACC / PER / PBR a "n/d".
"""

from __future__ import annotations

from sam_invest.data_sources import _norm_result, _rang_bourse


def _run() -> None:
    # 1. Rangs : principale < relais regional < MTF/OTC.
    for code in ("PAR", "GER", "NYQ", "NMS", "AMS", "MIL", "LSE", "HKG"):
        assert _rang_bourse(code) == 0, code
    for code in ("MUN", "FRA", "DUS", "STU", "BER", "HAM", "HAN"):
        assert _rang_bourse(code) == 1, code
    for code in ("DXE", "CXE", "IOB", "PNK", "OQX", "TLO"):
        assert _rang_bourse(code) == 2, code

    # 2. Place inconnue -> presumee principale (liste NOIRE, pas blanche).
    assert _rang_bourse("XYZ") == 0
    assert _rang_bourse("") == 0
    assert _rang_bourse(None) == 0

    # 3. Casse indifferente (Yahoo renvoie parfois le code en minuscules).
    assert _rang_bourse("mun") == 1

    # 4. _norm_result porte le rang, et filtre toujours les non actions/ETF.
    r = _norm_result("FFP.MU", "Peugeot Invest", "Munich", "EQUITY", "MUN", 20001)
    assert r["rang"] == 1 and r["type"] == "action" and r["score"] == 20001
    assert _norm_result("EURUSD=X", "EUR/USD", "CCY", "CURRENCY", "CCY", 1) is None

    # 5. Score absent ou non numerique -> 0.0, jamais une exception.
    assert _norm_result("AI.PA", "Air Liquide", "Paris", "EQUITY", "PAR", None)["score"] == 0.0

    # 6. Tri final : rang d'abord, pertinence Yahoo ensuite.
    brut = [
        _norm_result("FFP.MU", "Peugeot Invest", "Munich", "EQUITY", "MUN", 20001),
        _norm_result("SFFFF", "Peugeot Invest", "OTC Markets", "EQUITY", "PNK", 20001),
        _norm_result("PEUG.PA", "Peugeot Invest", "Paris", "EQUITY", "PAR", 20001),
        _norm_result("STLA", "Stellantis", "NYSE", "EQUITY", "NYQ", 20062),
    ]
    brut.sort(key=lambda x: (x["rang"], -x["score"]))
    assert [x["symbol"] for x in brut] == ["STLA", "PEUG.PA", "FFP.MU", "SFFFF"]

    print("OK - test_classement_bourses : tous les cas passent.")


if __name__ == "__main__":
    _run()
