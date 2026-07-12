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
   de conseil financier. Chaque recommandation est une heuristique assumée comme telle.
3. **Coût API maîtrisé** : chaque action coûteuse (LLM) est déclenchée explicitement
   par l'utilisateur, jamais automatiquement. Séparation stricte :
   - Données de marché : **gratuit** (aucun appel Claude)
   - News : **Claude Haiku** (classement/traduction)
   - Briefing : **Claude Sonnet** (1 seul appel pour toute la watchlist)
   - Diagnostic : **Claude Opus** (conclusions par étape, streamées)
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
  (data_sources, signals, indicators, rules, briefing, diagnostic, llm, db).
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
| `profile` | Payload JSON profil complet (secteur, PER, ROE, description…) | ticker |
| `news` | id, ticker, datetime, headline, summary, url, source | id |
| `news_analysis` | Payload JSON : [{headline, categorie, tonalite, resume, titre_fr, resume_fr}] | ticker |
| `update_log` | Journal des mises à jour (asof, kind, statut, detail) | id |

**Watchlist** : stockée dans `config.yaml` (pas en base). Un instrument =
`{ticker, nom, type: "action"|"ETF", theme}`.

**Sources de données de marché** avec repli en cascade : **yfinance → Finnhub → FMP**.

---

## 4. Spécifications fonctionnelles — écrans

L'app a **5 écrans** (navigation principale) + un en-tête global.

### 4.0 En-tête global (toutes pages)

- Titre produit + baseline : « Watchlist personnelle. Chiffres calculés par du code ;
  Claude explique seulement. **La décision finale reste humaine.** »
- Bouton **« Tout mettre à jour »** (données + news, jamais le briefing Sonnet).
  Désactivé si watchlist vide.
- Zone d'avertissements de configuration (clés manquantes, watchlist vide) —
  repliable, mise en avant si la watchlist est vide.
- Pendant une mise à jour : **barre de progression** avec message d'étape
  (ex : « News 3/10 — NVDA : 5 trouvées »), puis **compte-rendu** :
  résumé (succès/erreur) + détail par instrument repliable.

### 4.1 Écran « Données » (défaut)

Données de marché et signaux techniques. **Aucun appel LLM.**

**Actions utilisateur**
- Bouton « Mettre à jour les données » + horodatage de dernière mise à jour.

**Bloc 1 — Tableau watchlist & signaux**, séparé **Actions** / **ETF** :

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

**Bloc 2 — « À venir & estimations » (actions uniquement)**, deux tableaux côte à côte :
- *Calendrier* : date de résultats + « dans X j » (auj./demain/X j/passé), ex-dividende.
- *Estimations & révisions* : révisions EPS 30j (net, hausses, baisses), objectif de
  cours moyen, potentiel % vs cours actuel.

**Bloc 3 — Détail par instrument** (sélecteur d'instrument) :
- Graphique linéaire du cours (historique 2 ans).
- 5 métriques : Dernier, SMA 50, SMA 200, RSI 14, Plus-haut 52s.
- **Fondamentaux** selon le type :
  - *Action* : capitalisation, secteur, PER trailing/forward, Price/Book, marge nette,
    ROE, rendement dividende, croissance CA, croissance BPA, dette/capitaux,
    current ratio (grille 4 colonnes, ~4 rangées) + source et date.
  - *ETF* : champs spécifiques du profil (encours, frais, exposition…).

**États** : watchlist vide → invitation à la remplir ; colonnes vides → inviter à
lancer une mise à jour ; pas d'historique → message dédié.

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
- Le briefing **reprend le contenu des onglets Données et News**. Rappel des
  horodatages données/news avec ⚠️ si l'un date de plus de 2 h.
- **Garde-fou de fraîcheur** : au clic, si les données ou les news datent de plus de
  2 h (ou n'ont jamais été récupérées), aucun appel Sonnet n'est lancé ; un message
  invite gentiment à rafraîchir l'onglet concerné d'abord.
- Légende du code reco : 🥒 acheter · 🍊 maintenir · 🍅 vendre + disclaimer
  « heuristique LLM, pas un conseil financier ».

**Bloc 1 — Vue d'ensemble**
- Compteur : « N alerte(s), M info(s) ».
- **Synthèse globale rédigée par Sonnet** (paragraphe markdown), avec date des
  données utilisées.
- **Récapitulatif des recos** : « 🥒 x acheter · 🍊 y maintenir · 🍅 z vendre ».
- Sans briefing généré : invitation à cliquer ; sans clé API : bandeau explicatif
  (les flags et chiffres restent disponibles).

**Bloc 2 — Par instrument** (une section repliable par ligne de watchlist)
- Icône de la section = fruit de la reco si briefing généré, sinon état des flags
  (rouge/jaune/neutre).
- Dans la section, dans l'ordre :
  1. **Recommandation** (fruit + libellé) — en tête, c'est le cœur.
  2. **Briefing en 3 parties** (Sonnet) : **📊 analyse des chiffres** (onglet
     Données), **📰 analyse des news** (onglet News), **🎯 conclusion & arguments**
     (justifie le fruit).
  3. **Chiffres clés** : cours, séance %, RSI 14, tendance, drawdown 52s.
  4. **Événements** (actions) : résultats dans X j, révisions 30j net, potentiel %.
  5. **Flags** de l'instrument (alerte = rouge, info = jaune), « aucun flag » sinon.
  6. **News récentes** (top 4, mode compact).

**Les 5 règles de flags (déterministes, seuils dans config.yaml)** :
`chute` (baisse brutale), `technique` (signaux SMA/RSI), `degradation`
(fondamentaux), `evenements` (résultats imminents), `revisions` (révisions EPS
négatives). Sévérité : `info` | `alerte`.

### 4.4 Écran « Diagnostic »

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
   - Le dernier diagnostic reste affiché tant qu'on ne relance pas.

**États** : pas de clé API → bandeau bloquant ; recherche vide → suggestion ;
erreur de récupération → message d'erreur.

### 4.5 Écran « Watchlist » (édition)

- Horodatage de dernier enregistrement (config.yaml).
- **Recherche d'instrument** par nom ou ticker (Yahoo Search, actions + ETF,
  indices exclus) → résultats en multi-sélection → « Ajouter à la watchlist »
  (dédoublonnage automatique sur le ticker).
- **Tableau éditable** de la watchlist : Ticker (requis), Nom, Type
  (action/ETF, liste), Thème (libre). Ajout/suppression de lignes.
- **Enregistrer** : validation (ticker non vide, pas de doublon, lignes invalides
  ignorées avec compteur), réécrit uniquement la section watchlist de config.yaml
  (seuils et règles préservés), puis recharge l'app.

---

## 5. Composants transverses

- **Tooltips glossaire** : chaque terme de jargon (RSI, SMA, drawdown, PER, ROE,
  WACC, révisions…) porte une définition en français simple au survol. Le glossaire
  est centralisé (module `glossaire.py`, exposé par l'API).
- **Badges sémantiques** : tonalité news (positif/neutre/négatif), sévérité flags
  (alerte/info), catégorie news, tendance (haussière/baissière/neutre).
- **Recos « fruits »** : 🥒 acheter / 🍊 maintenir / 🍅 vendre — identité ludique du
  produit. ⚠️ Le design system dit « no emoji » : à trancher en design (pictos SVG
  dédiés recommandés, en conservant la métaphore fruits).
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
