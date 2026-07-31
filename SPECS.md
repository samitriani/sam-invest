# Sam_Invest — Spécifications fonctionnelles & techniques (refonte front)

> Document de référence pour la refonte de l'interface (Claude Design) et la nouvelle
> architecture front JavaScript / back Python. Il décrit **l'existant à iso-fonctionnalité** :
> la refonte doit couvrir 100 % de ce document avant tout ajout.

---

## 1. Présentation du produit

**Sam_Invest** est un outil personnel d'aide à la décision d'investissement : une
**watchlist** (actions + ETF, thèmes Tech & pays émergents) avec données de marché,
signaux techniques, news classées, briefing pédagogique et diagnostic financier
d'entreprise.

### 1.1 Principes non négociables (identité du produit)

1. **Tout chiffre est calculé par du code** (déterministe, vérifiable). Le LLM
   (Claude) ne calcule jamais : il **explique, résume, traduit, vulgarise**.
2. **La décision finale reste humaine.** L'outil signale et éclaire, il ne donne pas
   de conseil financier. La recommandation ACHAT / GARDER / VENDRE est une heuristique
   de lecture assumée comme telle, toujours accompagnée des arguments qui la produisent.
3. **Coût API maîtrisé** : chaque action coûteuse (LLM) est déclenchée explicitement
   par l'utilisateur, jamais automatiquement. Séparation stricte :
   - Données de marché : **gratuit** (aucun appel Claude)
   - News : **Claude Haiku** (classement/traduction)
   - Briefing : **Claude Sonnet** (1 seul appel pour toute la watchlist)
   - VN Diagnostic : **Claude Opus** (conclusions par étape, streamées)
4. **Transparence des sources** : chaque valeur affiche sa provenance
   (yfinance / Finnhub / FMP / calculé / LLM).

### 1.2 Utilisateur cible et ton

Investisseur **particulier débutant/intermédiaire**, francophone, qui consulte le soir
ou le week-end. Le produit vulgarise systématiquement : le jargon financier est
toujours accompagné d'une définition (tooltips glossaire). Langue : **français**.

---

## 2. Architecture cible

```
┌─────────────────────┐     HTTP/JSON + SSE      ┌──────────────────────┐
│  Front React (SPA)   │ ◄──────────────────────► │  API FastAPI (Python) │
│  Vite + TypeScript   │                          │  api.py               │
│  Tailwind + shadcn   │                          └──────────┬───────────┘
│  TanStack Query      │                                     │ imports directs
│  Recharts            │                          ┌──────────▼───────────┐
└─────────────────────┘                          │  Package sam_invest/  │
                                                  │  (logique existante,  │
                                                  │   inchangée)          │
                                                  │  SQLite data/*.db     │
                                                  └───────────────────────┘
```

- **Front** : React 18 + Vite + TypeScript, Tailwind CSS, shadcn/ui, TanStack Query
  (data fetching/cache), Recharts (graphiques). SPA, pas de SSR.
- **Back** : FastAPI qui enveloppe le package `sam_invest/` existant
  (data_sources, signals, indicators, rules, briefing, diagnostic, idees, llm, db).
- **Base** : SQLite locale (`data/sam_invest.db`), inchangée.
- **Secrets** : `.env` côté back uniquement (ANTHROPIC_API_KEY, FINNHUB_API_KEY,
  FMP_API_KEY). **Aucune clé n'atteint le navigateur.**
- **Types partagés** : générés depuis l'OpenAPI de FastAPI via `openapi-typescript`.

---

## 3. Modèle de données (SQLite, existant)

| Table | Contenu | Clé |
|---|---|---|
| `prices` | Historique de clôture 2 ans (date, close) | (ticker, date) |
| `quotes` | Dernier cours, variation séance, high/low 52s, drawdown, source | ticker |
| `fundamentals` | CA TTM, croissance, marge nette, dette/capitaux, source | ticker |
| `events_estimates` | Date résultats, ex-dividende, révisions EPS 30j/7j, objectifs de cours | ticker |
| `analyst_ratings` | Consensus analystes (achat fort/achat/conserver/vendre/vendre fort) + tendance mensuelle + upgrades/downgrades 90j | ticker |
| `profile` | Payload JSON profil complet (secteur, PER, ROE, description…) | ticker |
| `news` | id, ticker, datetime, headline, summary, url, source | id |
| `news_analysis` | Payload JSON : [{headline, categorie, tonalite, resume, titre_fr, resume_fr}] | ticker |
| `update_log` | Journal des mises à jour (asof, kind, statut, detail) | id |
| `briefing_cache` | Dernier briefing Sonnet généré (global + par instrument) + horodatages donnees/news au moment de la génération — persistant, récupération cross-appareil | 1 ligne |
| `flags_seen` | Ancienneté de chaque flag (première/dernière vue) pour distinguer « nouveau » et « persistant » | ticker\|règle\|sévérité |
| `diagnostic_cache` | Diagnostics Opus conservés : chiffres + conclusions par étape + executive summary + reco, avec statut `partiel`/`complet` — écrit après chaque étape (résilience réseau), récupération cross-appareil | ticker |

**Watchlist** : stockée dans `config.yaml` (pas en base). Un instrument =
`{ticker, nom, type: "action"|"ETF", theme}`.

**Sources de données de marché** avec repli en cascade : **yfinance → Finnhub → FMP**.

---

## 4. Spécifications fonctionnelles — écrans

L'app a **6 écrans de travail + un écran « À propos »**, dans un **menu burger**.

### 4.0 Navigation et actions globales (menu ☰)

**Navigation en menu burger** (`st.navigation`, position sidebar). Motivation :
l'usage principal est le **téléphone, en déplacement**.
- Une seule page est rendue par run → page légère sur mobile (les onglets
  rendaient les 6 écrans à chaque interaction).
- **L'URL porte la page courante** (`/briefing`, `/vn-diagnostic`…) : une coupure de
  connexion suivie d'une reconnexion ramène sur la même page, alors que les
  onglets repartaient systématiquement du premier.
- Sur grand écran le menu est déplié en permanence ; sous ~640 px il se replie
  derrière l'icône ☰ et se referme après chaque navigation.

**Contenu du menu** (sous la liste des pages) :
- Titre produit + baseline.
- Bouton **« Tout mettre à jour »** (données + news, jamais le briefing Sonnet).
  Désactivé si watchlist vide. S'exécute **avant** le rendu de la page courante.
- Bouton **« Exporter (.md) »**, rempli en fin de script pour inclure le briefing
  et le diagnostic générés pendant le run.
- Fraîcheur données / news, avec ⚠️ au-delà du seuil.
- Zone d'avertissements de configuration (clés manquantes, watchlist vide) —
  repliable, mise en avant si la watchlist est vide.
- Pendant une mise à jour : **barre de progression** avec message d'étape
  (ex : « News 3/10 — NVDA : 5 trouvées »), puis **compte-rendu** :
  résumé (succès/erreur) + détail par instrument repliable (rendus dans la zone
  principale, pas dans le menu).

**Règle de rédaction** : les pages de travail ne portent qu'**une ligne** de
contexte (titre + accroche + renvoi vers « À propos »). Toute explication de
méthodologie, de code couleur ou de coût vit dans l'écran « À propos » (§ 4.7) :
sur un écran de téléphone, un paragraphe en dur repousse les chiffres hors champ.

**Lisibilité mobile** : les rangées de métriques utilisent un conteneur horizontal
qui s'enroule (2–3 par ligne sous 375 px) et non `st.columns`, qui empile
verticalement — une rangée de 5 métriques valait sinon 5 écrans de défilement.

### 4.1 Écran « Données » (défaut)

Données de marché et signaux techniques. **Aucun appel LLM.**

**Actions utilisateur**
- Bouton « Mettre à jour les données » + horodatage de dernière mise à jour.

Toutes les sections de cet écran sont **pliables** (`Actions`, `ETF`, `Calendrier`,
`Estimations, révisions & consensus`). Streamlit interdisant d'imbriquer un expander
dans un expander, « À venir & estimations » reste un titre et ses deux tableaux sont
chacun pliables. Actions/ETF sont dépliés par défaut, les deux autres repliés.

**Bloc 1 — Tableau watchlist & signaux**, séparé **Actions** / **ETF** (un
expander chacun) :

| Colonne | Format | Tooltip glossaire |
|---|---|---|
| Ticker, Nom, Thème | texte | — |
| Cours | 2 déc. | oui |
| Séance % | 1 déc., **vert si ≥ 0, rouge si < 0** | oui |
| Drawdown 52s % | 1 déc. | oui |
| Position 52s % | 0 = plus-bas 52s, 100 = plus-haut | oui |
| RSI 14 | entier | oui |
| État RSI | survendu / neutre / suracheté | oui |
| Tendance | haussière / baissière / neutre (SMA50 vs SMA200) | oui |

**Bloc 2 — « À venir & estimations » (actions uniquement)**, deux tableaux pliables
(empilés, et non côte à côte : sur téléphone deux tableaux côte à côte sont illisibles) :
- *Calendrier* : date de résultats + « dans X j » (auj./demain/X j/passé), ex-dividende.
- *Estimations, révisions & consensus* : révisions EPS 30j (net, hausses, baisses),
  consensus analystes (Achat / Conserver / Vendre), objectif de cours moyen,
  potentiel % vs cours actuel.

**États** : watchlist vide → invitation à la remplir ; colonnes vides → inviter à
lancer une mise à jour.

### 4.1 bis Écran « Par instrument »

Détail d'**un seul** instrument de la watchlist, choisi dans un sélecteur.
**Aucun appel LLM.** Écran distinct de « Données » : c'est une consultation ciblée
(« où en est NVDA ? ») et non la vue d'ensemble ; les séparer évite de faire défiler
tout le tableau de la watchlist pour l'atteindre, et allège les deux écrans.

- **Auto-récupération** : à la sélection, si les données de l'instrument ne sont pas
  du jour, elles sont récupérées automatiquement (cet instrument seul, une tentative
  par jour et par session ; la fraîcheur globale de la page n'est pas modifiée).
- Graphique linéaire du cours (historique 2 ans).
- 5 métriques : Dernier, SMA 50, SMA 200, RSI 14, Plus-haut 52s.
- **Fondamentaux** selon le type :
  - *Action* : capitalisation, secteur, PER trailing/forward, Price/Book, marge nette,
    ROE, rendement dividende, croissance CA, croissance BPA, dette/capitaux,
    current ratio (grille 4 colonnes, ~4 rangées) + source et date.
  - *ETF* : champs spécifiques du profil (encours, frais, exposition…).
- **Avis des analystes** (actions) : consensus courant (5 compteurs : achat fort →
  vendre fort), tendance vs mois dernier (↗️/↘️/→ sur les avis à l'achat), et tableau
  des upgrades/downgrades des 90 derniers jours (🟢 relevé · 🔴 abaissé · 🆕 initié ·
  ⚪ confirmé, avec firme, de → vers).

**États** : watchlist vide → renvoi vers l'écran Watchlist ; pas d'historique →
message dédié.

### 4.2 Écran « News »

News récentes par instrument, **classées et traduites par Claude Haiku**.

**Actions utilisateur**
- Bouton « Mettre à jour les news » + horodatage.
- Si pas de clé Claude : bandeau « les news s'affichent en clair mais ne sont ni
  classées ni résumées ».

**Contenu** : une section repliable par instrument (`TICKER — Nom (N news)`) ;
à l'intérieur, une **carte par news** :
- **Indicateur de tonalité** : positif (vert) / neutre (gris) / négatif (rouge).
- **Badge catégorie** : résultats, produit, réglementaire, macro, dirigeant, autre.
- **Titre traduit en français** (sinon titre original).
- **Résumé français** (traduction du résumé source, tronqué à 500 caractères),
  sinon résumé d'une phrase généré par Haiku.
- Lien « Lire l'article original » + date (JJ/MM) + source (yfinance/finnhub/fmp).

Mode **compact** (réutilisé dans le Briefing) : titre + tonalité seulement.

**États** : aucune news en base → inviter à mettre à jour (+ conseil clé Finnhub).

### 4.3 Écran « Briefing » (cœur du produit)

Vue d'ensemble pédagogique + recommandation par instrument. **1 seul appel Sonnet**
pour tout, déclenché explicitement.

**Actions utilisateur**
- Bouton « Générer le briefing » (désactivé sans clé Claude).
- Le briefing **reprend le contenu des pages Données et News**. Rappel des
  horodatages données/news avec ⚠️ si l'un date de plus de 2 h.
- **Garde-fou de fraîcheur** : au clic, si les données ou les news datent de plus de
  2 h (ou n'ont jamais été récupérées), aucun appel Sonnet n'est lancé ; un message
  invite gentiment à rafraîchir la page concernée d'abord.
- **Cache persistant, sans appel redondant** : le briefing généré est enregistré en
  base (table `briefing_cache`), pas seulement dans la session du navigateur.
  - **Récupération cross-appareil** : à l'ouverture de la page, si cette session
    n'affiche encore rien (nouvel appareil/navigateur), le dernier briefing généré
    est rechargé automatiquement depuis la base — sans appel Claude.
  - **Anti-doublon** : au clic sur « Générer le briefing », si les données ET les news
    n'ont pas changé depuis la dernière génération (comparaison des horodatages
    `donnees_asof`/`news_asof` mémorisés), le texte déjà généré est simplement
    rechargé (message « Données et news inchangées… ») — aucun nouvel appel Sonnet,
    aucun coût. Un nouvel appel n'a lieu que si Données ou News ont été rafraîchies
    depuis.
- **Recommandation** : badge coloré **ACHAT** (vert) / **GARDER** (orange) /
  **VENDRE** (rouge) — `st.badge`, et directive markdown `:green-badge[…]` dans le
  libellé de l'expander pour être visible sans ouvrir la section. Le mot est toujours
  écrit : la couleur ne fait que le renforcer (lisible sans distinguer les couleurs).
  Récapitulatif « achat : x · garder : y · vendre : z » en tête de la liste, masqué
  si aucune reco n'est connue (briefing antérieur à la fonctionnalité).

**Bloc 1 — Vue d'ensemble**
- Compteur : « N alerte(s), M info(s) ».
- **Synthèse globale rédigée par Sonnet** (paragraphe markdown), avec date des
  données utilisées.
- Sans briefing généré : invitation à cliquer ; sans clé API : bandeau explicatif
  (les flags et chiffres restent disponibles).

**Bloc 2 — Par instrument** (une section repliable par ligne de watchlist)
- Titre de la section = **badge de reco + ticker + nom**. Pas de pastille de flag
  en plus : les deux systèmes d'icônes se télescopaient, et le rond rouge criait
  sur chaque ligne sans rien apprendre (le détail est à l'intérieur).
- Tri : ce qui mérite l'attention en haut (nouvelle alerte > alerte > info > rien),
  puis par reco (vendre d'abord), puis alphabétique. L'ordre de la watchlist n'est pas un ordre de priorité.
- Dans la section, dans l'ordre :
  1. **Recommandation** (badge) puis **briefing en 3 parties** (Sonnet) :
     **📊 analyse des chiffres** (page Données), **📰 analyse des news** (page News),
     **🎯 conclusion & arguments** — qui justifient la reco.
  2. **Chiffres clés** : cours, séance %, RSI 14, tendance, drawdown 52s.
  3. **Événements** (actions) : résultats dans X j, révisions 30j net, potentiel %.
  4. **Flags** de l'instrument (alerte = rouge, info = jaune), « aucun flag » sinon.
  5. **News récentes** (top 4, mode compact).

**Les 5 règles de flags (déterministes, seuils dans config.yaml)** :
`chute` (baisse brutale), `technique` (signaux SMA/RSI), `degradation`
(fondamentaux), `evenements` (résultats imminents), `revisions` (révisions EPS
négatives). Sévérité : `info` | `alerte`.

### 4.4 Écran « VN Diagnostic »

Analyse financière complète d'**une entreprise au choix** (pas forcément en
watchlist). Chiffres = code ; conclusions = **Claude Opus, streamées**.

**Parcours en 3 étapes**
1. **Recherche** : champ texte (ticker ou nom, ex « NVDA », « Alibaba ») → liste de
   résultats Yahoo (symbole, nom, bourse, type). Entrée = rechercher.
2. **Sélection** : choisir l'entreprise → bouton « Analyser ».
3. **Analyse à affichage progressif** (pas d'effet tunnel) :
   - En-tête : nom, ticker, devise, exercice de référence, hypothèses WACC
     (taux sans risque, prime de marché, bêta), note de fiabilité éventuelle.
   - **Executive summary en haut**, rempli EN DERNIER (placeholder pendant l'analyse).
   - **7 étapes**, chacune : tableau de chiffres (label + valeur + source
     yfinance/calculé, marqueur « chiffre douteux » si aberration) **affiché
     instantanément**, puis conclusion Opus **streamée token par token** :
     1. Activité & marges — 2. Rentabilité — 3. Création de valeur —
     4. Structure financière — 5. Génération de cash — 6. Croissance — 7. Valorisation
   - Chaque bloc LLM est étiqueté « 🤖 LLM · Claude Opus 4.8 » (transparence).

**Persistance (résilience réseau)** — le diagnostic est l'appel le plus cher :
- Il est écrit dans `diagnostic_cache` **après chaque étape** (statut `partiel`),
  puis passé à `complet` quand l'executive summary est rédigé. Une coupure en
  pleine génération ne perd donc jamais ce qui a déjà été payé à Opus ; le
  diagnostic rouvert affiche « ⚠️ incomplet » et les étapes déjà conclues.
- **Un diagnostic conservé par entreprise.** Un sélecteur en haut de page liste
  les diagnostics conservés (ticker, nom, date, statut) et les recharge **sans
  aucun appel Claude**, y compris depuis un autre appareil. Bouton « 🗑️ Oublier ».
- Session vide (reconnexion, nouvel appareil) → le **dernier** diagnostic écrit
  est remonté automatiquement.
- Avant de relancer Opus sur une entreprise déjà analysée, la page affiche la date
  du diagnostic existant et le bouton devient « Analyser à nouveau ».

**États** : pas de clé API → bandeau bloquant ; recherche vide → suggestion ;
erreur de récupération → message d'erreur.

### 4.5 Écran « Watchlist » (édition + suggestions)

- Horodatage de dernier enregistrement (config.yaml).
- **Recherche d'instrument** par nom ou ticker (Yahoo Search, actions + ETF,
  indices exclus) → résultats en multi-sélection → « Ajouter à la watchlist »
  (dédoublonnage automatique sur le ticker).
- **Tableau éditable** de la watchlist : Ticker (requis), Nom, Type
  (action/ETF, liste), Thème (libre). Ajout/suppression de lignes.
- **Enregistrer** : validation (ticker non vide, pas de doublon, lignes invalides
  ignorées avec compteur), réécrit uniquement la section watchlist de config.yaml
  (seuils et règles préservés), puis recharge l'app.
- Édition **par ligne** dans un conteneur horizontal (pas `st.columns`, qui empile
  verticalement sous 640 px) : sur téléphone la ligne s'enroule en deux rangées
  `[ticker][nom]` / `[type][thème][🗑️]` au lieu de cinq champs anonymes empilés.
  Chaque champ porte un placeholder qui l'identifie une fois enroulé.

### 4.6 Section « Suggestions » — rendue DANS l'écran Watchlist (§ 4.5)

Ancien écran « Idées », **fusionné dans Watchlist** : proposer un instrument et
l'ajouter étaient deux écrans pour un seul geste. Rendue en section (et non en
expander) car elle contient déjà un expander par candidat.

Recommandations d'instruments à AJOUTER à la watchlist. Deux sources de
candidats combinées, puis **validation + chiffrage systématiques par le code**
avant tout affichage (garde-fou anti-hallucination).

**Sources de candidats**
1. **Pairs (Finnhub, déterministe, sans LLM)** : pour chaque action suivie,
   endpoint `/stock/peers` → entreprises comparables. Exclut les tickers déjà
   suivis, limité par source et au total.
2. **Trous thématiques (Claude Sonnet, texte seul)** : à partir de la
   répartition par thème de la watchlist actuelle, Claude identifie 2 à 4 trous
   de diversification (concentration excessive, zone/secteur absent) et propose
   1 à 2 tickers réels par trou. Claude ne cite ni ne calcule AUCUN chiffre —
   uniquement le positionnement thématique.

**Validation + chiffrage (obligatoire, quelle que soit l'origine)**
- Chaque ticker candidat est d'abord **résolu par une recherche Yahoo** ; s'il
  ne correspond à aucun instrument réel connu, il est silencieusement écarté
  (protège des tickers Claude mal formés ou inexistants).
- Les candidats retenus sont ensuite **chiffrés en direct** par le même code que
  la page Données (cours, variation séance, drawdown 52s, RSI 14, tendance
  SMA50/200, et pour les actions : secteur, PER, croissance CA, marge nette,
  dette/capitaux, consensus analystes) — **sans écriture en base** (ce ne sont
  que des candidats, pas encore suivis).

**Actions utilisateur**
- Case à cocher « Inclure les suggestions thématiques » (désactivée sans clé
  Claude) + bouton « Générer des idées » (désactivé sans watchlist, ou sans
  aucune des deux clés Finnhub/Claude).
- Tableau récapitulatif des candidats (mêmes colonnes que la page Données) +
  volet repliable par candidat (origine, justification, chiffres, fondamentaux,
  consensus).
- Multi-sélection + bouton **« Ajouter à la watchlist »** : réutilise
  `save_watchlist` (comme la page Watchlist). Les données du nouvel instrument
  se remplissent automatiquement à la première visite de la page Données
  (auto-récupération, voir 4.1 Bloc 3).

**États** : ni clé Finnhub ni clé Claude → bandeau, bouton désactivé ; aucun
candidat retenu (sources indisponibles ou tickers déjà suivis/introuvables) →
message explicite.

### 4.7 Écran « À propos »

Regroupe **toutes** les explications, en sections repliables : à quoi sert l'outil,
principe code/LLM, usage mobile & coupures de connexion, rôle de chaque page, codes
code des recos et symboles (🆕, 🚬, ⚠️), modèle Claude et coût de chaque bouton, sources
de données et stockage (avec l'état réel des clés configurées), et un **glossaire
filtrable** (source unique : `sam_invest/glossaire.py`, les mêmes définitions que
les infobulles). Avertissement « pas un conseil financier » en tête de page.

---

## 5. Composants transverses

- **Tooltips glossaire** : chaque terme de jargon (RSI, SMA, drawdown, PER, ROE,
  WACC, révisions…) porte une définition en français simple au survol. Le glossaire
  est centralisé (module `glossaire.py`, exposé par l'API).
- **Badges sémantiques** : tonalité news (positif/neutre/négatif), sévérité flags
  (alerte/info), catégorie news, tendance (haussière/baissière/neutre).
- **Recommandation en badge texte.** L'ancien codage par fruits
  (🥒 acheter / 🍊 maintenir / 🍅 vendre) a été abandonné : « concombre = acheter » ne
  s'auto-explique jamais, et l'icône se télescopait avec les pastilles de flags.
  Remplacé par un badge où le **mot est écrit** (ACHAT / GARDER / VENDRE), la couleur
  ne faisant que le renforcer. Seul autre marqueur codé : le **🚬** (chiffre douteux).
- **Étiquette de provenance** sur tout contenu LLM (« Claude Haiku/Sonnet/Opus »)
  et toute donnée (yfinance/finnhub/fmp/calculé).
- **Barres de progression** des mises à jour avec messages d'étape.
- **États vides soignés** : chaque écran a un état « pas encore de données » qui
  guide vers l'action (mettre à jour, remplir la watchlist, ajouter une clé).
- **Formats** : dates JJ/MM/AAAA HH:MM (heure locale) ; grands montants en
  T / Md / M ; pourcentages 1 décimale ; devise affichée séparément.

---

## 6. Design system

Référence complète : `DESIGN_SYSTEM_PROMPT.md` (thème **sombre luxe, vert dollar,
minimaliste**). Rappel des fondamentaux :

- **Fond** : dégradé #14181A → #0f1214 ; cartes #1C2226, bordures 1px #2A3238,
  radius 8-10px (20px pour les pills).
- **Marque** : vert dollar #2FAE72, vert clair #34D399 (hover), or #C9A96A (accents).
- **Sémantique** : hausse #22C55E, baisse #F05252, neutre #94A3B8, alerte #FBBF24,
  info #3B82F6.
- **Texte** : principal #ECEFEE, secondaire #98A2A0, tertiaire #6B7580 ; labels en
  MAJUSCULES 0.7rem letter-spacing 0.08em ; chiffres en monospace (IBM Plex Mono).
- **Animations** : 140-320ms cubic-bezier(0.4, 0, 0.2, 1) ; hover cartes = fond
  #242A30 + ombre ; navigation sticky avec backdrop-blur.
- **Layout** : container max 1440px ; grilles auto-fit minmax(280px, 1fr) ;
  responsive 2 colonnes tablette, 1 colonne mobile.

---

## 7. Spécifications techniques — API

### 7.1 Contrat REST (FastAPI)

| Méthode | Route | Description |
|---|---|---|
| GET | `/api/watchlist` | Watchlist + avertissements de config |
| PUT | `/api/watchlist` | Enregistre la watchlist (validation incluse) |
| GET | `/api/search?q=` | Recherche d'instruments (Yahoo) |
| GET | `/api/snapshots` | Snapshots signaux (tableau Données) |
| GET | `/api/events` | Calendrier + estimations (actions) |
| GET | `/api/history/{ticker}` | Historique de cours + indicateurs |
| GET | `/api/profile/{ticker}` | Fondamentaux/profil |
| GET | `/api/news` | News + analyses Haiku, groupées par ticker |
| POST | `/api/update/{kind}` | Lance une maj (`donnees` \| `news` \| `global`) → `job_id` |
| GET | `/api/jobs/{job_id}` | Progression (fraction + message) + compte-rendu final |
| GET | `/api/briefing` | Données déterministes du briefing (flags, chiffres, news) |
| POST | `/api/briefing/generer` | Synthèse Sonnet — **SSE** (streaming) |
| GET | `/api/diagnostic/search?q=` | Recherche d'entreprise |
| POST | `/api/diagnostic/{ticker}` | Diagnostic — chiffres immédiats puis conclusions **SSE** |
| POST | `/api/idees/generer` | Candidats d'ajout (pairs + trous thématiques), validés/chiffrés |
| GET | `/api/glossaire` | Dictionnaire terme → définition |
| GET | `/api/updates/last` | Horodatages des dernières maj par type |

### 7.2 Tâches longues et streaming

- **Mises à jour** (données/news) : `POST /api/update/*` démarre un job en
  arrière-plan (thread) et renvoie un `job_id` ; le front **poll** `GET /api/jobs/{id}`
  toutes les ~800 ms pour la barre de progression. Les updates news sont déjà
  parallélisées côté Python (ThreadPoolExecutor, 2 phases fetch/classement).
- **Briefing Sonnet et conclusions Opus** : **SSE** (Server-Sent Events) pour un
  affichage token par token (équivalent du `st.write_stream` actuel).
- Un seul job de mise à jour à la fois (verrou) ; SQLite : écritures depuis le
  thread du job uniquement.

### 7.3 Contraintes

- **Local-first** : app mono-utilisateur qui tourne sur le poste (pas d'auth en v1).
  CORS restreint à localhost.
- **Aucune clé API dans le front.** Tous les appels externes (yfinance, Finnhub,
  FMP, Anthropic) passent par le back.
- **Erreurs** : un échec sur un ticker n'interrompt jamais le lot (comportement
  actuel conservé) ; les comptes-rendus listent le détail par instrument.
- **Performance** : réponses API < 200 ms pour les lectures (tout vient de SQLite) ;
  les indicateurs pandas peuvent être mis en cache côté back, invalidés par
  l'horodatage de dernière mise à jour.

### 7.4 Organisation du repo (cible)

```
Sam_Invest/
├── backend/
│   ├── api.py              # FastAPI (nouveau)
│   ├── sam_invest/         # package existant, inchangé
│   ├── data/               # SQLite
│   ├── config.yaml / .env
│   └── requirements.txt
└── frontend/
    ├── src/ (pages, components, api/ types générés)
    ├── index.html, vite.config.ts, tailwind.config.ts
    └── package.json
```

Migration en 4 phases : API FastAPI → squelette front → écrans un par un
(Données → News → Watchlist → Briefing → Diagnostic) → bascule et suppression de
`app.py` (Streamlit reste fonctionnel pendant toute la migration).

---

## 8. Hors périmètre v1 (pistes v2, ne pas designer maintenant)

- Alertes automatiques (push/email/Telegram) et briefing hebdo par email.
- Suivi de portefeuille réel (positions, PRU, performance vs indice).
- Multi-utilisateurs, auth, déploiement cloud, monétisation/freemium.
- Track record des recommandations.
- App mobile native (le front doit néanmoins être **responsive**).
