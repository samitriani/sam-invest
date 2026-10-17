"""Test de l'etat de rafraichissement (suivi cross-session + verrou d'exclusion).

Lancement : python -m tests.test_etat_refresh

Contexte : un rafraichissement mobile dure 3 a 4 min et la connexion du
telephone se coupe en route. Pour que le travail continue cote serveur et qu'une
session rouverte sache ou il en est, l'etat du cycle vit en base. Deux pieges :
  - un 2e lancement pendant un cycle vivant doit etre refuse (sinon deux
    ecrivains SQLite concurrents) ;
  - un cycle 'en_cours' dont le process est mort (veille / redeploiement) ne
    doit pas bloquer le verrou a jamais : il est requalifie 'interrompu'.

Verifie que :
  - les trois statuts s'ecrivent et se relisent ;
  - le verrou refuse un 2e lancement tant que le cycle est 'en_cours' ;
  - un cycle clos (termine ou interrompu) libere le verrou ;
  - chaque kind initialise ses trois etapes (donnees, news, synthese), celles
    hors cycle etant 'hors' ;
  - un cycle sans progression depuis > DELAI_REFRESH_PERIME_S est requalifie
    'interrompu' a la lecture, et libere le verrou.

Ce test utilise une base SQLite TEMPORAIRE et ne touche jamais data/sam_invest.db.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sam_invest import db


def _run() -> None:
    # ignore_cleanup_errors : sous Windows, db.py ne ferme pas ses connexions
    # explicitement et le fichier reste verrouille a la suppression du dossier.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db.DB_PATH = Path(tmp) / "test_sam_invest.db"
        db.init_db()

        t0 = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc)

        # 0. Base vierge : aucun cycle.
        assert db.etat_refresh(t0) is None

        # 1. Demarrage : cycle 'en_cours' lisible.
        assert db.demarrer_refresh("global", t0) is True
        e = db.etat_refresh(t0)
        assert e["statut"] == "en_cours" and e["kind"] == "global"
        assert e["debut"] == "2026-08-09T10:00:00+00:00"
        # Etapes initiales : celles du kind en attente, les autres hors cycle.
        assert {k: v["etat"] for k, v in e["etapes"].items()} ==             {"donnees": "attente", "news": "attente", "synthese": "hors"}

        # 2. L'etat d'une etape avance, les autres ne bougent pas.
        db.maj_etape_refresh("donnees", "ok", 20, 20, maintenant=t0 + timedelta(seconds=20))
        db.maj_etape_refresh("news", "en_cours", 12, 20, phase="fetch",
                             maintenant=t0 + timedelta(seconds=30))
        e = db.etat_refresh(t0 + timedelta(seconds=30))
        assert e["statut"] == "en_cours"
        assert e["etapes"]["news"] == {"etat": "en_cours", "fait": 12, "total": 20, "phase": "fetch"}
        assert e["etapes"]["donnees"] == {"etat": "ok", "fait": 20, "total": 20}
        assert e["etapes"]["synthese"] == {"etat": "hors"}
        # Une nouvelle ecriture remplace l'entree de l'etape en entier (la phase disparait).
        db.maj_etape_refresh("news", "en_cours", 3, 20, phase="classement",
                             maintenant=t0 + timedelta(seconds=35))
        assert db.etat_refresh(t0 + timedelta(seconds=35))["etapes"]["news"]["phase"] == "classement"
        db.maj_etape_refresh("news", "en_cours", 12, 20, phase="fetch",
                             maintenant=t0 + timedelta(seconds=36))

        # 3. Verrou : un 2e lancement pendant un cycle vivant est refuse et
        #    n'ecrase rien.
        assert db.demarrer_refresh("news", t0 + timedelta(seconds=40)) is False
        e = db.etat_refresh(t0 + timedelta(seconds=40))
        assert e["kind"] == "global" and e["etapes"]["news"]["fait"] == 12

        # 4. Cycle clos 'termine' : le verrou est libere, resume conserve.
        db.terminer_refresh("termine", "Prix OK 20/20", t0 + timedelta(minutes=3))
        e = db.etat_refresh(t0 + timedelta(minutes=3))
        assert e["statut"] == "termine" and e["resume"] == "Prix OK 20/20"
        # ... et une ecriture d'etape tardive n'a pas le droit de le rouvrir.
        db.maj_etape_refresh("news", "en_cours", 1, 1, maintenant=t0 + timedelta(minutes=3, seconds=5))
        assert db.etat_refresh(t0 + timedelta(minutes=4))["statut"] == "termine"

        # 5. Nouveau cycle apres cloture, puis cloture 'interrompu' : verrou libre.
        assert db.demarrer_refresh("news", t0 + timedelta(minutes=5)) is True
        assert db.etat_refresh(t0 + timedelta(minutes=5))["kind"] == "news"
        db.terminer_refresh("interrompu", "coupe a 7/20", t0 + timedelta(minutes=6))
        assert db.etat_refresh(t0 + timedelta(minutes=6))["statut"] == "interrompu"
        assert db.demarrer_refresh("donnees", t0 + timedelta(minutes=7)) is True

        # 5bis. Chaque kind fixe ses etapes actives.
        db.terminer_refresh("termine", "", t0 + timedelta(minutes=7, seconds=30))
        attendu = {"donnees": ("attente", "hors", "hors"), "news": ("hors", "attente", "hors"),
                   "global": ("attente", "attente", "hors"),
                   "global_analyse": ("attente", "attente", "attente")}
        tt = t0 + timedelta(minutes=7, seconds=40)
        for kind, etats in attendu.items():
            assert db.demarrer_refresh(kind, tt) is True
            lus = tuple(db.etat_refresh(tt)["etapes"][e]["etat"] for e in db.ETAPES)
            assert lus == etats, (kind, lus)
            db.terminer_refresh("termine", "", tt)
            tt += timedelta(seconds=5)
        assert db.demarrer_refresh("donnees", t0 + timedelta(minutes=7)) is True

        # 6. Cycle fantome : 'en_cours' sans progression depuis > delai.
        t1 = t0 + timedelta(minutes=7)
        db.maj_etape_refresh("donnees", "en_cours", 3, 20, maintenant=t1)
        # Juste sous le delai : toujours vivant.
        vivant = t1 + timedelta(seconds=db.DELAI_REFRESH_PERIME_S - 1)
        assert db.etat_refresh(vivant)["statut"] == "en_cours"
        # Au-dela : requalifie 'interrompu' a la lecture, etat des etapes garde.
        mort = t1 + timedelta(seconds=db.DELAI_REFRESH_PERIME_S + 1)
        e = db.etat_refresh(mort)
        assert e["statut"] == "interrompu" and e["etapes"]["donnees"]["fait"] == 3

        # 7. Le verrou se libere aussi directement au lancement, sans lecture
        #    prealable : un cycle 'en_cours' fantome ne bloque pas un nouveau.
        assert db.demarrer_refresh("global", t1 + timedelta(minutes=15)) is True
        db.maj_etape_refresh("donnees", "en_cours", 1, 20, maintenant=t1 + timedelta(minutes=15))
        assert db.demarrer_refresh("news", t1 + timedelta(minutes=16)) is False
        assert db.demarrer_refresh("news", t1 + timedelta(minutes=30)) is True, \
            "un cycle fantome ne doit pas bloquer le verrou"

    print("OK - test_etat_refresh : tous les cas passent.")


if __name__ == "__main__":
    _run()
