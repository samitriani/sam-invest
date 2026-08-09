"""Test de non-regression du masquage des alertes (croix du menu ☰).

Lancement : python -m tests.test_masquage_alertes

Contexte : un flag n'est pas une ligne stockee. rules.tous_les_flags le
RECALCULE a chaque affichage a partir des cours, donc « supprimer » une alerte
ne peut pas l'effacer — seulement la masquer. La regle retenue est un REPORT :
le masquage vaut pour l'etat des donnees au moment du clic et expire a la
prochaine actualisation des cours. Sans ce test, il suffirait d'oublier de
comparer `donnees_asof` pour transformer ce report en suppression definitive,
et une degradation reelle passerait alors inapercue.

Verifie que :
  - une cle masquee reste masquee tant que la mise a jour des donnees ne bouge pas ;
  - elle reapparait des que `donnees_asof` change (nouvelle actualisation) ;
  - les masquages perimes sont purges de la table a la lecture (pas d'accumulation) ;
  - les cles jamais masquees ne sont pas affectees.

Ce test utilise une base SQLite TEMPORAIRE et ne touche jamais data/sam_invest.db.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sam_invest import db


def _run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # Isolation : on detourne le chemin de la base AVANT tout appel.
        db.DB_PATH = Path(tmp) / "test_sam_invest.db"
        db.init_db()

        asof1 = "2026-08-09T10:00:00+00:00"
        asof2 = "2026-08-09T14:00:00+00:00"   # actualisation suivante
        cle_a = "NVDA|chute|alerte"
        cle_b = "BABA|degradation|alerte"

        # 0. Base vierge : rien de masque.
        assert db.cles_masquees(asof1) == set()

        # 1. Masquage : la cle disparait tant que l'actualisation ne bouge pas.
        db.masquer_flags([cle_a], asof1, "2026-08-09T10:05:00+00:00")
        assert db.cles_masquees(asof1) == {cle_a}
        assert db.cles_masquees(asof1) == {cle_a}, "la lecture ne doit pas etre destructive"

        # 2. Nouvelle actualisation -> le masquage expire, l'alerte revient.
        assert db.cles_masquees(asof2) == set()

        # 3. ... et le masquage perime a bien ete purge : revenir a asof1 (cas
        #    theorique) ne doit pas ressusciter le masquage.
        assert db.cles_masquees(asof1) == set()

        # 4. « Tout supprimer » : plusieurs cles d'un coup, meme actualisation.
        db.masquer_flags([cle_a, cle_b], asof2, "2026-08-09T14:05:00+00:00")
        assert db.cles_masquees(asof2) == {cle_a, cle_b}

        # 5. Une cle jamais masquee n'est pas affectee.
        assert "MSFT|technique|info" not in db.cles_masquees(asof2)

        # 6. Re-masquer une cle deja masquee est idempotent (PK sur `cle`).
        db.masquer_flags([cle_a], asof2, "2026-08-09T14:10:00+00:00")
        assert db.cles_masquees(asof2) == {cle_a, cle_b}

        # 7. Liste vide : aucune ecriture, aucun effet de bord.
        db.masquer_flags([], asof2, "2026-08-09T14:15:00+00:00")
        assert db.cles_masquees(asof2) == {cle_a, cle_b}

        # 8. Aucune mise a jour des donnees encore faite (asof None) : le
        #    masquage doit rester coherent et ne pas planter.
        db.masquer_flags([cle_a], None, "2026-08-09T15:00:00+00:00")
        assert db.cles_masquees(None) == {cle_a}

    print("OK - test_masquage_alertes : tous les cas passent.")


if __name__ == "__main__":
    _run()
