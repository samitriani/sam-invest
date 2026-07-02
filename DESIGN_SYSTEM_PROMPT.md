DESIGN SYSTEM FINANCIER LUXE - PROMPT CLAUDE CODE
================================================

## CONTEXTE
App financière personnelle : watchlist + briefing + détail instrument + news.
Design : sombre luxe, vert dollar, minimaliste, animations fluides.

## PALETTE COULEURS (HEX)
Marque:
  - Vert dollar: #2FAE72
  - Vert clair: #34D399
  - Or: #C9A96A

Surfaces sombres:
  - Fond principal: #14181A
  - Surfaces/cartes: #1C2226
  - Fond tertiaire: #242A30
  - Bordures: #2A3238
  - Bordures légères: #3C4450

Texte:
  - Texte principal: #ECEFEE
  - Texte secondaire: #98A2A0
  - Texte tertiaire: #6B7580

Sémantique financière:
  - Hausse/positif: #22C55E
  - Baisse/négatif: #F05252
  - Neutre: #94A3B8
  - Alerte: #FBBF24
  - Info: #3B82F6

## TYPOGRAPHIE
Police principale: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif
Police monospace: 'IBM Plex Mono', 'Courier New', monospace

Hiérarchie:
  - Titres (h1): 3rem, weight 700, color #2FAE72, text-shadow léger
  - Sous-titres (h2): 1.5rem, weight 600, color #ECEFEE
  - Texte corps: 0.95-1.125rem, weight 400, color #ECEFEE
  - Labels: 0.7rem, weight 700, MAJUSCULES, letter-spacing 0.08em, color #6B7580
  - Monospace: 0.875rem, weight 500, pour codes/nombres

## ANIMATIONS & TRANSITIONS
  - Rapide: 140ms cubic-bezier(0.4, 0, 0.2, 1)
  - Standard: 220ms cubic-bezier(0.4, 0, 0.2, 1)
  - Lent: 320ms cubic-bezier(0.4, 0, 0.2, 1)

Hover effects:
  - Cartes: augmenter ombre, changer bg à #242A30
  - Badges: translateY(-2px)
  - Boutons: changer couleur vers #34D399 (vert clair)

## COMPOSANTS CLÉS

### Cards (.card)
- bg: #1C2226
- border: 1px solid #2A3238
- border-radius: 10px
- padding: 1.75rem
- shadow: 0 2px 8px rgba(0,0,0,0.3), 0 1px 0 rgba(47,174,114,0.05)
- hover: bg #242A30, shadow plus prononcée, border #3C4450

### Navigation (nav)
- bg: rgba(28, 34, 38, 0.8) avec backdrop-filter: blur(10px)
- position: sticky, top 0
- border-bottom: 1px solid #2A3238
- Boutons: underline vert au survol/actif

### Badges
- padding: 6px 14px
- border-radius: 20px
- border: 1px solid (couleur sémantique avec opacité)
- bg: linear-gradient avec opacité faible

Variantes:
  - badge-up: border #22C55E, text #22C55E, bg rgba(34,197,94,0.1)
  - badge-down: border #F05252, text #F05252, bg rgba(240,82,82,0.1)
  - badge-neutral: border #94A3B8, text #94A3B8
  - badge-brand: border #2FAE72, text #2FAE72

### Layout
- Container max-width: 1440px, padding: 3rem 2rem
- Grilles: grid-template-columns repeat(auto-fit, minmax(280px, 1fr)) ou repeat(4, 1fr)
- Gap: 1.75rem
- Responsive: 2 cols sur tablet, 1 col sur mobile

## AFFICHAGE SPÉCIFIQUE

### Page Watchlist
- Titre principal + description
- Cartes en grille 4 colonnes (strict)
- Par carte: TICKER | COURS + VAR% | RSI + TENDANCE | SIGNAL (badge)
- Variation positive en #22C55E, négative en #F05252

### Page Briefing
- 4 KPI cartes: S&P 500, NASDAQ, VIX, USD/EUR
- Recommandations: ticker + justification + badge signal + potentiel %

### Page Détail
- Dropdown pour sélectionner instrument
- Section Signaux: RSI, SMA 200, SMA 50, cours
- Section Fondamentaux: P/E, marge, ROE, objectif 12M

### Page News
- Cartes news: titre + source + timestamp + impact badge + tags

## RÈGLES DE STYLE GLOBALES
1. NO EMOJI - minimalisme absolu
2. Fond: dégradé 135deg #14181A → #0f1214 (background-attachment: fixed)
3. Transitions fluides sur ALL (200ms)
4. Bordures: 1px seulement, couleur #2A3238
5. Ombres: douces, teintées vert (opacité faible)
6. Espaces: multiples de 0.5rem (0.5, 1, 1.5, 2, 3rem)
7. Border-radius: 8-10px pour cards, 20px pour pills

## FICHIERS ATTENDUS
- portfolio-app.html (fichier unique, fonctionnel offline)
OU
- Python Streamlit app.py + design-system/tokens.css

Commencer par le HTML, ça c'est sûr.
