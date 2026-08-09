# CLAUDE.md

Guide pour les assistants IA travaillant sur ce depot. A lire avant toute
modification. Les details fonctionnels destines a l'utilisateur final sont dans
`README.md` ; ce fichier-ci decrit le **code**, ses regles et ses pieges.

---

## 1. Ce qu'est le projet

**Sam_Invest** : outil personnel de **watchlist** d'investissement (actions +
ETF), mono-utilisateur, mono-processus. Une seule app Streamlit (`app.py`) posee
sur un package metier (`sam_invest/`) et une base SQLite locale.

Ce n'est **pas** un suivi de portefeuille (aucune quantite, aucun PRU, aucune
position), **pas** un robot de trading (aucun cron, aucune planification, aucun
ordre passe). Tout se declenche par un clic.

---

## 2. La regle non negociable : chiffres = code, texte = Claude

C'est l'identite du produit. Toute contribution doit la respecter.

| Couche | Ou | Role |
|---|---|---|
| **Donnees** | tout `sam_invest/` **sauf `llm.py`** | 100 % Python deterministe. Tout chiffre — prix, ratio, indicateur, signal, flag — est calcule par du code verifiable. |
| **Jugement** | `sam_invest/llm.py` **uniquement** | API Claude. Resume/classe/traduit des news, et redige du texte **a partir** de chiffres deja calcules. |

Consequences concretes :

- **Ne jamais** demander un chiffre a Claude, ni laisser un chiffre du modele
  atteindre l'ecran sans verification par le code. Les prompts systeme de
  `llm.py` le rappellent explicitement — ne pas les affaiblir.
- Quand Claude propose un **ticker** (`llm.generer_idees_thematiques`), le code
  le valide obligatoirement par une recherche Yahoo puis calcule ses chiffres en
  direct (`idees.evaluer_candidat`). Un ticker qui ne resout pas est ecarte
  silencieusement.
- La recommandation **ACHAT / GARDER / VENDRE** est une heuristique de lecture,
  jamais un conseil : le mot est toujours ecrit en toutes lettres (la couleur ne
  fait que le renforcer), et la mise en garde s'affiche **au-dessus** du badge.
- `llm.py` ne doit **jamais** planter l'app : cle absente, erreur reseau ou JSON
  invalide renvoient une valeur de repli (`None`, `[]`, texte d'indisponibilite).

---

## 3. Commandes

```bash
# Dependances (pas de venv sur un conteneur frais : pandas/streamlit absents)
pip install -r requirements.txt

# Lancer l'app
streamlit run app.py            # Windows pour l'utilisateur : launch_windows.bat

# Tests (scripts autonomes, PAS de pytest — voir §10)
python -m tests.test_debt_to_equity
python -m tests.test_classement_bourses
python -m tests.test_societe_financiere
```

Python 3.11+ (le devcontainer est en 3.11). `streamlit>=1.50` est un plancher
reel : l'UI utilise `st.navigation`/`st.Page`, `st.badge`,
`st.container(horizontal=...)` et le parametre `width=` des widgets.

Il n'y a **ni linter, ni formateur, ni CI** configures. La coherence de style se
tient a la main (voir §9).

---

## 4. Carte du depot

```
app.py                     UI Streamlit — 6 pages + menu burger (~2000 lignes, monolithe assume)
sam_invest/
  config.py                Chargement/validation de config.yaml + .env ; ecriture du bloc watchlist
  db.py                    SQLite : schema, migrations, tous les acces (aucun SQL ailleurs)
  data_sources.py          Recuperation externe : yfinance -> Finnhub -> FMP (~1000 lignes)
  indicators.py            SMA / RSI / 52 semaines, en pandas pur
  signals.py               Snapshot par instrument (cours + indicateurs + signaux derives)
  events.py                Vue derivee : echeances resultats/ex-div, revisions d'EPS, potentiel
  rules.py                 Les 5 regles -> objets Flag (valeur observee + seuil, toujours)
  briefing.py              Assemble le dict entierement chiffre passe a Sonnet
  diagnostic.py            Analyse financiere en 7 etapes (marges -> valorisation), deterministe
  idees.py                 Candidats d'ajout : pairs Finnhub + trous thematiques Claude, valides
  llm.py                   *** SEUL module autorise a appeler un LLM ***
  export.py                Export Markdown de tout l'etat de l'app
  glossaire.py             Definitions + formules du jargon -> tooltips (source de verite unique)
  logs.py                  Journal fichier data/sam_invest.log
tests/                     3 scripts de non-regression autonomes
config.yaml                Watchlist + seuils des regles — VERSIONNE (aucun secret)
config.template.yaml       Template commente de reference
.env / .env.example        Cles API — .env JAMAIS versionne
.streamlit/config.toml     Theme sombre natif (couleurs seulement, aucun CSS custom)
data/                      Base SQLite + log — JAMAIS versionne, cree au 1er lancement
```

Documents de reference : `README.md` (utilisateur/onboarding/deploiement),
`SPECS.md` (specification fonctionnelle exhaustive de l'existant, ecrite pour une
refonte front React/FastAPI **non entamee**), `amelioration.md` (plan d'audit UX,
en grande partie applique), `DESIGN_SYSTEM_PROMPT.md` (palette et intentions
visuelles ; l'app n'en applique qu'un sous-ensemble via le theme natif).

---

## 5. Flux de donnees

```
config.yaml ──> config.load_config() ──> AppConfig (watchlist + regles + secrets)
                                              │
                     update.update_donnees ───┤  prix, fondamentaux, evenements,
                     (0 appel Claude)         │  avis analystes, profil
                                              ▼
        data_sources (yfinance -> Finnhub -> FMP) ──> db.upsert_* ──> SQLite
                                              │
                     update.update_news ──────┤  fetch news puis classement Haiku
                     (cout Haiku)             ▼
                                        news / news_analysis
                                              │
                    signals.construire_snapshots ──> rules.tous_les_flags
                                              │
                     briefing.construire_briefing ──> dict 100 % chiffre
                                              ├──> affiche tel quel dans l'UI
                                              ├──> export.construire_export_md
                                              └──> llm.synthese_et_reco (Sonnet, streaming)
```

`update.update_global` = donnees + news. Il ne declenche **jamais** la synthese
Sonnet : seul son bouton dedie le fait.

**Chaine de repli des sources** : yfinance d'abord (gratuit, sans cle), puis
Finnhub, puis FMP — pour menager les quotas. Regle d'or de `data_sources.py` :
**aucune fonction ne plante l'app**. Un rate-limit, un ticker inconnu ou une
coupure reseau renvoient `None` / liste vide, et la donnee est marquee
indisponible en aval.

---

## 6. Couche LLM : trois modeles, trois usages, cout maitrise

| Modele | Appele par | Quand | Ce qu'il produit |
|---|---|---|---|
| Haiku | `llm.classer_news` | bouton « Actualiser les actualites » | categorie, tonalite, resume FR, traduction du titre |
| Sonnet | `llm.synthese_et_reco` | bouton « Ecrire ma synthese » | 1 SEUL appel pour le global + tous les instruments |
| Sonnet | `llm.generer_idees_thematiques` | bouton « Generer des suggestions » | tickers candidats (texte seul, valides ensuite par le code) |
| Opus | `llm.conclusion_etape_stream`, `llm.exec_summary_diagnostic_stream` | bouton « Analyser » | conclusion par etape + executive summary |

Les IDs de modeles sont **configurables** via `.env`
(`CLAUDE_MODEL_HAIKU` / `_SONNET` / `_OPUS`), avec des defauts dans
`config.Secrets`. Ne pas les coder en dur ailleurs.

Regles a preserver quand on touche a cette couche :

- **Rien de couteux ne part automatiquement.** Rafraichir les cours est gratuit ;
  les news coutent du Haiku ; la synthese et l'analyse sont derriere leur propre
  bouton. Ne jamais declencher un appel LLM dans un rerun Streamlit implicite.
- **Le cache Haiku** (`update._existing_analysis_map`) ne renvoie au modele que
  les news jamais classees ; les autres sont reprises depuis la base.
- **Streaming + recuperation partielle** : `synthese_et_reco` streame (timeout
  120 s, callback de progression pour garder la connexion Streamlit vivante) et,
  si le JSON est tronque ou l'appel coupe, `_salvage_combine` recupere par regex
  tous les blocs d'instruments complets. Ne pas simplifier cela en un
  `json.loads` nu : c'est un correctif issu d'un usage mobile reel.
- **Tout resultat paye est ecrit en base, pas en session** : briefing dans
  `briefing_cache`, diagnostic dans `diagnostic_cache` avec ecriture **apres
  chaque etape** (statut `partiel` -> `complet`). Une coupure reseau ne doit
  jamais faire perdre un appel deja facture.
- **Normalisation des recos** : `llm.normaliser_reco` tolere les variantes
  ("acheter", "hold", "alleger"...). Un libelle inattendu fait disparaitre le
  badge, jamais le texte.

---

## 7. UI Streamlit (`app.py`)

Monolithe assume, organise en sections commentees : helpers, puis une fonction
par page, puis la navigation en fin de fichier.

- **Navigation** : `st.navigation` + `st.Page` (menu burger, sidebar). Une seule
  page rendue par run — page legere sur telephone — et **l'URL porte la page
  courante** (`url_path`), donc une reconnexion revient au meme endroit. Les
  onglets (`st.tabs`) ont ete abandonnes pour cette raison : ne pas y revenir.
- **6 pages**, nommees par la question a laquelle elles repondent, pas par
  l'etage du pipeline : `Aujourd'hui` (defaut), `Ma liste`, `Une entreprise`,
  `Ma synthese`, `Analyser`, `Aide`. `Analyser` est a part car sa **portee** est
  differente : n'importe quelle societe cotee, pas seulement la watchlist.
- **Watchlist vide** -> `ecran_demarrage()` court-circuite la navigation. Ne pas
  afficher un menu et des tableaux vides a quelqu'un qui decouvre l'outil.
- **Mobile d'abord** : usage principal = telephone. Les durees et les couts sont
  annonces en **texte visible avant le clic** (pas en infobulle : il n'y a pas de
  survol sur un telephone). Le libelle d'un bouton doit annoncer le travail
  reel — un bouton qui refuse de travailler est un bouton casse.
- **CSS** : volontairement minimal, un seul bloc `st.markdown` en tete de fichier,
  limite a des selecteurs Streamlit stables (`data-testid`). Le theme vit dans
  `.streamlit/config.toml`. Ne pas construire de design system CSS parallele.
- **Tooltips** : passer par `glossaire.definition/formule/abbr` (source de verite
  unique), jamais par des textes d'aide ecrits en dur dans l'UI.
- **`st.session_state`** ne porte que de l'ephemere (`synth_global`,
  `synth_instruments`, `search_results`, `idees_candidats`, messages a rejouer
  apres un `st.rerun()`). Tout ce qui doit survivre a une coupure va en base.
- **L'export `.md`** est rempli en **fin de script**, apres `navigation.run()`,
  pour inclure ce qui a ete genere pendant le rerun courant, et est enveloppe
  dans un `try/except` : l'export ne doit jamais casser l'app.

---

## 8. Configuration, secrets, base

**`config.yaml` est versionne** (watchlist + seuils, aucun secret) : c'est la
reference deployee sur Streamlit Cloud. **`.env` et `data/` ne le sont jamais.**

- `config.save_watchlist()` reecrit **uniquement** le bloc `watchlist:` et
  preserve commentaires et sections de regles a l'identique. Ne pas remplacer par
  un `yaml.dump` du fichier entier.
- Le chargement est **tolerant** : un placeholder, un type invalide ou un doublon
  produit un warning affichable, jamais une exception.
- **Pont Streamlit Cloud** : `app.py` recopie `st.secrets` dans `os.environ` en
  tete de script, pour que `load_secrets()` ait un seul chemin de code
  (`os.getenv`) en local comme en ligne.

**SQLite** (`db.py`) — 12 tables, dont `prices`, `quotes`, `fundamentals`,
`events_estimates`, `analyst_ratings`, `profile`, `news`, `news_analysis`,
`update_log`, `briefing_cache`, `flags_seen`, `diagnostic_cache`.

- **Tout le SQL vit dans `db.py`.** Les autres modules appellent ses fonctions.
- Le schema est applique par `executescript` avec `CREATE TABLE IF NOT EXISTS` :
  une colonne ajoutee apres coup a une table existante **ne sera pas creee**. Il
  faut un `ALTER TABLE` explicite dans `init_db()` (cf. `diagnostic_cache.reco`).
  Y penser systematiquement en modifiant `SCHEMA`.
- **Concurrence** : dans `update_news`, le reseau et les appels LLM sont
  parallelises (`ThreadPoolExecutor`), mais **toutes les ecritures DB restent
  dans le thread principal**. SQLite tolere mal les ecritures concurrentes — ne
  pas deplacer un `db.upsert_*` dans un worker.

---

## 9. Conventions de code

- **Langue : francais**, pour les noms de fonctions metier, docstrings,
  commentaires, messages UI et libelles. `construire_snapshots`,
  `flags_degradation`, `evaluer_candidat`.
- **Pas d'accents dans le code ni les commentaires** (`README.md`, docstrings,
  messages UI compris) — depot cross-plateforme, encodages heterogenes. Les
  accents ne subsistent que dans `SPECS.md`, `amelioration.md`,
  `DESIGN_SYSTEM_PROMPT.md` et dans une poignee de libelles metier historiques
  (l'etat RSI de `signals.py`, repris tel quel par `rules.py` et l'UI : ne pas le
  renommer sans mettre a jour les trois endroits).
- `from __future__ import annotations` en tete de chaque module ; annotations de
  type modernes (`float | None`, `list[dict]`).
- `@dataclass` pour les structures de donnees (`Instrument`, `Snapshot`, `Flag`,
  `EventView`, `Candidat`).
- Prefixe `_` pour tout ce qui est interne au module.
- **Les docstrings expliquent le POURQUOI**, pas seulement le quoi. Ce depot
  documente ses arbitrages dans le code (ex : pourquoi `pandas-ta` a ete ecarte,
  pourquoi les fruits 🥒/🍅 ont ete remplaces par des mots, pourquoi le briefing
  n'a plus qu'un seul bouton). C'est une convention forte : la conserver.
- **Ne jamais planter** : toute I/O externe est enveloppee, avec un repli explicite.
- **Tout flag affiche la valeur observee ET le seuil.** Un signal non verifiable
  n'a pas sa place.

---

## 10. Tests

Trois scripts de **non-regression** dans `tests/`, ecrits sans framework : un
`_run()` avec des `assert`, lance par `python -m tests.<module>`. Ils ne
touchent ni le reseau ni la base et ne demandent que `pandas`.

Chacun documente en tete le **bug reel** qu'il verrouille :

| Test | Verrouille |
|---|---|
| `test_debt_to_equity` | `yfinance.info['debtToEquity']` est **toujours** un pourcentage. L'ancienne heuristique "diviser si > 10" fabriquait de fausses alertes rouges (NVDA a 6,55 %). Priorite au calcul direct sur le bilan. |
| `test_classement_bourses` | Une cotation relais (Munich, Francfort) ou marginale (MTF, OTC) doit passer **derriere** la cotation principale. Liste **noire**, jamais blanche : une place inconnue est presumee principale. |
| `test_societe_financiere` | Banques/assureurs/holdings detectes par le secteur **ou** par la forme des etats. Peugeot Invest affichait 82,6 % de "marge nette" sans avertissement. |

Convention a suivre : **quand un bug de donnees est corrige, ajouter un script du
meme format** qui reproduit le cas d'origine, avec le contexte en docstring.

---

## 11. Git

- Branche par defaut : `main`. Le deploiement Streamlit Cloud suit `main`.
- Messages de commit : **francais, sans accents**, une ligne, style
  `Domaine: ce que ca change et pourquoi`. Exemples reels :
  - `Briefing: cache persistant cross-appareil, evite les appels Sonnet redondants`
  - `Recherche : cotation principale d abord + garde-fou societes financieres`
  - `UX : 4 pages nommees par l usage, au lieu de 7 nommees par le pipeline`
- Ne jamais commiter `.env`, `data/`, `context.md`, `.streamlit/secrets.toml`.

---

## 12. Pieges connus

1. **`pandas-ta` est incompatible** avec numpy 2.x / Python 3.13 (`from numpy
   import NaN`). Les indicateurs sont recalcules en pandas pur dans
   `indicators.py` : ne pas reintroduire cette dependance.
2. **`debtToEquity` de yfinance est en pourcentage**, toujours. Voir
   `_debt_to_equity_yf` et son test.
3. **Cotation secondaire** : `.info` y est souvent vide, donc WACC / PER / PBR
   sortent a `n/d`. `search_instruments` classe la cotation principale en tete et
   l'UI marque les autres « ⚠️ cotation secondaire ».
4. **Societes financieres** : les ratios rapportes au CA n'ont pas de sens.
   `diagnostic._est_financiere` les detecte, marque les lignes concernees d'un
   ⚠️ et transmet une mise en garde a Opus dans le prompt — sans elle, le modele
   commente une "marge nette de 82,6 %" comme une performance exceptionnelle.
5. **Devise des etats != devise de cotation** : les ratios melant capitalisation
   et postes comptables (WACC, PBR, P/S) sont fausses par le change. Ils sont
   marques douteux, et les valeurs aberrantes (> 200) sont neutralisees.
6. **`st.expander` ne s'imbrique pas** : les suggestions sont une *section* et
   non un expander, parce qu'elles contiennent deja un expander par candidat.
7. **`st.rerun()` efface tout ce qui a ete ecrit pendant le run.** Les messages a
   montrer apres une generation transitent par `st.session_state`
   (`synthese_messages`) et sont rejoues au run suivant.
8. **Streamlit Cloud reinitialise le disque** a chaque redeploiement : la base est
   perdue (tout est re-telechargeable), et une watchlist modifiee **en ligne** ne
   survit pas — d'ou le bouton « ⬇️ Telecharger config.yaml » de la page Ma liste.
9. **Le log** est un fichier (`data/sam_invest.log`), pas la sortie standard :
   c'est la qu'il faut chercher pourquoi une synthese n'apparait pas.

---

## 13. Hors perimetre

- **Email / SMTP** : reporte par choix utilisateur. Les variables existent dans
  `.env.example`, le code n'envoie aucun email.
- **Suivi de portefeuille** (PRU, quantites, allocation, DCA) : retire
  volontairement. C'est un outil de watchlist.
- **Refonte front React + FastAPI** decrite dans `SPECS.md` : **non commencee**.
  Le seul front existant est `app.py`. Ne pas traiter `SPECS.md` comme l'etat du
  code — c'est une cible, doublee d'une description a iso-fonctionnalite de
  l'existant.
- **Multi-utilisateur / authentification** : hors sujet, l'outil est personnel.
