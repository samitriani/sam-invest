# Spec Delta

## Purpose

Decrit comment Sam_Invest recupere et met a jour, pour toute la watchlist, les
cours/fondamentaux/evenements/avis/profils et les actualites (+ classement
Haiku), ainsi que la resilience de ce cycle face aux interruptions reseau.

## ADDED Requirements

### Requirement: Isolation des echecs par instrument
Un echec de recuperation (reseau, source indisponible, ticker inconnu) sur un
instrument SHALL etre isole : il n'interrompt ni n'invalide le traitement des
autres instruments de la watchlist.

#### Scenario: Un instrument echoue parmi vingt
- **WHEN** la recuperation des cours ou des actualites echoue pour un
  instrument de la watchlist
- **THEN** les dix-neuf autres instruments sont traites normalement et leurs
  resultats sont enregistres

### Requirement: Chaine de repli des sources de donnees
Pour chaque type de donnee (cours, fondamentaux, evenements, avis analystes),
le systeme SHALL tenter les sources dans l'ordre yfinance, puis Finnhub, puis
FMP, et retenir la premiere qui repond.

#### Scenario: yfinance indisponible pour un instrument
- **WHEN** yfinance ne repond pas pour un instrument donne
- **THEN** le systeme tente Finnhub puis FMP avant de marquer la donnee
  indisponible

### Requirement: Resilience aux coupures de connexion
Un rafraichissement (donnees, news, ou global) declenche par une session
utilisateur SHALL continuer de s'executer et d'enregistrer ses resultats meme
si la connexion de cette session se coupe avant la fin.

#### Scenario: Coupure pendant un rafraichissement en cours
- **WHEN** la connexion de la session qui a declenche un rafraichissement se
  coupe avant que celui-ci ne soit termine
- **THEN** le rafraichissement continue de s'executer jusqu'a son terme (ou
  jusqu'a echec explicite), independamment de cette connexion

### Requirement: Persistance incrementale des actualites
Les actualites recuperees et leur classement SHALL etre enregistres instrument
par instrument au fur et a mesure de leur traitement, et non uniquement une
fois tous les instruments de la watchlist traites.

#### Scenario: Interruption pendant la phase actualites
- **WHEN** un rafraichissement des actualites est interrompu apres que
  certains instruments ont ete recuperes et classes, mais avant que tous les
  instruments de la watchlist ne l'aient ete
- **THEN** les actualites et classements deja obtenus pour les instruments
  traites restent enregistres en base

### Requirement: Visibilite de l'etat d'un rafraichissement
L'etat du dernier rafraichissement declenche (en cours, interrompu, ou
termine) SHALL etre consultable depuis n'importe quelle session — y compris
une session differente de celle qui l'a declenche — sans dependre du fait que
cette session d'origine soit encore ouverte.

#### Scenario: Reouverture pendant un rafraichissement en cours
- **WHEN** l'application est ouverte (ou rouverte) alors qu'un
  rafraichissement est en cours d'execution cote serveur
- **THEN** l'application indique qu'un rafraichissement est en cours, plutot
  que d'afficher un etat sans rapport avec ce qui se passe reellement

#### Scenario: Reouverture apres une interruption
- **WHEN** l'application est ouverte (ou rouverte) alors que le dernier
  rafraichissement declenche a ete interrompu avant son terme
- **THEN** l'application indique que ce rafraichissement est incomplet, avec
  l'horodatage et l'etendue de ce qui a ete obtenu avant l'interruption

### Requirement: Rafraichissement exclusif
Le systeme SHALL empecher le lancement d'un nouveau rafraichissement tant
qu'un rafraichissement est deja en cours d'execution.

#### Scenario: Lancement demande pendant un rafraichissement en cours
- **WHEN** un rafraichissement est declenche alors qu'un autre est deja en
  cours d'execution
- **THEN** ce nouveau lancement est refuse, et l'utilisateur est informe
  qu'un rafraichissement est deja en cours
