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

### 6 onglets (+ bouton « Tout mettre a jour » en haut a droite)
Chaque onglet a son propre bouton de mise a jour et affiche sa derniere date/heure,
pour maitriser la consommation d'API Claude :

- **📈 Donnees** _(0 appel Claude)_ : met a jour prix + fondamentaux + evenements/
  estimations + **avis des analystes** + profils. Affiche le tableau watchlist +
  signaux, la section **« A venir & estimations »** (avec le consensus
  Achat/Conserver/Vendre), et **« Donnees par instrument »** en trois sous-parties :
  *Cours de l'instrument* (graphique + indicateurs), *Fondamentaux de l'instrument*
  (actions : PER, P/B, marges, ROE, croissance, dette, FCF, dividende, objectif ;
  ETF : categorie, encours, TER, rendement, perf YTD, top holdings) et *Avis des
  analystes* (actions : consensus + tendance vs mois dernier + derniers
  upgrades/downgrades par firme sur 90 j).
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
- **🔬 Diagnostic** _(Claude Opus 4.8, a la demande)_ : recherche une entreprise
  (Yahoo), calcule un diagnostic financier complet (marges, ROE/ROA/ROIC, WACC/EVA,
  structure financiere, cash, croissance, valorisation) et redige une conclusion par
  etape + un executive summary avec preconisation (streaming, affichage progressif).
  Actions uniquement.
- **💡 Idees** _(Claude Sonnet, a la demande)_ : recommandations d'ajout a la
  watchlist. Combine des **pairs Finnhub** (entreprises comparables aux actions
  suivies, deterministe) et des **suggestions thematiques Claude** (trous de
  diversification : theme/zone sous-representee). Chaque ticker candidat, quelle
  que soit son origine, est d'abord **valide** par une recherche Yahoo puis
  **chiffre en direct** par le meme code que l'onglet Donnees (cours, tendance, RSI,
  fondamentaux, consensus analystes) — aucun ticker ni chiffre invente n'est
  affiche. Bouton **« Ajouter a la watchlist »** en un clic ; les donnees du
  nouvel instrument se rempliront automatiquement a la premiere visite de
  l'onglet Donnees.
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

## Deploiement en ligne (Streamlit Community Cloud)

Usage **personnel uniquement** (pas d'authentification multi-utilisateur). Gratuit,
zero serveur a gerer, deploiement direct depuis GitHub.

**Une fois, dans le dashboard Streamlit Cloud :**
1. Va sur [share.streamlit.io](https://share.streamlit.io), connecte-toi avec GitHub,
   autorise l'acces au depot `samitriani/sam-invest` (fonctionne avec un depot prive).
2. « New app » → repo `samitriani/sam-invest`, branche `main`, fichier `app.py`.
3. Avant (ou apres) le deploiement, ouvre **Settings → Secrets** et colle :
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   FINNHUB_API_KEY = "..."
   ```
   (mêmes valeurs que ton `.env` local ; jamais dans le depot Git).
4. Choisis la version Python la plus recente proposee (3.11/3.12) dans les
   parametres avances si demande.
5. Deploie, attends l'installation de `requirements.txt`, puis ouvre l'URL et
   clique **« Tout mettre a jour »** pour verifier que yfinance/Finnhub
   repondent bien depuis le cloud.
6. Optionnel mais recommande : **Settings → Sharing** → restreins l'acces a ton
   seul email, pour eviter qu'une URL publique fasse consommer ton credit
   Claude par un tiers.

**A savoir (compromis du plan gratuit, acceptes pour un usage perso simple) :**
- Le disque est **reinitialise a chaque redeploiement** (nouveau `git push`) et
  parfois apres une longue inactivite : la base `data/sam_invest.db` (cache
  prix/news/fondamentaux) est perdue — reclique juste sur « Tout mettre a jour »,
  tout est re-telechargeable, rien n'est perdu de facon permanente.
- Si tu modifies la watchlist **depuis l'app en ligne**, ce changement ne
  survivra PAS au prochain redeploiement : utilise le bouton **« ⬇️ Telecharger
  config.yaml »** (onglet Watchlist) juste apres modification, puis remplace le
  fichier dans ton depot local et commit/push.
- `config.yaml` est **versionne dans Git** (aucun secret dedans : juste tickers/
  noms/themes/seuils) — c'est la reference qui alimente le deploiement.

## Fichiers

| Fichier / dossier | Role |
|---|---|
| `app.py` | Interface Streamlit (un seul processus) |
| `config.yaml` | **Ta** watchlist + tes regles (versionne, editable, aucun secret) |
| `config.template.yaml` | Template commente de reference |
| `.env` | Cles API (jamais versionne ; en ligne : Secrets Streamlit Cloud) |
| `data/sam_invest.db` | Base SQLite locale (cache, jamais versionnee) |
| `sam_invest/signals.py` | Snapshot marche + signaux techniques (deterministe) |
| `sam_invest/rules.py` | Les 3 regles |
| `sam_invest/llm.py` | Couche jugement (Claude) |
| `install.bat` / `launch_windows.bat` | Install + lancement |

## Email
Fonctionnalite SMTP **reportee** (choix utilisateur). Le code n'envoie aucun email.
