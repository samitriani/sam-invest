# Design

## Context

Aujourd'hui, `run_update()` (app.py) appelle `update_donnees`/`update_news`/
`update_global` (sam_invest/update.py) de facon synchrone, dans le script
Streamlit declenche par le clic. Les prix sont deja ecrits instrument par
instrument (`_maj_donnees_instrument`), mais les news et leur classement Haiku
ne sont ecrits qu'une fois toute la phase terminee (boucles apres les
`ThreadPoolExecutor` dans `update_news`). Un precedent proche existe pour le
diagnostic Opus (`_lancer_analyse` + `diagnostic_cache`, statut
partiel/complet) : chaque etape est ecrite en base au fur et a mesure, pour
qu'une interruption laisse un resultat partiel consultable. Mais ce precedent
reste execute DANS le script Streamlit du clic : si le serveur annule cette
execution (session jugee deconnectee), le travail s'arrete reellement — seule
une trace de ce qui etait fait subsiste, rien ne continue. Voir
`proposal.md - Why` pour le constat cote utilisateur (coupure mobile en cours
de rafraichissement, reussite ~1 fois sur 2).

Contrainte a preserver (CLAUDE.md §8) : toutes les ecritures SQLite restent
dans un seul thread a la fois (le fetch reseau/LLM peut etre parallelise, pas
les ecritures).

## Goals / Non-Goals

**Goals:**
- Le rafraichissement continue de s'executer et d'ecrire ses resultats meme si
  la connexion qui l'a declenche se coupe.
- Les news/classements sont persistes par ticker, au meme niveau de granularite
  que les prix.
- L'etat du dernier rafraichissement (en cours / interrompu / termine) est
  lisible en base par n'importe quelle session.
- Un seul rafraichissement s'execute a la fois.

**Non-Goals:**
- Pas de refonte des menus, boutons ou pages (volet UI explicitement en
  standby — voir proposal.md - Impact). Seul le texte d'etat minimal change.
- Pas de changement a la chaine de repli des sources (yfinance -> Finnhub ->
  FMP) ni aux donnees recuperees.
- Pas de file de taches externe, de cron, ni de notification push : outil
  mono-utilisateur, mono-processus (CLAUDE.md §1).
- Pas de coordination multi-appareils temps reel (push) : lecture au prochain
  rendu de page, pas un flux live.

## Decisions

### D1. Thread serveur detache, independant de la session Streamlit

Le clic lance le travail dans un `threading.Thread` demarre par le process
Streamlit, et non plus comme un appel bloquant dans le script de la session.
Streamlit Community Cloud fait tourner un seul process Python partage par
toutes les sessions d'une meme app ; un thread demarre dans ce process survit
a la deconnexion de la session qui l'a lance, tant que le process reste vivant.

**Alternative ecartee — ameliorer seulement la persistance (reprendre le
pattern diagnostic tel quel)** : resout la perte de donnees mais pas l'echec
percu par l'utilisateur. Il faudrait toujours tout relancer a chaque coupure,
ce qui est le probleme signale, pas seulement sa consequence.

**Alternative ecartee — file de taches externe (Celery/RQ/cron)** : ajoute une
dependance et un process a operer, disproportionne pour 20 instruments dans un
outil mono-utilisateur assume comme tel.

### D2. Rafraichissement exclusif (verrou en base)

Un enregistrement d'etat en base (pas seulement en memoire) refuse un nouveau
lancement tant que le precedent n'est pas `termine` ou `interrompu`. En base
(pas seulement en memoire) pour survivre a un redemarrage du process (ex.
redeploiement Streamlit Cloud) sans etat fantome indetectable : voir le risque
dedie ci-dessous pour la requalification automatique.

Necessaire aussi pour proteger l'invariant "un seul thread ecrit a la fois" :
le process etant partage, deux clics (ou deux appareils) pourraient sinon
lancer deux threads ecrivains concurrents.

### D3. Table de suivi d'etat du cycle

Un enregistrement mutable par cycle de rafraichissement (etendant
`update_log` ou une table dediee) : `statut` (`en_cours` | `interrompu` |
`termine`), `type` (donnees/news/global/global_analyse), horodatage de debut, et l'etat de
chaque etape (voir D6). Ecrit par le thread de travail a chaque etape ; lu par n'importe quelle session au rendu de page.

**Alternative ecartee — reutiliser `diagnostic_cache` tel quel** : son grain
est "un statut par ticker", adapte a la persistance par instrument (voir D4)
mais pas a representer l'etat d'ENSEMBLE du cycle (une seule ligne lisible
d'un coup d'œil, independante du nombre d'instruments).

### D4. Persistance des news par ticker, pas par phase

Dans `update_news`, ecrire `db.replace_news` et `db.upsert_news_analysis` pour
chaque ticker des qu'il est pret (resultat du fetch puis du classement),
au lieu d'attendre la fin de la boucle `as_completed` sur l'ensemble de la
watchlist — au meme niveau de granularite que les prix
(`_maj_donnees_instrument`). L'invariant "un seul thread ecrit" est preserve :
c'est toujours le meme thread de travail qui ecrit, seul le moment de chaque
ecriture avance (par ticker plutot qu'en bloc final).

### D5. Synthese Sonnet dans le meme thread (`global_analyse`)

Le bouton « donnees et analyse » enchaine aujourd'hui `update_global` puis
`ecrire_synthese` dans le script Streamlit ; un thread detache ne peut pas
rendre la main a ce script entre les deux. La partie non-UI de
`ecrire_synthese` (cache inchange, `construire_briefing`, appel Sonnet,
`save_briefing_cache`) est extraite en `update.generer_synthese`, sans `st`.
`app.ecrire_synthese` en devient un enrobage UI (barre de progression,
`session_state`) pour « Lancer l'analyse », qui reste un appel direct. Un cycle
`global_analyse` fait `update_global` puis `generer_synthese` ; si la synthese
echoue, les donnees sont deja en base et le resume du cycle le dit.

**Alternative ecartee — refresh seul en arriere-plan, synthese a part** :
retire le « tout en un clic » qui est l'usage principal, et laisse les 2-3 min
de Sonnet exposees a la meme coupure.

La page « Ma synthese » ne relit `briefing_cache` que si sa session est vide :
apres un cycle `global_analyse`, une session qui affichait une ancienne
synthese doit la remplacer par celle du cache plus recente.

### D6. Etat par etape, trois lignes fixes

L'etat du cycle porte un JSON par etape (`donnees`, `news`, `synthese`) :
`etat` (`hors` | `attente` | `en_cours` | `ok` | `partiel` | `echec`), `fait`,
`total`, et pour les news `phase` (`fetch` | `classement`). Le kind du cycle fixe
quelles etapes sont `hors` (cours seuls : news et synthese `hors`). Ce stockage
remplace la chaine de progression unique : une chaine ne peut pas dire qu'une
etape est finie pendant qu'une autre tourne.

Un compteur final compte les instruments **sans erreur** (cours obtenus ; news
recuperees, ecrites et classees sans echec), pas ceux qui ont des news : un ETF
sans actualite recente n'est pas un manque, sinon `⚠️` serait permanent. `ok` si
tous, `partiel` si certains, `echec` si aucun. Une etape `en_cours` ou `attente`
d'un cycle `interrompu` s'affiche coupee / non lancee, deduit au rendu sans
ecriture supplementaire.

L'UI affiche toujours les trois lignes (`—` pour `hors`), sans phrase
explicative, dans un encadre info / succes / avertissement selon l'etat. Aucun
nouveau bouton ni reorganisation de menu. Le resume en phrase et les details par
instrument restent disponibles (resume dans `update_log`, details en expander).

## Risks / Trade-offs

- **[Risque]** Le process redemarre (veille Streamlit Cloud, redeploiement)
  pendant un refresh : le thread meurt avec le process, l'etat reste
  `en_cours` indefiniment (etat fantome). **Mitigation** : si l'etat lu est
  `en_cours` sans progression depuis une duree bien superieure a la duree
  normale (3-4 min), le requalifier automatiquement en `interrompu` a la
  lecture.
- **[Risque]** Deux sessions ouvertes simultanement peuvent lire un etat
  legerement perime (pas de push temps reel). **Mitigation** : acceptable
  pour un outil mono-utilisateur ; l'etat se rafraichit au prochain rendu de
  page.
- **[Risque]** Le thread capture la configuration (watchlist) au moment du
  clic ; une modification de la watchlist pendant un refresh en cours ne sera
  prise en compte qu'au prochain cycle. **Mitigation** : consequence directe
  du verrou d'exclusion (D2) — il suffit d'attendre la fin ou l'interruption
  pour relancer sur la liste a jour.
- **[Risque]** Un `threading.Thread` tue brutalement (arret du process) peut
  interrompre une ecriture en cours. **Mitigation** : chaque ecriture
  (par ticker) est une transaction SQLite independante (`with get_conn() as
  conn`, deja le cas dans db.py) ; au pire, seule l'ecriture en cours au
  moment de la coupure est perdue, jamais les precedentes.

## Migration Plan

- Changement additif en base (nouvelle table ou nouvelles colonnes) : suivre
  le pitfall #2 de CLAUDE.md (`ALTER TABLE` explicite dans `init_db()`, le
  `CREATE TABLE IF NOT EXISTS` ne modifie pas une table existante).
- Deploiement : un commit/push vers `main` (Streamlit Cloud suit `main`), pas
  d'etape manuelle.
- Rollback : revert du commit. L'ajout etant additif, l'ancien code ignore
  simplement la nouvelle table/colonnes s'il faut revenir en arriere.

## Open Questions

- Faut-il persister le classement Haiku par sous-lots (ex. tous les 5
  tickers) plutot qu'au fil de chaque `as_completed` individuel, pour encore
  reduire la fenetre de perte ? N'affecte pas les specs (le comportement
  observable — persistance par ticker — reste le meme) ; tranchable a
  l'implementation.
- Duree exacte avant de requalifier `en_cours` en `interrompu` (propose : 10
  min, largement au-dessus des 3-4 min attendus) : ajustable sans impact sur
  les specs.
