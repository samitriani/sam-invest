# Tasks

## 1. Suivi d'etat du rafraichissement (db.py)

- [ ] 1.1 Ajouter le suivi d'etat d'un cycle de rafraichissement (statut
  `en_cours`/`interrompu`/`termine`, type, horodatage de debut, progression
  textuelle) via `ALTER TABLE` explicite dans `init_db()` (pas seulement
  `CREATE TABLE IF NOT EXISTS`, cf. pitfall #2 de CLAUDE.md), avec des
  fonctions `db.demarrer_refresh` / `db.maj_progression_refresh` /
  `db.terminer_refresh` / `db.etat_refresh`. Verifier par
  `python -m tests.test_etat_refresh` (DB temporaire) : ecrire puis relire
  chacun des trois statuts.
- [ ] 1.2 Dans `db.etat_refresh`, requalifier automatiquement un etat
  `en_cours` en `interrompu` si aucune progression n'a ete ecrite depuis un
  delai bien superieur a la duree normale (ex. 10 min pour 3-4 min attendues).
  Verifier dans le meme test : forcer un horodatage ancien et controler la
  requalification a la lecture.
- [ ] 1.3 Ajouter le verrou d'exclusion : refuser un nouveau
  `db.demarrer_refresh` tant qu'un etat `en_cours` existe (apres
  requalification eventuelle par 1.2). Verifier dans le meme test : simuler
  un demarrage alors qu'un etat `en_cours` existe deja et controler le refus.

## 2. Persistance incrementale des news (sam_invest/update.py)

- [ ] 2.1 Dans `update_news`, deplacer les ecritures `db.replace_news` et
  `db.upsert_news_analysis` a l'interieur des boucles `as_completed` (fetch
  puis classement), ticker par ticker, au lieu d'apres la boucle complete sur
  toute la watchlist — au meme grain que les prix (`_maj_donnees_instrument`).
  Verifier par `python -m tests.test_refresh_resilient` (voir 4.1) : une
  interruption simulee apres N tickers laisse N lignes en base.
- [ ] 2.2 A chaque instrument traite (donnees et news), appeler
  `db.maj_progression_refresh` (1.1) avec un libelle court (ex. "News
  12/20"). Verifier dans le meme test que la progression lue avance au fil
  du traitement.

## 3. Execution en thread serveur detache (sam_invest/update.py, app.py)

- [ ] 3.1 Dans `update.py`, ajouter `lancer_refresh_arriere_plan(config,
  kind)` qui demarre un `threading.Thread` (non rattache a la session
  Streamlit) executant `update_donnees`/`update_news`/`update_global`, en
  respectant le verrou (1.3) et en ecrivant l'etat (1.1/2.2) a chaque etape.
  Verifier par un test qui lance le thread, attend sa fin (`join`) et
  controle que l'etat final est `termine` et que les donnees sont ecrites.
- [ ] 3.2 Dans `app.py`, remplacer l'appel bloquant actuel (`run_update` et
  les boutons du menu ☰ et des pages) par : declenchement de
  `lancer_refresh_arriere_plan` sans attendre sa fin, puis lecture de
  `db.etat_refresh` a chaque rendu de page pour afficher l'etat courant.
  Verifier manuellement (`streamlit run app.py`) : declencher un
  rafraichissement, recharger la page pendant son execution, constater
  l'affichage "en cours".
- [ ] 3.3 Adapter le texte existant (captions) pour les trois etats
  `en_cours`/`interrompu`/`termine`, sans ajouter de bouton ni reorganiser le
  menu (volet UI en standby — voir proposal.md). Verifier visuellement que
  les boutons et le menu restent inchanges hors ce texte.

## 4. Verification de bout en bout et non-regression

- [ ] 4.1 Ecrire `tests/test_refresh_resilient.py` (DB temporaire comme
  `test_masquage_alertes`, sans reseau : mocks de `data_sources` comme les
  tests existants) simulant une interruption pendant la phase news et
  verifiant (a) les tickers deja traites restent en base, (b) l'etat passe a
  `interrompu`, (c) un nouveau lancement est accepte une fois `interrompu`.
  Verifier par `python -m tests.test_refresh_resilient`.
- [ ] 4.2 Executer les quatre scripts de non-regression existants
  (`test_debt_to_equity`, `test_classement_bourses`, `test_societe_financiere`,
  `test_masquage_alertes`) et confirmer qu'ils passent toujours sans
  modification.
- [ ] 4.3 Test manuel mobile : declencher « Mettre a jour donnees et analyse »
  sur telephone, passer sur une autre appli pendant 2-3 minutes, revenir, et
  verifier que l'app affiche l'etat correct (en cours ou termine) sans avoir
  tout perdu.

## Workflow follow-up

- Archiver le changement une fois les taches ci-dessus completees et
  validees par l'utilisateur.
- Verifier que le resultat archive (`openspec show rafraichissement-watchlist
  --type spec`) reflete bien les nouvelles exigences.
