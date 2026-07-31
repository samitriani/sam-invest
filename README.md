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
  a partir des chiffres fournis (Sonnet). Claude ne produit jamais un prix ni un
  ratio. La synthese et l'analyse approfondie se terminent par une **recommandation**
  ACHAT / GARDER / VENDRE, affichee en badge colore (le mot est toujours ecrit :
  la couleur ne fait que le renforcer). C'est une heuristique de lecture des
  chiffres et des news, **pas un conseil financier**. La decision reste humaine.

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
   - Sans cle, l'app se lance mais **le classement des actualites, la synthese et
     l'analyse approfondie sont desactives** (cours, chiffres cles et fiches
     entreprise restent utilisables).
   - `FINNHUB_API_KEY` / `FMP_API_KEY` restent **facultatives** (repli si
     yfinance echoue) : laisse-les vides pour demarrer, tu peux y revenir plus tard.
4. La base **`data/sam_invest.db`** (historique/cache) se **cree automatiquement**
   au premier lancement : rien a faire.

Apres ca, **plus jamais de ligne de commande**.

## Utilisation quotidienne

**Double-clique sur `launch_windows.bat`** → le navigateur s'ouvre sur l'app.
Pour arreter : ferme la fenetre noire.

### Premier lancement : l'ecran de demarrage

Tant qu'aucune valeur n'est suivie, l'app n'affiche **ni menu ni tableau vide** :
juste un ecran de bienvenue avec un champ de recherche et quelques exemples.
On coche deux ou trois entreprises, on clique **« Ajouter et commencer »**, et le
menu complet apparait. Montrer une machine a l'arret est le meilleur moyen de
perdre quelqu'un qui decouvre l'outil.

### 5 pages dans le menu ☰ (+ actions globales dans ce meme menu)
La navigation est un **menu burger** (icone ☰ en haut a gauche sur telephone ; menu
lateral deplie sur grand ecran). Une seule page est chargee a la fois — page legere
sur mobile — et **l'adresse porte la page courante**, donc une coupure de connexion
suivie d'une reconnexion ramene exactement ou on etait.

Chaque page est nommee par la **question a laquelle elle repond**. Les quatre
premieres parlent des valeurs **que tu suis** ; la cinquieme, **Analyser**,
s'applique a **n'importe quelle entreprise cotee** — c'est cette difference de
**portee** qui justifie qu'elle soit a part. Le menu contient aussi
**« Tout mettre a jour »**, **« Exporter (.md) »** et la fraicheur des donnees.

- **📈 Aujourd'hui** — *« Quoi de neuf ? »* C'est l'accueil, parce que c'est la
  question qu'on se pose en ouvrant l'app. Tableau des cours et signaux (*Actions*
  / *ETF*, sections pliables), section **« A venir & estimations »** avec le
  consensus analystes, puis le **fil des actualites recentes**, tous instruments
  confondus, du plus recent au plus ancien. Deux boutons : *Actualiser les cours*
  (gratuit) et *Actualiser les actualites* (Claude Haiku pour le classement et la
  traduction).
- **✏️ Ma liste** — *« Qu'est-ce que je suis ? »* Placee en deuxieme parce que
  c'est par la qu'on commence. Recherche par nom (« air liquide » → `AI.PA`, la
  cotation principale sortant toujours en tete), edition ligne par ligne (nom,
  type, theme, retrait 🗑️) et **💡 Suggestions d'ajout** _(Claude Sonnet, a la
  demande)_ : pairs Finnhub + suggestions thematiques, chaque candidat valide et
  chiffre par le code avant affichage.
- **🔎 Une entreprise** — *« Je creuse celle-ci. »* Le detail d'UNE valeur **suivie** :
  *son cours* (graphique + indicateurs), *ses chiffres cles*, *ce qu'en disent les
  analystes*, *ses actualites*. Tout sort de la base locale — donc uniquement pour
  les valeurs de ta liste. Un lien en bas renvoie vers **Analyser**.
- **🧠 Ma synthese** — *« Aide-moi a faire le point. »* Une section **🌍 Global**
  puis une section **📋 Valeur par valeur**. Chaque volet donne une analyse en
  **3 parties** — chiffres, actualites, conclusion & arguments — plus une
  **recommandation ACHAT / GARDER / VENDRE** en badge, visible dans le titre sans
  avoir a l'ouvrir. Un seul appel Sonnet couvre le global ET tous les instruments.
- **🔬 Analyser** — *« Cette boite vaut-elle quelque chose ? »* **Portee differente :
  toute entreprise cotee, suivie ou non.** Recherche libre (tes valeurs et tes
  analyses passees sont proposees en raccourci), puis une analyse financiere en
  **7 etapes** _(Claude Opus, ~1 min)_ : marges, rentabilite, creation de valeur,
  structure financiere, cash, croissance, valorisation. Une conclusion par etape,
  une conclusion generale et sa **recommandation**. Rien n'a besoin d'etre en base :
  les comptes sont recuperes en direct. Les analyses sont **conservees** et se
  rouvrent gratuitement ; l'ecriture se fait **apres chaque etape**, donc une
  coupure reseau ne perd jamais ce qui a deja ete paye. Sous l'analyse, un bouton
  **« ➕ Suivre cette entreprise »** : c'est la que se prend la decision d'ajouter.
- **ℹ️ Aide** — methodologie code/LLM, role de chaque page, code des recos et
  symboles (🆕, ⚠️), cout et duree de chaque bouton, sources de donnees, glossaire
  filtrable. Les pages de travail ne portent plus de renvoi vers l'aide : une page
  qui doit pointer son mode d'emploi a chaque en-tete ne s'explique pas toute seule.

La recommandation est une heuristique de lecture produite par Claude, **pas un
conseil en investissement** — la mise en garde est affichee **au-dessus** du badge,
pour etre lue avant lui. La decision reste humaine.

Le bouton **« Tout mettre a jour »** (dans le menu ☰) fait cours + actualites, mais
jamais la synthese Sonnet (declenchee uniquement par son bouton dedie).

_L'analyse approfondie suit le deroule en etapes de la methode d'analyse financiere
de **Veronique Nguyen** (l'app s'est longtemps appelee « VN Diagnostic »)._

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
  config.yaml »** (page Ma liste) juste apres modification, puis remplace le
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
