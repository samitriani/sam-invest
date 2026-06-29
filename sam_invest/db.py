"""Stockage local SQLite (un seul fichier).

Couche donnees pure. Tables :
  - prices          : historique de cloture par ticker/date.
  - quotes          : dernier snapshot (prix, variation seance, 52s) par ticker.
  - fundamentals    : derniers fondamentaux par ticker (CTO surtout).
  - news            : news brutes recuperees.
  - news_analysis   : sortie de la couche jugement (Claude) - clairement separee.
  - update_log      : journal des mises a jour.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "sam_invest.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,           -- 'YYYY-MM-DD'
    close  REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS quotes (
    ticker        TEXT PRIMARY KEY,
    asof          TEXT NOT NULL,    -- ISO datetime de la mise a jour
    last_price    REAL,
    prev_close    REAL,
    change_pct    REAL,             -- variation seance en %
    high_52w      REAL,
    low_52w       REAL,
    drawdown_pct  REAL,             -- (last/high_52w - 1) * 100
    source        TEXT
);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          TEXT PRIMARY KEY,
    asof            TEXT NOT NULL,
    revenue_ttm     REAL,
    revenue_prev    REAL,           -- CA periode precedente (pour YoY)
    revenue_growth  REAL,           -- croissance YoY en %
    net_margin      REAL,           -- marge nette en %
    debt_to_equity  REAL,
    source          TEXT
);

CREATE TABLE IF NOT EXISTS events_estimates (
    ticker          TEXT PRIMARY KEY,
    asof            TEXT NOT NULL,
    earnings_date   TEXT,           -- 'YYYY-MM-DD' prochaine date de resultats
    exdiv_date      TEXT,           -- 'YYYY-MM-DD' prochaine date ex-dividende
    eps_rev_up_30   REAL,           -- nb d'analystes relevant l'EPS (30j)
    eps_rev_down_30 REAL,           -- nb d'analystes abaissant l'EPS (30j)
    eps_rev_up_7    REAL,
    eps_rev_down_7  REAL,
    eps_rev_period  TEXT,           -- periode de reference ('0y', '+1q'...)
    pt_mean         REAL,           -- objectif de cours moyen
    pt_high         REAL,
    pt_low          REAL,
    pt_current      REAL,           -- cours courant (reference de l'objectif)
    source          TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    ticker  TEXT PRIMARY KEY,
    asof    TEXT NOT NULL,
    type    TEXT,                   -- 'action' | 'ETF'
    payload TEXT,                   -- JSON des fondamentaux d'affichage
    source  TEXT
);

CREATE TABLE IF NOT EXISTS news (
    id        TEXT PRIMARY KEY,     -- hash stable (ticker+url/headline)
    ticker    TEXT NOT NULL,
    datetime  TEXT,                 -- ISO
    headline  TEXT,
    summary   TEXT,
    url       TEXT,
    source    TEXT
);

CREATE TABLE IF NOT EXISTS news_analysis (
    ticker     TEXT PRIMARY KEY,    -- analyse agregee par ticker (derniere passe)
    asof       TEXT NOT NULL,
    payload    TEXT,                -- JSON : [{headline, categorie, sentiment, resume}]
    model      TEXT
);

CREATE TABLE IF NOT EXISTS briefing_snapshot (
    id      INTEGER PRIMARY KEY,    -- toujours 1 : on ne garde que la derniere reference
    asof    TEXT,
    payload TEXT                    -- JSON de l'etat de reference (prix, flags, news, revisions)
);

CREATE TABLE IF NOT EXISTS update_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asof    TEXT,
    kind    TEXT,                 -- 'donnees' | 'news' | 'global'
    status  TEXT,
    detail  TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        # Migration : l'ancien update_log (asof PK, sans 'kind') doit etre recree.
        # Le journal n'est qu'un cache d'affichage : le supprimer est sans risque.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(update_log)").fetchall()]
        if cols and "kind" not in cols:
            conn.execute("DROP TABLE update_log")
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------
# Ecritures (appelees uniquement par update.py)
# --------------------------------------------------------------------------
def upsert_prices(ticker: str, rows: list[tuple[str, float]]) -> None:
    """rows = [(date 'YYYY-MM-DD', close), ...]"""
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close",
            [(ticker, d, c) for d, c in rows],
        )


def upsert_quote(q: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO quotes
               (ticker, asof, last_price, prev_close, change_pct, high_52w, low_52w, drawdown_pct, source)
               VALUES (:ticker, :asof, :last_price, :prev_close, :change_pct, :high_52w, :low_52w, :drawdown_pct, :source)
               ON CONFLICT(ticker) DO UPDATE SET
                 asof=excluded.asof, last_price=excluded.last_price, prev_close=excluded.prev_close,
                 change_pct=excluded.change_pct, high_52w=excluded.high_52w, low_52w=excluded.low_52w,
                 drawdown_pct=excluded.drawdown_pct, source=excluded.source""",
            q,
        )


def upsert_fundamentals(f: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO fundamentals
               (ticker, asof, revenue_ttm, revenue_prev, revenue_growth, net_margin, debt_to_equity, source)
               VALUES (:ticker, :asof, :revenue_ttm, :revenue_prev, :revenue_growth, :net_margin, :debt_to_equity, :source)
               ON CONFLICT(ticker) DO UPDATE SET
                 asof=excluded.asof, revenue_ttm=excluded.revenue_ttm, revenue_prev=excluded.revenue_prev,
                 revenue_growth=excluded.revenue_growth, net_margin=excluded.net_margin,
                 debt_to_equity=excluded.debt_to_equity, source=excluded.source""",
            f,
        )


def upsert_events_estimates(e: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO events_estimates
               (ticker, asof, earnings_date, exdiv_date, eps_rev_up_30, eps_rev_down_30,
                eps_rev_up_7, eps_rev_down_7, eps_rev_period, pt_mean, pt_high, pt_low,
                pt_current, source)
               VALUES (:ticker, :asof, :earnings_date, :exdiv_date, :eps_rev_up_30, :eps_rev_down_30,
                :eps_rev_up_7, :eps_rev_down_7, :eps_rev_period, :pt_mean, :pt_high, :pt_low,
                :pt_current, :source)
               ON CONFLICT(ticker) DO UPDATE SET
                 asof=excluded.asof, earnings_date=excluded.earnings_date, exdiv_date=excluded.exdiv_date,
                 eps_rev_up_30=excluded.eps_rev_up_30, eps_rev_down_30=excluded.eps_rev_down_30,
                 eps_rev_up_7=excluded.eps_rev_up_7, eps_rev_down_7=excluded.eps_rev_down_7,
                 eps_rev_period=excluded.eps_rev_period, pt_mean=excluded.pt_mean, pt_high=excluded.pt_high,
                 pt_low=excluded.pt_low, pt_current=excluded.pt_current, source=excluded.source""",
            e,
        )


def get_events_estimates(ticker: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM events_estimates WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None


def upsert_profile(ticker: str, asof: str, type_: str, payload_json: str, source: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO profile (ticker, asof, type, payload, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 asof=excluded.asof, type=excluded.type, payload=excluded.payload,
                 source=excluded.source""",
            (ticker, asof, type_, payload_json, source),
        )


def get_profile(ticker: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM profile WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None


def replace_news(ticker: str, items: list[dict]) -> None:
    """Remplace les news d'un ticker par le lot recupere."""
    with get_conn() as conn:
        conn.execute("DELETE FROM news WHERE ticker = ?", (ticker,))
        conn.executemany(
            """INSERT OR REPLACE INTO news (id, ticker, datetime, headline, summary, url, source)
               VALUES (:id, :ticker, :datetime, :headline, :summary, :url, :source)""",
            items,
        )


def upsert_news_analysis(ticker: str, asof: str, payload_json: str, model: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO news_analysis (ticker, asof, payload, model)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET asof=excluded.asof, payload=excluded.payload, model=excluded.model""",
            (ticker, asof, payload_json, model),
        )


def save_briefing_snapshot(asof: str, payload_json: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO briefing_snapshot (id, asof, payload) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET asof=excluded.asof, payload=excluded.payload",
            (asof, payload_json),
        )


def get_briefing_snapshot() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM briefing_snapshot WHERE id = 1").fetchone()
        return dict(row) if row else None


def log_update(asof: str, kind: str, status: str, detail: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO update_log (asof, kind, status, detail) VALUES (?, ?, ?, ?)",
            (asof, kind, status, detail),
        )


# --------------------------------------------------------------------------
# Lectures (UI / regles)
# --------------------------------------------------------------------------
def get_quote(ticker: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM quotes WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None


def get_fundamentals(ticker: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM fundamentals WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None


def get_price_history(ticker: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date", (ticker,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_news(ticker: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news WHERE ticker = ? ORDER BY datetime DESC", (ticker,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_news_analysis(ticker: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM news_analysis WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None


def last_update(kind: str | None = None) -> dict | None:
    """Derniere mise a jour, globalement ou pour un type donne ('donnees'|'news')."""
    with get_conn() as conn:
        if kind:
            row = conn.execute(
                "SELECT * FROM update_log WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM update_log ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
