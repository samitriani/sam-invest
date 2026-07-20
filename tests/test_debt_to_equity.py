"""Test de non-regression du ratio dette/capitaux (fix des fausses alertes).

Lancement : python -m tests.test_debt_to_equity  (aucune dependance externe)

Verifie que :
  - le bilan (Total Debt / Stockholders Equity) est prioritaire quand dispo ;
  - a defaut, info['debtToEquity'] est traite comme un POURCENTAGE (/100) dans
    TOUS les cas, y compris les petites valeurs (6.55 -> 0.0655) qui, avec
    l'ancienne heuristique '> 10', declenchaient de fausses alertes.
"""

from __future__ import annotations

import pandas as pd

from sam_invest.data_sources import _debt_to_equity_yf


class _FakeTicker:
    """Imite yfinance.Ticker.balance_sheet (DataFrame colonnes=exercices)."""

    def __init__(self, balance_sheet):
        self.balance_sheet = balance_sheet


def _bs(total_debt, equity):
    return pd.DataFrame({"2025": {"Total Debt": total_debt, "Stockholders Equity": equity}})


def _run() -> None:
    # 1. Bilan disponible -> calcul direct, prioritaire sur info.
    tk = _FakeTicker(_bs(90.0, 100.0))
    assert abs(_debt_to_equity_yf(tk, {"debtToEquity": 180}) - 0.9) < 1e-9

    # 2. Pas de bilan -> repli sur info, TOUJOURS en pourcentage.
    empty = _FakeTicker(pd.DataFrame())
    cas = {6.55: 0.0655, 180.0: 1.8, 0.9: 0.009, 250.0: 2.5}
    for pct, attendu in cas.items():
        got = _debt_to_equity_yf(empty, {"debtToEquity": pct})
        assert abs(got - attendu) < 1e-9, f"debtToEquity={pct} -> {got}, attendu {attendu}"

    # 3. Donnee absente -> None (pas d'alerte fabriquee).
    assert _debt_to_equity_yf(empty, {}) is None

    # 4. Equity nulle/negative dans le bilan -> repli propre sur info.
    tk0 = _FakeTicker(_bs(50.0, 0.0))
    assert abs(_debt_to_equity_yf(tk0, {"debtToEquity": 180}) - 1.8) < 1e-9

    print("OK - test_debt_to_equity : tous les cas passent.")


if __name__ == "__main__":
    _run()
