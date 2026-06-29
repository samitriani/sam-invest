"""Indicateurs techniques - DETERMINISTE, sans LLM, pandas pur.

NB : pandas-ta (upstream) etant incompatible avec numpy 2.x / Python 3.13,
les indicateurs usuels sont recalcules ici en pandas. C'est volontairement
simple et sans dependance fragile (priorite : fiabilite). L'exigence
non-negociable - "tout chiffre calcule par du code, jamais par un LLM" - est
respectee : ces formules sont du code Python verifiable.
"""

from __future__ import annotations

import pandas as pd


def _closes_series(history: list[dict]) -> pd.Series:
    """history = [{'date': 'YYYY-MM-DD', 'close': float}, ...] -> Series indexee par date."""
    if not history:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(history)
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["close"].astype("float64").dropna().sort_index()
    return s


def sma(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    return float(series.tail(window).mean())


def rsi(series: pd.Series, window: int = 14) -> float | None:
    """RSI de Wilder (lissage par moyenne mobile exponentielle)."""
    if len(series) < window + 1:
        return None
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Lissage de Wilder via ewm(alpha=1/window).
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def compute_indicators(history: list[dict]) -> dict:
    """Renvoie un dict d'indicateurs deterministes pour une ligne.

    Toutes les valeurs peuvent etre None si l'historique est trop court.
    """
    s = _closes_series(history)
    out = {
        "n_points": int(len(s)),
        "sma_20": sma(s, 20),
        "sma_50": sma(s, 50),
        "sma_200": sma(s, 200),
        "rsi_14": rsi(s, 14),
        "high_52w": None,
        "low_52w": None,
        "last_close": None,
    }
    if len(s):
        window = s.tail(252)
        out["high_52w"] = float(window.max())
        out["low_52w"] = float(window.min())
        out["last_close"] = float(s.iloc[-1])
    return out
