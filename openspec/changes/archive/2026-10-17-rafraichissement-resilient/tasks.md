# Tasks

## 1. Suivi d'etat du rafraichissement (db.py)

- [x] 1.1 Ajouter le suivi d'etat d'un cycle de rafraichissement (statut
  `en_cours`/`interrompu`/`termine`, type, horodatage de debut, progression
  textuelle) via `ALTER TABLE` explicite dans `init_db()` (pas seulement
  `CREATE TABLE IF NOT EXISTS`, cf. pitfall #2 de CLAUDE.md), avec des
  fonctions `db.demarrer_refresh` / `db.maj_progression_refresh` /
  `db.terminer_refresh` / `db.etat_refresh`. Verifier par
  `python -m tests.test_etat_refresh` (DB temporaire) : ecrire puis relire
  chacun des trois statuts.
- [x] 1.2 Dans `db.etat_refresh`, requalifier automatiquement un etat
  `en_cours` en `interrompu` si aucune progression n'a ete ecrite depuis un
  delai bien superieur a la duree normale (ex. 10 min pour 3-4 min attendues).
  Verifier dans le meme test : forcer un horodatage ancien et controler la
  requalification a la lecture.
- [x] 1.3 Ajouter le verrou d'exclusion : refuser un nouveau
  `db.demarrer_refresh` tant qu'un etat `en_cours` existe (apres
  requalification eventuelle par 1.2). Verifier dans le meme test : simuler
  un demarrage alors qu'un etat `en_cours` existe deja et controler le refus.

## 2. Persistance incrementale des news (sam_invest/update.py)

- [x] 2.1 Dans `update_news`, deplacer les ecritures `db.replace_news` et
  `db.upsert_news_analysis` a l'interieur des boucles `as_completed` (fetch
  puis classement), ticker par ticker, au lieu d'apres la boucle complete sur
  toute la watchlist — au meme grain que les prix (`_maj_donnees_instrument`).
  Verifier par `python -m tests.test_refresh_resilient` (voir 4.1) : une
  interruption simulee apres N tickers laisse N lignes en base.
- [x] 2.2 A chaque instrument traite (donnees et news), appeler
  `db.maj_progression_refresh` (1.1) avec un libelle court (ex. "News
  12/20"). Verifier dans le meme test que la progression lue avance au fil
  du traitement.

## 3. Execution en thread serveur detache (sam_invest/update.py, app.py)

- [x] 3.1 Dans `update.py`, ajouter `lancer_refresh_arriere_plan(config,
  kind)` qui demarre un `threading.Thread` (non rattache a la session
  Streamlit) executant `update_donnees`/`update_news`/`update_global`, en
  respectant le verrou (1.3) et en ecrivant l'etat (1.1/2.2) a chaque etape.
  Verifier par un test qui lance le thread, attend sa fin (`join`) et
  controle que l'etat final est `termine` et que les donnees sont ecrites.
- [x] 3.2 Extraire la partie non-UI de `app.ecrire_synthese` (cache inchange,
  `construire_briefing`, appel Sonnet, `save_briefing_cache`) en
  `update.generer_synthese(config, progress)` sans `st`, faire de
  `ecrire_synthese` un enrobage UI de cette fonction (comportement identique),
  et ajouter le kind `global_analyse` a `lancer_refresh_arriere_plan` (donnees
  + news puis synthese, le resume du cycle signalant une synthese manquante).
  Verifier dans `tests/test_refresh_resilient.py` : cycle `global_analyse` avec
  Sonnet simule -> briefing en base et etat `termine` ; Sonnet qui echoue ->
  donnees gardees, etat `termine` avec synthese signalee manquante.
- [x] 3.3 Dans `app.py`, remplacer l'appel bloquant actuel (`run_update` et
  les boutons du menu ☰ et des pages) par : declenchement de
  `lancer_refresh_arriere_plan` sans attendre sa fin, puis lecture de
  `db.etat_refresh` a chaque rendu de page pour afficher l'etat courant.
  Verifier manuellement (`streamlit run app.py`) : declencher un
  rafraichissement, recharger la page pendant son execution, constater
  l'affichage "en cours".
- [x] 3.4 Adapter le texte existant (captions) pour les trois etats
  `en_cours`/`interrompu`/`termine`, sans ajouter de bouton ni reorganiser le
  menu (volet UI en standby — voir proposal.md). Verifier visuellement que
  les boutons et le menu restent inchanges hors ce texte.

## 4. Verification de bout en bout et non-regression

- [x] 4.1 Ecrire `tests/test_refresh_resilient.py` (DB temporaire comme
  `test_masquage_alertes`, sans reseau : mocks de `data_sources` comme les
  tests existants) simulant une interruption pendant la phase news et
  verifiant (a) les tickers deja traites restent en base, (b) l'etat passe a
  `interrompu`, (c) un nouveau lancement est accepte une fois `interrompu`.
  Verifier par `python -m tests.test_refresh_resilient`.
- [x] 4.2 Executer les quatre scripts de non-regression existants
  (`test_debt_to_equity`, `test_classement_bourses`, `test_societe_financiere`,
  `test_masquage_alertes`) et confirmer qu'ils passent toujours sans
  modification.
- [x] 4.3 Test manuel mobile : declencher « Mettre a jour donnees et analyse »
  sur telephone, passer sur une autre appli pendant 2-3 minutes, revenir, et
  verifier que l'app affiche l'etat correct (en cours ou termine) sans avoir
  tout perdu.

## 5. Etat par etape et bandeau en trois lignes

- [x] 5.1 Stocker l'etat par etape dans `refresh_etat` (colonne `etapes` JSON,
  `ALTER TABLE` explicite dans `init_db()`), initialise par `demarrer_refresh`
  selon le kind (etapes hors cycle = `hors`), avec `db.maj_etape_refresh` qui
  remplace `maj_progression_refresh` (meme role de heartbeat pour la
  requalification) et `etat_refresh` qui renvoie `etapes` en dict. Verifier dans
  `tests/test_etat_refresh.py` : etapes initiales par kind, mise a jour d'une
  etape, requalification toujours effective.
- [x] 5.2 Dans `update.py`, renseigner les etapes : donnees (compteur par
  instrument puis cours obtenus), news (fetch puis classement, compteur
  d'instruments sans erreur), synthese (en cours puis ok/echec). Verifier dans
  `tests/test_refresh_resilient.py` : compteurs pendant la phase, etat final
  `ok`/`partiel`/`echec`, kind `donnees` laissant news et synthese `hors`.
- [x] 5.3 Dans `app.py`, rendre le bandeau en trois lignes fixes (Donnees, News,
  Synthese) avec les etats de D6, sans texte explicatif, et le meme rendu pour
  en cours / termine / interrompu. Verifier dans le navigateur (desktop et
  375 px) : les trois etats du bandeau et un cycle `donnees` seul.

## Notes de cloture

- 4.2 : les trois scripts purs passent ; `test_masquage_alertes` passe toutes ses
  verifications mais plante au nettoyage de son dossier temporaire sous Windows
  (comportement anterieur a ce changement, non corrige ici).
- 4.3 : valide par l'utilisateur sur l'app deployee apres redemarrage (bandeau et
  cycle OK) ; le scenario « changer d'appli pendant le cycle » n'a pas ete
  confirme explicitement.

## Workflow follow-up

- Archiver le changement une fois les taches ci-dessus completees et
  validees par l'utilisateur.
- Verifier que le resultat archive (`openspec show rafraichissement-watchlist
  --type spec`) reflete bien les nouvelles exigences.
