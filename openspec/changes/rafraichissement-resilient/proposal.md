# Proposal

## Why

L'usage principal de Sam_Invest est mobile, et l'action principale est de tout
rafraichir (cours + actualites, 3 a 4 min). Aujourd'hui ce rafraichissement est
un seul appel synchrone Streamlit, tenu par la connexion du telephone : une mise
en arriere-plan de l'appli ou un changement de reseau (wifi -> 4G) pendant ces
3-4 minutes coupe la connexion, et le travail en cours est perdu. Pire, les
actualites et leur classement Haiku ne sont ecrits en base qu'une fois toute la
phase terminee : une coupure a la derniere seconde de cette phase fait perdre
*tout* ce qui avait deja ete recupere et paye, pas seulement ce qui restait a
faire. Resultat observe par l'utilisateur : le rafraichissement complet
reussit environ une fois sur deux sur mobile, sans message clair expliquant
pourquoi.

## What Changes

- Le rafraichissement (donnees + news) continue de s'executer cote serveur meme
  si la connexion du navigateur (telephone) se coupe ou que l'onglet passe en
  arriere-plan : il n'est plus lie au cycle de vie du script Streamlit qui l'a
  declenche.
- Les news et leur classement Haiku sont persistes au fur et a mesure (par
  ticker), et non plus seulement a la fin de toute la phase : une coupure ne
  fait plus perdre un travail deja recupere/paye. Les prix le sont deja
  (inchange).
- L'etat d'un rafraichissement (en cours / interrompu / termine, et sa
  progression) est suivi en base, pour qu'une page rouverte (meme appareil ou
  un autre) puisse afficher ou en est le rafraichissement au lieu de ne rien
  montrer.
- Le bouton qui enchaine rafraichissement puis synthese Sonnet (« donnees et
  analyse ») fait lui aussi tourner la synthese dans le cycle serveur : elle
  dure 2 a 3 min de plus sous la meme exposition aux coupures, et un thread
  detache ne peut pas enchainer une etape qui vit dans le script Streamlit. La
  synthese est ecrite en base (`briefing_cache`, deja relue cross-appareil) sans
  dependre de la session qui l'a declenchee. « Lancer l'analyse » (synthese
  seule, sur l'existant) garde son appel direct.
- Un second rafraichissement ne peut pas se lancer tant qu'un premier est en
  cours (aucun garde-fou aujourd'hui).
- Affichage minimal, repris du style existant (caption + barre de progression) :
  "rafraichissement en cours" / "interrompu, relance si besoin" / horodatage de
  fin. Aucune refonte des menus, boutons ou pages n'est dans le perimetre de ce
  changement (volet mis en standby, voir conversation) ; seul le texte
  strictement necessaire pour rendre l'etat resilient visible est ajuste.

## Capabilities

### New Capabilities
- `rafraichissement-watchlist` : le cycle de recuperation des cours,
  fondamentaux, evenements, avis analystes, profils et actualites (+
  classement Haiku) pour toute la watchlist — sa persistance incrementale et sa
  resilience aux interruptions de connexion. Premiere spec de ce depot : le
  comportement existant (chaines de repli, ecriture par instrument pour les
  prix) est documente tel quel, et les nouvelles exigences de resilience s'y
  ajoutent.

### Modified Capabilities
_Aucune — aucune spec n'existe encore dans ce depot (`openspec list --specs`
ne renvoie rien)._

## Impact

- `sam_invest/update.py` : `update_donnees`, `update_news`, `update_global` —
  ecriture incrementale des news/analyses (au lieu d'en bloc en fin de phase),
  et point d'entree pour un lancement non bloquant.
- `sam_invest/db.py` : suivi de l'etat d'un rafraichissement en cours (table ou
  colonnes dediees, dans l'esprit du `statut` partiel/complet deja utilise par
  `diagnostic_cache`) ; `update_log` seul ne distingue aujourd'hui que des
  mises a jour terminees.
- `app.py` : le clic sur un bouton de rafraichissement lance le travail dans un
  thread serveur detache du cycle de script Streamlit (au lieu de l'appel
  bloquant actuel dans `run_update`) ; la page affiche l'etat courant au lieu
  d'un spinner bloquant. Aucun autre changement de menu, bouton ou page.
- Aucune nouvelle dependance : `threading` est deja utilise dans `update.py`
  (`ThreadPoolExecutor` pour le fetch/classement news).
