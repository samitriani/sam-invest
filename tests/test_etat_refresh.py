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

        # 2. La progression avance.
        db.maj_progression_refresh("News 12/20", t0 + timedelta(seconds=30))
        e = db.etat_refresh(t0 + timedelta(seconds=30))
        assert e["progression"] == "News 12/20" and e["statut"] == "en_cours"

        # 3. Verrou : un 2e lancement pendant un cycle vivant est refuse et
        #    n'ecrase rien.
        assert db.demarrer_refresh("news", t0 + timedelta(seconds=40)) is False
        e = db.etat_refresh(t0 + timedelta(seconds=40))
        assert e["kind"] == "global" and e["progression"] == "News 12/20"

        # 4. Cycle clos 'termine' : le verrou est libere, resume conserve.
        db.terminer_refresh("termine", "Prix OK 20/20", t0 + timedelta(minutes=3))
        e = db.etat_refresh(t0 + timedelta(minutes=3))
        assert e["statut"] == "termine" and e["resume"] == "Prix OK 20/20"
        # ... et une progression tardive n'a pas le droit de le rouvrir.
        db.maj_progression_refresh("fantome", t0 + timedelta(minutes=3, seconds=5))
        assert db.etat_refresh(t0 + timedelta(minutes=4))["statut"] == "termine"

        # 5. Nouveau cycle apres cloture, puis cloture 'interrompu' : verrou libre.
        assert db.demarrer_refresh("news", t0 + timedelta(minutes=5)) is True
        assert db.etat_refresh(t0 + timedelta(minutes=5))["kind"] == "news"
        db.terminer_refresh("interrompu", "coupe a 7/20", t0 + timedelta(minutes=6))
        assert db.etat_refresh(t0 + timedelta(minutes=6))["statut"] == "interrompu"
        assert db.demarrer_refresh("donnees", t0 + timedelta(minutes=7)) is True

        # 6. Cycle fantome : 'en_cours' sans progression depuis > delai.
        t1 = t0 + timedelta(minutes=7)
        db.maj_progression_refresh("Donnees 3/20", t1)
        # Juste sous le delai : toujours vivant.
        vivant = t1 + timedelta(seconds=db.DELAI_REFRESH_PERIME_S - 1)
        assert db.etat_refresh(vivant)["statut"] == "en_cours"
        # Au-dela : requalifie 'interrompu' a la lecture, progression gardee.
        mort = t1 + timedelta(seconds=db.DELAI_REFRESH_PERIME_S + 1)
        e = db.etat_refresh(mort)
        assert e["statut"] == "interrompu" and e["progression"] == "Donnees 3/20"

        # 7. Le verrou se libere aussi directement au lancement, sans lecture
        #    prealable : un cycle 'en_cours' fantome ne bloque pas un nouveau.
        assert db.demarrer_refresh("global", t1 + timedelta(minutes=15)) is True
        db.maj_progression_refresh("Donnees 1/20", t1 + timedelta(minutes=15))
        assert db.demarrer_refresh("news", t1 + timedelta(minutes=16)) is False
        assert db.demarrer_refresh("news", t1 + timedelta(minutes=30)) is True, \
            "un cycle fantome ne doit pas bloquer le verrou"

    print("OK - test_etat_refresh : tous les cas passent.")


if __name__ == "__main__":
    _run()
