# Sam_Invest

Outil **personnel** de **watchlist** d'investissement (actions + ETF), 100 % local.

Ce n'est **pas** un suivi de portefeuille, ni un robot de trading, ni un oracle.
C'est une **liste d'instruments a surveiller** : le systeme

1. agrege des donnees (prix, fondamentaux, news),
2. calcule des indicateurs et des signaux **par du code** (deterministe, zero hallucination de chiffre),
3. applique des **regles explicites** que tu definis,
4. signale ce qui merite ton attention.

**La decision finale reste 100 % humaine.**

---

## Architecture (non negociable)

- **Couche donnees** (`sam_invest/`, sauf `llm.py`) : 100 % Python deterministe.
  Tout chiffre — prix, ratios, indicateurs, signaux — est calcule par du code.
  Jamais par un LLM.
- **Couche jugement** (`sam_invest/llm.py`) : API Claude. Sert **uniquement** a
  resumer/classer les news (Haiku) et a rediger une synthese en langage naturel
  a partir des chiffres fournis (Sonnet). Claude ne produit jamais un prix, un
  ratio, ni un verdict acheter/vendre.

### Sources de donnees (chaine de repli)
Pour chaque donnee : **yfinance → Finnhub → Financial Modeling Prep**.
On essaie yfinance d'abord (gratuit, sans cle) et on ne sollicite les API a cle
qu'en repli, pour menager les quotas. Une panne (rate-limit, ticker introuvable,
reseau) ne fait jamais planter l'app : la donnee est marquee indisponible.

### Indicateurs techniques
Calcules en **pandas pur** (`sam_invest/indicators.py`) : SMA 20/50/200, RSI 14,
plus-haut/plus-bas 52 semaines, variation seance, drawdown.
> Note : `pandas-ta` (upstream) est incompatible avec numpy 2.x / Python 3.13.
> Les indicateurs sont donc recalcules en pandas, ce qui evite une dependance
> fragile sans rien changer au principe « chiffres = code ».

---

## Installation (une seule fois)

> `config.yaml`, `.env` et `data/sam_invest.db` sont **personnels** : ils ne sont
> jamais versionnes sur GitHub (voir `.gitignore`). Si tu recuperes ce depot pour
> la premiere fois (clone / fork / telechargement ZIP), ces 3 elements
> n'existent pas encore chez toi — les etapes ci-dessous les creent.

1. **Double-clique sur `install.bat`** (cree l'environnement `.venv` et installe tout).
2. **Configure ta watchlist** : copie `config.template.yaml` en `config.yaml`
   (le template contient deja 20 instruments pre-remplis : 10 actions + 10 ETF,
   Tech & emergents — garde-les tels quels ou edite librement). Doc detaillee
   en commentaires dans le fichier.
3. **Configure ta cle Claude (obligatoire)** : copie `.env.example` en `.env`,
   puis renseigne `ANTHROPIC_API_KEY` :
   - Cree une cle sur [console.anthropic.com](https://console.anthropic.com/settings/keys)
     (necessite un compte + credit/carte enregistree).
   - Colle-la dans `.env` : `ANTHROPIC_API_KEY=sk-ant-...`
   - Sans cle, l'app se lance mais **News, Briefing et Diagnostic sont
     desactives** (Onglet Donnees seul reste utilisable).
   - `FINNHUB_API_KEY` / `FMP_API_KEY` restent **facultatives** (repli si
     yfinance echoue) : laisse-les vides pour demarrer, tu peux y revenir plus tard.
4. La base **`data/sam_invest.db`** (historique/cache) se **cree automatiquement**
   au premier lancement : rien a faire.

Apres ca, **plus jamais de ligne de commande**.

## Utilisation quotidienne

**Double-clique sur `launch_windows.bat`** → le navigateur s'ouvre sur l'app.
Pour arreter : ferme la fenetre noire.

### 4 onglets (+ bouton « Tout mettre a jour » en haut a droite)
Chaque onglet a son propre bouton de mise a jour et affiche sa derniere date/heure,
pour maitriser la consommation d'API Claude :

- **📈 Donnees** _(0 appel Claude)_ : met a jour prix + fondamentaux + evenements/
  estimations + profils. Affiche le tableau watchlist + signaux, la section
  **« A venir & estimations »**, et **« Donnees par instrument »** en deux sous-parties :
  *Cours de l'instrument* (graphique + indicateurs) et *Fondamentaux de l'instrument*
  (actions : PER, P/B, marges, ROE, croissance, dette, FCF, dividende, objectif ;
  ETF : categorie, encours, TER, rendement, perf YTD, top holdings).
- **📰 News** _(Claude Haiku)_ : recupere et classe les news. Affiche les news par
  instrument (brutes toujours visibles, enrichies si analysees).
- **🧠 Briefing** _(Claude Sonnet, a la demande)_ : reprend le contenu des onglets
  **Donnees** et **News** ; si l'un des deux date de plus de 2 h (ou n'a jamais ete
  recupere), un message invite a le rafraichir avant de generer. Une section
  **🌍 Global** (vue d'ensemble big-picture + recap des recos) puis une section
  **📋 Par instrument**. Chaque volet donne un briefing en **3 parties** —
  **📊 analyse des chiffres** (onglet Donnees), **📰 analyse des news** (onglet News),
  **🎯 conclusion & arguments** — accompagnees d'une **recommandation codee par un
  fruit** : 🥒 concombre = acheter, 🍊 orange = maintenir, 🍅 tomate = vendre. Un seul
  appel Sonnet couvre le global ET tous les instruments. Un avertissement rappelle que
  la reco est une heuristique du LLM, **pas un conseil financier** — la decision reste
  humaine.
- **✏️ Watchlist** : **recherche par nom** (« air liquide » → `AI.PA`) via Yahoo,
  sans connaitre les tickers ; + edition directe de la liste. Enregistree dans
  config.yaml sans toucher aux regles.

Le bouton **« Tout mettre a jour »** (haut a droite) fait Donnees + News, mais
jamais la synthese Sonnet (declenchee uniquement par son bouton dedie).

---

## La watchlist par defaut (reconfigurable)

10 actions : NVDA, MSFT, AAPL, ASML, TSM, BABA, TCEHY, MELI, INFY, SE.
10 ETF : QQQ, SMH, SOXX, IGV, ARKK, EEM, VWO, INDA, FXI, EWZ.
Orientation **Tech & pays emergents**. Modifiable dans `config.yaml`.

## Les 3 regles (toutes definies dans `config.yaml`)

1. **Flag de chute brutale** (signal d'attention) — defaut : −7 % sur une seance
   **ou** −20 % depuis le plus-haut 52 semaines.
2. **Signaux techniques** — tendance (SMA50 vs SMA200), RSI survendu/suracheté,
   position dans le range 52 semaines. Affiches dans le tableau ; les cas notables
   (RSI extreme, proche du plus-bas 52s) remontent en flags.
3. **Alarme de degradation (ACTIONS uniquement)** — surveille la these via
   croissance du CA, marge nette, endettement (realise). Ne s'applique pas aux ETF.
4. **Evenements a venir (ACTIONS)** — flag *« resultats dans X jours »*
   (utile avant un versement DCA) ; date d'ex-dividende affichee.
5. **Revisions d'estimations (ACTIONS)** — signal *avance* : nombre d'analystes
   relevant vs abaissant leurs estimations d'EPS sur 30 jours. Un solde net
   negatif = attentes en degradation, plus precoce que le realise. Objectif de
   cours moyen et potentiel affiches.

Chaque flag affiche la valeur observee **et** le seuil : tout est verifiable.
Les regles 3 a 5 ne concernent que les actions (donnees via yfinance) ; les ETF
sont ignores sans erreur.

---

## Cadre d'usage

- Declenchement **100 % manuel** : aucun cron, aucune planification, aucun ordre passe.
- Watchlist de suivi : aucune quantite, aucun PRU, aucune position detenue.

## Fichiers

| Fichier / dossier | Role |
|---|---|
| `app.py` | Interface Streamlit (un seul processus) |
| `config.yaml` | **Ta** watchlist + tes regles (pre-rempli, editable) |
| `config.template.yaml` | Template commente de reference |
| `.env` | Cles API (jamais versionne) |
| `data/sam_invest.db` | Base SQLite locale |
| `sam_invest/signals.py` | Snapshot marche + signaux techniques (deterministe) |
| `sam_invest/rules.py` | Les 3 regles |
| `sam_invest/llm.py` | Couche jugement (Claude) |
| `install.bat` / `launch_windows.bat` | Install + lancement |

## Email
Fonctionnalite SMTP **reportee** (choix utilisateur). Le code n'envoie aucun email.
