# 00 — Vue d'ensemble de l'architecture Analyse

## 1. Stack technologique

### Backend (`HALLAL-Invest-backend`)

| Couche             | Technologie                            |
|--------------------|----------------------------------------|
| Runtime            | Python 3.11                            |
| Framework HTTP     | FastAPI 0.115                          |
| Validation         | Pydantic 2.11                          |
| ORM async          | SQLAlchemy 2.0 + asyncpg               |
| Migrations         | Alembic 1.15                           |
| Tests              | pytest 9 + pytest-asyncio              |
| Retry / fallback   | tenacity 9                              |
| HTTP client async  | httpx 0.28                              |
| Scraping Yahoo     | yfinance ≥ 0.2.40 (lib, via `asyncio.to_thread`) |
| Hébergement        | Railway (Procfile = `alembic upgrade head && uvicorn`) |

### Frontend (`Hallal-Invest-Frontend`)

| Couche             | Technologie                            |
|--------------------|----------------------------------------|
| Framework          | React 18                                |
| Typage             | TypeScript strict (`exactOptionalPropertyTypes`) |
| Build              | Vite 7                                  |
| Style              | Tailwind 4 + dark theme + glass-card    |
| Tests              | vitest 4 + @testing-library/react + jsdom |
| Types backend      | `openapi-typescript` (régénération via `npm run types:generate`) |
| Hébergement        | Railway                                 |

## 2. Diagramme bout-en-bout (analyse d'un ticker)

```
                              ┌─────────────────────────────┐
                              │   USER (browser)            │
                              │   tape "AAPL" + Analyser    │
                              └──────────────┬──────────────┘
                                              │
                                              ▼
                        ┌─────────────────────────────────────┐
                        │ FRONTEND React (Vite, Railway)       │
                        │                                       │
                        │  AnalysisDashboard.tsx (page)         │
                        │     ├─ TradingChart (placeholder)     │
                        │     ├─ SynthesisBanner (en haut)      │
                        │     └─ Onglets : 11 panels            │
                        │         GENERALE       AAOIFI         │
                        │         INVESTISSABLE  VALORISÉE      │
                        │         CALENDRIER     MGMT           │
                        │         HOLDERS         RELS           │
                        │         SCORE          PLAN            │
                        │         RISQUE                         │
                        └──────────────┬──────────────────────────┘
                                       │ apiFetch (X-Auth-Token UUID)
                                       │
                                       ▼
                  ┌──────────────────────────────────────────┐
                  │ BACKEND FastAPI (Railway)                  │
                  │  /api/v1/* router (verify_api_key dep)    │
                  │                                            │
                  │  Endpoints (12) :                           │
                  │    /shariah/{s}         /synthesis/{s}     │
                  │    /financials/{s}      /portfolio/*       │
                  │    /investissable/{s}   /calendar/{s}      │
                  │    /valuation/{s}       /management/{s}    │
                  │    /holders/{s}                            │
                  │                                            │
                  │  Services (composent les calculs) :         │
                  │    shariah_service  financials_service     │
                  │    investissable_service                   │
                  │    valuation_service synthesis_service     │
                  │    portfolio_service yfinance_service      │
                  │                                            │
                  │  Score modules (transparence) :             │
                  │    altman  piotroski  growth  fraud        │
                  │    capital_allocation  sector              │
                  └──────────────┬──────────────┬──────────────┘
                                  │              │
              ┌──────────────────┼──────────────┼──────────────────┐
              │                  │              │                    │
              ▼                  ▼              ▼                    ▼
   ┌──────────────────┐ ┌─────────────────┐ ┌────────────────┐ ┌─────────────┐
   │  SEC EDGAR        │ │   YFinance      │ │ Halal Terminal │ │ PostgreSQL │
   │  data.sec.gov     │ │   (Yahoo, lib)  │ │  api.halal     │ │  Railway   │
   │  UA header        │ │   scraping      │ │  X-API-Key     │ │  asyncpg   │
   │                   │ │                 │ │                │ │            │
   │  • company_facts │ │ • info          │ │ • aggregate    │ │ 7 tables   │
   │  • submissions    │ │ • history       │ │   verdict      │ │ 7 mig.     │
   │  • ticker map     │ │ • calendar      │ │                │ │            │
   │                   │ │ • info.officers │ │                │ │            │
   │  US-GAAP +        │ │ • holders       │ │                │ │            │
   │  IFRS-full        │ │                 │ │                │ │            │
   └──────────────────┘ └─────────────────┘ └────────────────┘ └─────────────┘
                              │
                              ▼
                     ┌─────────────────────┐
                     │  caches Postgres    │
                     │  financials_cache   │
                     │   (TTL 24h)         │
                     │  yfinance_cache     │
                     │   (TTL 1h/6h/24h)   │
                     │  screen_history     │
                     │   (TTL 7j, §11.2)   │
                     └─────────────────────┘
```

## 3. Principes architecturaux

### P1 — Cascade halal stricte (éthique avant tout)

Le verdict global du `SynthesisBanner` (`overall_verdict`) suit une cascade non-overridable :

```
halal == FAIL                    → BLOCKED          (hard stop, jamais d'override)
halal in {ERROR, NOT_COVERED}    → REQUIRES_REVIEW  + warning explicite
halal == PASS && investissable == NON         → NOT_INVESTABLE
halal == PASS && investissable == INCERTAIN   → REQUIRES_REVIEW
halal == PASS && valuation in {NON, INDÉTERMINÉ}  → REQUIRES_REVIEW
sinon                                          → INVESTABLE
```

### P2 — Decimal everywhere (P&L et finance)

Aucun `float` dans la chaîne financière. `Decimal` Python côté backend, strings préservées côté API → frontend via `Intl.NumberFormat` (jamais d'arithmétique JavaScript sur les montants).

### P3 — Transparence des calculs (Étape 8 Phase B)

Chaque verdict expose un `calculation_detail` auditable :
- `formula` : la formule littérale appliquée
- `variables` : valeurs substituées (strings Decimal-fidèles)
- `intermediates` : ratios calculés mid-flight
- `computation_steps` : lignes de calcul à la main
- `result` : scalaire final
- `thresholds` : grille de seuils
- `interpretation` : conclusion une-phrase

Rendu côté frontend par `<ExpandableCalc />` en accordéon collapsable.

### P4 — Fail-graceful sur sources externes (Étape 5 + Étape 8)

Aucun endpoint ne propage les pannes externes en 5xx :
- SEC EDGAR down → `verdict: "ERROR"` dans le body, status 200
- YFinance rate-limit → `available: false` + raison, status 200
- Halal Terminal HS → `verdict: "ERROR"` (pas caché)

Le frontend rend l'état dégradé (bandeau ambre, warnings inline), jamais d'écran blanc.

### P5 — Per-branch DB session en parallèle (hotfix Étape 7.1A)

`synthesis_service.compute_synthesis` lance 3 services en `asyncio.gather`. Chacun reçoit **sa propre `AsyncSession`** via `session_factory` (sinon `InterfaceError: another operation in progress` sur asyncpg).

### P6 — Couverture universelle (Étape 8 Phase A)

`_extract_latest_annual` + `extract_n_year_annuals` parcourent à la fois `us-gaap` et `ifrs-full`. `_is_annual_entry` accepte `fp == "FY"` OU formes `{10-K, 20-F, 40-F, …}` avec durée ~12 mois. Les foreign filers (Toyota, BABA, Novo Nordisk…) sont couverts.

### P7 — Source de vérité documentaire figée

- `docs/finterminal-spec.md` v2.1.1 (3147 lignes) — spec métier figée
- `docs/master-prompt.md` v1.0 (2278 lignes) — mapping technique figé

Toute déviation est documentée dans `docs/*_LIMITATION.md` ou `docs/*_DEVIATION.md`.

## 4. Périmètre fonctionnel V1

### 12 endpoints REST sous `/api/v1`

Voir détail dans [`03-ENDPOINTS.md`](./03-ENDPOINTS.md).

### 11 onglets frontend

| # | Onglet         | Source backend                       | Statut V1 |
|---|----------------|--------------------------------------|-----------|
| 1 | GÉNÉRALE       | `/financials/{ticker}`               | 🟢 LIVE   |
| 2 | AAOIFI         | `/shariah/{symbol}`                  | 🟢 LIVE   |
| 3 | INVESTISSABLE  | `/investissable/{symbol}`            | 🟢 LIVE   |
| 4 | VALORISÉE      | `/valuation/{symbol}`                | 🟢 LIVE   |
| 5 | CALENDRIER     | `/calendar/{symbol}`                 | 🟢 LIVE (Étape 8 Phase C) |
| 6 | MGMT           | `/management/{symbol}`               | 🟢 LIVE (Étape 8 Phase C) |
| 7 | RELS           | —                                    | 🟡 V2 (ComingSoonTab) |
| 8 | HOLDERS        | `/holders/{symbol}`                  | 🟢 LIVE (Étape 8 Phase C) |
| 9 | SCORE          | `/investissable/{symbol}` (alias)    | 🟢 LIVE   |
| 10 | PLAN          | `/synthesis/{symbol}`                | 🟢 LIVE   |
| 11 | RISQUE        | —                                    | 🟡 V2 (ComingSoonTab) |

Plus le **SynthesisBanner** transverse en haut de page (cascade 3 couches via `/synthesis/{symbol}`).

## 5. Métriques de qualité

- **257 tests pytest** : unit + integration (DB function-scoped). Couverture des formules, des extracteurs, des extracteurs de tags, des endpoints, des cascades synthesis.
- **51 tests vitest** : composants idle/loading/success/error pour les 9 panels + ExpandableCalc.
- **tsc strict** : `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess` actif. Zéro `any`, zéro `@ts-ignore`.
- **Lint** : `npm run lint` = `tsc --noEmit` (pas d'eslint en V1 — opt-in).
- **CI** : GitHub Actions sur les 2 repos (déclenché par push, env vars hoistées au job-level).
