"""Les 3 regles de la watchlist - DETERMINISTE, sans LLM, basees sur des seuils.

Chaque flag indique noir sur blanc la valeur observee ET le seuil : tout est
verifiable, rien n'est de l'intuition. Les regles signalent ce qui merite
l'attention ; elles ne disent JAMAIS acheter/vendre.

1. Flag de chute brutale (signal d'attention).
2. Signaux techniques notables (RSI extreme, proche du plus-bas 52s).
3. Alarme de degradation (ACTIONS uniquement) : CA / marge / dette.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import db
from .config import AppConfig
from .events import construire_evenements
from .signals import Snapshot


@dataclass
class Flag:
    ticker: str
    regle: str          # "chute" | "technique" | "degradation"
    severite: str       # "info" | "alerte"
    message: str        # explication chiffree


# ==========================================================================
# REGLE 1 - Flag de chute brutale
# ==========================================================================
def flags_chute(config: AppConfig, snaps: list[Snapshot]) -> list[Flag]:
    cfg = config.flag_chute
    seuil_seance = float(cfg.get("seuil_seance_pct", -7))
    seuil_dd = float(cfg.get("seuil_drawdown_52s_pct", -20))
    flags: list[Flag] = []
    for s in snaps:
        t = s.instrument.ticker
        if s.change_pct is not None and s.change_pct <= seuil_seance:
            flags.append(Flag(t, "chute", "alerte",
                              f"{t} : {s.change_pct:+.1f}% sur la seance (seuil {seuil_seance:+.0f}%)."))
        if s.drawdown_pct is not None and s.drawdown_pct <= seuil_dd:
            flags.append(Flag(t, "chute", "alerte",
                              f"{t} : {s.drawdown_pct:+.1f}% depuis le plus-haut 52 semaines "
                              f"(seuil {seuil_dd:+.0f}%)."))
    return flags


# ==========================================================================
# REGLE 2 - Signaux techniques notables
# ==========================================================================
def flags_technique(config: AppConfig, snaps: list[Snapshot]) -> list[Flag]:
    cfg = config.signaux_techniques
    if not cfg.get("flags_actifs", True):
        return []
    survente = float(cfg.get("rsi_survente", 30))
    surachat = float(cfg.get("rsi_surachat", 70))
    proche_bas = float(cfg.get("seuil_proche_bas_52s_pct", 10))
    flags: list[Flag] = []
    for s in snaps:
        t = s.instrument.ticker
        if s.rsi_etat == "survendu" and s.rsi_14 is not None:
            flags.append(Flag(t, "technique", "info",
                              f"{t} : RSI 14 = {s.rsi_14:.0f} (< {survente:.0f}) — potentiellement survendu."))
        elif s.rsi_etat == "suracheté" and s.rsi_14 is not None:
            flags.append(Flag(t, "technique", "info",
                              f"{t} : RSI 14 = {s.rsi_14:.0f} (> {surachat:.0f}) — potentiellement suracheté."))
        if s.position_52w_pct is not None and s.position_52w_pct <= proche_bas:
            flags.append(Flag(t, "technique", "info",
                              f"{t} : proche du plus-bas 52s (position {s.position_52w_pct:.0f}% du range)."))
    return flags


# ==========================================================================
# REGLE 3 - Alarme de degradation (ACTIONS UNIQUEMENT)
# ==========================================================================
def flags_degradation(config: AppConfig) -> list[Flag]:
    cfg = config.degradation_actions
    flags: list[Flag] = []
    for inst in config.actions():
        f = db.get_fundamentals(inst.ticker)
        t = inst.ticker
        if not f:
            flags.append(Flag(t, "degradation", "info",
                              f"{t} : fondamentaux indisponibles (n/d) — alarme non evaluable."))
            continue

        rule = cfg.get("croissance_ca_min_pct", {}) or {}
        if rule.get("actif"):
            seuil = float(rule.get("seuil", 0))
            val = f.get("revenue_growth")
            if val is None:
                flags.append(Flag(t, "degradation", "info", f"{t} : croissance du CA n/d."))
            elif val < seuil:
                flags.append(Flag(t, "degradation", "alerte",
                                  f"{t} : croissance CA {val:+.1f}% < seuil {seuil:.0f}% — these a surveiller."))

        rule = cfg.get("marge_nette_min_pct", {}) or {}
        if rule.get("actif"):
            seuil = float(rule.get("seuil", 0))
            val = f.get("net_margin")
            if val is None:
                flags.append(Flag(t, "degradation", "info", f"{t} : marge nette n/d."))
            elif val < seuil:
                flags.append(Flag(t, "degradation", "alerte",
                                  f"{t} : marge nette {val:.1f}% < seuil {seuil:.0f}%."))

        rule = cfg.get("dette_sur_capitaux_max", {}) or {}
        if rule.get("actif"):
            seuil = float(rule.get("seuil", 0))
            val = f.get("debt_to_equity")
            if val is None:
                flags.append(Flag(t, "degradation", "info", f"{t} : ratio dette/capitaux n/d."))
            elif val > seuil:
                flags.append(Flag(t, "degradation", "alerte",
                                  f"{t} : dette/capitaux {val:.2f} > seuil {seuil:.2f}."))
    return flags


# ==========================================================================
# REGLE 4 - Evenements a venir (resultats) -- ACTIONS
# ==========================================================================
def flags_evenements(config: AppConfig) -> list[Flag]:
    cfg = config.evenements
    seuil_res = cfg.get("resultats_dans_jours", None)
    flags: list[Flag] = []
    if seuil_res is None:
        return flags
    seuil_res = int(seuil_res)
    for v in construire_evenements(config):
        t = v.instrument.ticker
        j = v.jours_avant_resultats
        if j is not None and 0 <= j <= seuil_res:
            quand = "aujourd'hui" if j == 0 else (f"demain" if j == 1 else f"dans {j} jours")
            flags.append(Flag(t, "evenement", "info",
                              f"{t} : resultats {quand} ({v.earnings_date}). "
                              f"Prudence avant un versement DCA."))
    return flags


# ==========================================================================
# REGLE 5 - Revisions d'estimations (signal avance) -- ACTIONS
# ==========================================================================
def flags_revisions(config: AppConfig) -> list[Flag]:
    cfg = config.revisions_estimations
    if not cfg.get("actif", True):
        return []
    seuil = float(cfg.get("seuil_net_revisions_30j", 0))
    flags: list[Flag] = []
    for v in construire_evenements(config):
        if v.rev_net_30 is None:
            continue
        t = v.instrument.ticker
        if v.rev_net_30 < seuil:
            flags.append(Flag(t, "revision", "alerte",
                              f"{t} : revisions d'EPS nettes {v.rev_net_30:+.0f} sur 30j "
                              f"({v.rev_up_30:.0f} hausses / {v.rev_down_30:.0f} baisses, "
                              f"periode {v.rev_period}) — attentes en degradation."))
    return flags


def tous_les_flags(config: AppConfig, snaps: list[Snapshot]) -> list[Flag]:
    return (flags_chute(config, snaps)
            + flags_technique(config, snaps)
            + flags_degradation(config)
            + flags_evenements(config)
            + flags_revisions(config))
