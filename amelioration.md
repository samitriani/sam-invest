# Plan d'amelioration UX — Sam_Invest

Issu de l'audit PM du 20/07/2026 (test complet des 6 onglets, mise a jour globale,
generation du briefing, recherche Diagnostic/Watchlist, passage en viewport mobile).
Une solution concrete par probleme, avec fichiers/lignes concernes et effort estime
(S = < 1 h, M = quelques heures, L = demi-journee et plus).

## Vue d'ensemble et priorisation

| # | Probleme | Priorite | Effort | Impact |
|---|---|---|---|---|
| 1 | Bug d'unites dette/capitaux → fausses alertes | P0 | S | Confiance dans le produit |
| 2 | Alert fatigue (19 alertes / 24 instruments) | P0 | M | Saillance des signaux |
| 3 | Briefing : renvoi manuel vers les autres onglets | P1 | S | Parcours quotidien |
| 4 | Contradiction « sans verdict » vs recos 🥒/🍅 | P1 | S | Coherence du message |
| 5 | Deux systemes d'icones qui se telescopent | P1 | S | Lisibilite du briefing |
| 6 | Doublons cross-listing non detectes (ASML / ASML.AS) | P1 | M | Cout API + clarte |
| 7 | Graphique de cours trop lourd (~500 points DOM) | P2 | S | Performance |
| 8 | Feedback de generation en « caracteres recus » | P2 | S | Attente active |
| 9 | Barre d'onglets qui deborde sur mobile | P2 | S | Usage Streamlit Cloud |

---

## 1. Bug d'unites dette/capitaux (fausses alertes rouges)

**Probleme.** `yfinance` renvoie `debtToEquity` en pourcentage. L'heuristique de
`sam_invest/data_sources.py:250` ne divise par 100 que si la valeur depasse 10.
Toute societe peu endettee (D/E reel entre 2 % et 10 %, ex. NVDA = 6,55 %) passe
telle quelle et est comparee au seuil 2.0 exprime en ratio → alerte rouge
« dette/capitaux 6.55 > seuil 2.00 » fausse, que le briefing Sonnet reprend ensuite
en toutes lettres (« la dette est elevee »). La promesse « zero hallucination de
chiffre » est cassee par la donnee elle-meme.

**Solution.**
1. **Ne plus deviner l'unite : la calculer.** Dans `_fundamentals_yfinance`,
   remplacer l'heuristique par un calcul direct depuis le bilan quand il est
   disponible : `Total Debt / Stockholders Equity` via `t.balance_sheet`
   (lignes `Total Debt` et `Stockholders Equity`). C'est la meme philosophie que
   le reste de l'app : chiffre = code.
2. **Repli :** si le bilan est indisponible, considerer que `info["debtToEquity"]`
   est TOUJOURS un pourcentage (c'est le comportement documente de Yahoo) et
   diviser systematiquement par 100 — supprimer le `if > 10`.
3. **Verifier les autres chemins :** le repli FMP (`data_sources.py:315`, ratios-ttm
   renvoie deja un ratio — ne pas rediviser) et le chemin « pairs » de l'onglet
   Idees (`data_sources.py:651` et `idees.py:131`) doivent appliquer la meme regle.
4. **Test de non-regression :** un petit test avec les cas 6.55 (→ 0.0655),
   180 (→ 1.8) et 0.9 (→ 0.009) pour figer le contrat.
5. **Migration :** les valeurs fausses restent en base (`fundamentals`) jusqu'a la
   prochaine maj — apres deploiement du fix, relancer « Mettre a jour les
   donnees » une fois (a mentionner dans le message de commit ou un st.info
   temporaire).

## 2. Alert fatigue : 19 alertes sur 24 instruments

**Probleme.** Quand presque tout est 🔴, plus rien n'attire l'attention. Une partie
vient du bug n°1 ; le reste vient du fait qu'une alerte persistante (drawdown
> 20 % depuis des mois sur une watchlist tech volatile) a le meme poids visuel
qu'un evenement nouveau.

**Solution : distinguer « nouveau » et « persistant ».**
1. **Historiser les flags.** Nouvelle table `flags_history` dans
   `sam_invest/db.py` (`ticker, regle, severite, message, asof`), alimentee a
   chaque calcul de flags apres une maj des donnees (dans `update.py`).
2. **Comparer avec la maj precedente** au moment de l'affichage (Briefing,
   `app.py:731-737`) : un flag absent de la maj precedente porte un badge
   **🆕 nouveau** ; un flag deja present affiche **depuis le JJ/MM** (date de
   premiere apparition).
3. **Reordonner la vue d'ensemble :** « Vue d'ensemble — 3 nouvelles alertes,
   16 persistantes, 1 info », les nouvelles listees en premier, les persistantes
   regroupees par regle dans un expander (« 8 x drawdown > 20 % : INFY, SE, ... »).
4. **Optionnel (config) :** ajouter dans `config.yaml` un parametre par regle
   `rappel_apres_jours` (ex. 30) pour qu'une alerte persistante remonte
   periodiquement au lieu de crier tous les jours.

## 3. Briefing : le warning renvoie l'utilisateur cliquer ailleurs

**Probleme.** Si donnees ou news ont plus de 2 h, le clic sur « Generer le
briefing » (`app.py:655-665`) affiche un avertissement qui demande d'aller dans
un autre onglet, cliquer « Mettre a jour », puis revenir. Trois clics et deux
navigations sur LE parcours quotidien principal.

**Solution.**
1. **Extraire la generation dans une fonction** `_generer_briefing()` (le bloc
   `app.py:666-723` actuel), pour pouvoir l'appeler depuis deux endroits.
2. **Sous le warning, ajouter un bouton** « 🔄 Rafraichir ce qui manque puis
   generer » qui enchaine dans le meme run : `run_update(update_global, ...)`
   (ou seulement `update_donnees` / `update_news` selon ce qui est perime —
   les booleens `donnees_fraiches` / `news_fraiches` le disent deja), puis
   `_generer_briefing()`.
3. Le bouton « Generer le briefing » seul garde son comportement actuel
   (bloquant si perime) : la maitrise des couts reste explicite, on ajoute
   juste le raccourci.

## 4. Contradiction « sans verdict acheter/vendre » vs recos 🥒/🍅

**Probleme.** Le bandeau d'accueil (`app.py:348`) promet « Claude resume/explique
seulement, sans verdict acheter/vendre » alors que le briefing affiche
« 🥒 10 acheter · 🍅 1 vendre ». Le produit se contredit a l'ecran (et le README
aussi, qui dit les deux).

**Solution : assumer la reco indicative (c'est la valeur percue du briefing).**
1. **Reformuler le bandeau :** « Chiffres et signaux calcules par du code
   (deterministe). Claude redige les syntheses et propose une reco *indicative*
   (jamais un chiffre). La decision finale reste humaine. »
2. **Harmoniser le README** (section Architecture : remplacer « ni un verdict
   acheter/vendre » par « les recos 🥒/🍊/🍅 sont une heuristique de lecture,
   pas un conseil financier »).
3. L'avertissement existant sous le bouton Generer reste tel quel — il est bien.

*Alternative si tu preferes garder la promesse stricte : renommer les fruits en
« a renforcer / neutre / a surveiller » — mais c'est moins actionnable et la
nuance sera perdue a l'usage. Recommandation : option 1.*

## 5. Deux systemes d'icones qui se telescopent (flags vs fruits)

**Probleme.** Avant generation, les titres d'expanders « Par instrument »
montrent l'etat des flags (🔴/🟡/·) ; apres generation, le fruit remplace la
pastille (`app.py:781`) — on perd l'info d'alerte au moment ou on en a le plus
besoin. Et concombre = acheter ne s'auto-explique jamais.

**Solution.**
1. **Cumuler au lieu de remplacer :** titre = `{fruit} {pastille} {ticker} — {nom}`
   (ex. « 🥒 🔴 NVDA — Nvidia »). La pastille flag reste TOUJOURS affichee ;
   le fruit s'ajoute quand le briefing existe.
2. **Trier la liste** par severite de flag puis par fruit (alerte+tomate en
   premier), plutot que par ordre de watchlist : ce qui merite attention en haut.
3. **Legende unique et permanente** deja en place (`app.py:635`) — la garder,
   c'est le bon endroit ; ne pas la dupliquer dans chaque expander.

## 6. Doublons cross-listing non detectes (ASML + ASML.AS)

**Probleme.** La meme societe cotee sur deux places (ASML Nasdaq + ASML.AS
Amsterdam) est suivie deux fois : double consommation API/Claude, deux briefings
pour la meme these. La dedup a l'enregistrement (`app.py:1179`) ne compare que
le ticker exact.

**Solution : avertir sans bloquer (le doublon peut etre voulu, ex. devise).**
1. **Fonction utilitaire** `doublons_probables(watchlist)` dans
   `sam_invest/config.py` : deux instruments sont suspects si (a) meme racine de
   ticker avant le point (`ASML` vs `ASML.AS`) ou (b) noms tres proches
   (`difflib.SequenceMatcher(...).ratio() > 0.85` apres normalisation
   casse/espaces).
2. **A l'ajout** (recherche Watchlist `app.py:1119` et bouton « Ajouter » de
   l'onglet Idees `app.py:1034`) : si le candidat matche un instrument existant,
   `st.warning("ASML.AS semble etre la meme societe que ASML deja suivie
   (cotation differente). Ajout effectue — supprime l'un des deux si c'est un
   doublon.")`.
3. **Audit permanent :** dans l'onglet Watchlist, sous le tableau, une caption
   listant les paires suspectes actuelles (aucune action automatique).

## 7. Graphique de cours trop lourd (~500 points dans le DOM)

**Probleme.** `st.line_chart` (`app.py:544`) recoit 2 ans de cours quotidiens :
~500 points rendus individuellement par Vega. Page lourde, rendu lent (c'est ce
qui faisait planter les captures pendant l'audit).

**Solution : decimer, l'oeil ne verra aucune difference.**
1. Dans le bloc `app.py:539-544` : garder les 6 derniers mois en quotidien et
   reechantillonner le reste en hebdomadaire :
   ```python
   if len(dfh) > 250:
       recent = dfh.iloc[-126:]                      # ~6 mois quotidiens
       ancien = dfh.iloc[:-126]["close"].resample("W-FRI").last()
       serie = pd.concat([ancien, recent["close"]])
   ```
   → ~200 points au lieu de ~500.
2. **Optionnel (bonus UX) :** un `st.segmented_control("Periode", ["6 m", "1 an", "2 ans"])`
   au-dessus du graphe — leger, et 6 m/1 an couvrent l'usage courant.

## 8. Feedback de generation : « 11 419 caracteres recus »

**Probleme.** Pendant les ~2 min de generation du briefing, le compteur de
caracteres (`app.py:694-700`) est un jargon technique qui ne dit pas ou on en est.

**Solution : compter les instruments, pas les caracteres.**
1. Le stream Sonnet produit les sections instrument dans l'ordre de la
   watchlist. Dans le callback `_prog`, passer le buffer accumule (ou faire
   compter par `llm.synthese_et_reco` les tickers deja apparus dans le flux) et
   afficher : « 🧠 Redaction du briefing… NVDA fait, MSFT en cours (2/24) ».
2. Modification cote `sam_invest/llm.py` : le callback `progress` recoit deja la
   taille ; lui passer aussi le texte cumule (ou le nombre de sections
   detectees) — une regex sur les tickers de la watchlist suffit.
3. **Alternative plus simple** (si le format du flux est trop variable) : une
   barre `st.progress` calee sur `caracteres_recus / taille_typique` (taille du
   dernier briefing en cache comme estimation) avec un libelle « environ X % ».

## 9. Mobile : barre d'onglets qui deborde (usage Streamlit Cloud)

**Probleme.** En 375 px de large, la barre des 6 onglets mesure 480 px : les
onglets Idees et Watchlist sont invisibles sans deviner qu'il faut faire defiler
le bandeau. Les tableaux larges restent peu praticables.

**Solution : faire tenir les 6 onglets, accepter le reste.**
1. **CSS cible** dans le `st.markdown(<style>)` existant (`app.py:56`) :
   reduire police et padding du tablist sur petit ecran :
   ```css
   @media (max-width: 640px) {
     [data-testid="stTabs"] button[role="tab"] {
       padding: 0.25rem 0.45rem; font-size: 0.8rem;
     }
   }
   ```
   (a ajuster jusqu'a ce que 6 onglets tiennent en 375 px).
2. **Raccourcir les libelles** si necessaire : « 📈 Donnees », « 📰 News »,
   « 🧠 Brief », « 🔬 Diag », « 💡 Idees », « ✏️ Liste ».
3. **Tableaux :** ne rien sur-investir (les `st.dataframe` scrollent
   horizontalement, c'est acceptable) ; s'assurer juste que les colonnes les
   plus importantes (Ticker, Cours, Seance %) sont les premieres — c'est deja
   le cas.
4. **Positionner le produit desktop-first** dans le README (une ligne), et
   retester apres le deploiement Streamlit Cloud avec un vrai telephone.

---

## Ordre de mise en oeuvre conseille

1. **#1** (bug dette) — petit fix, restaure la confiance, prerequis du #2.
2. **#3** (rafraichir + generer) — le gain quotidien le plus direct.
3. **#5 puis #4** — deux retouches de coherence rapides.
4. **#2** (nouveau vs persistant) — le plus gros chantier, le plus structurant.
5. **#7, #8, #6, #9** — au fil de l'eau.
