# FinTerminal V1 — Release Notes (livré 14 mai 2026)

Terminal halal d'investissement long terme pour usage personnel. V1
finalise l'arc complet **screen Shariah → qualité fondamentale →
valuation multi-méthodes → suivi portfolio**, accessible via un
dashboard React.

## Périmètre fonctionnel V1

### Backend — 9 endpoints REST sous `/api/v1`

| Endpoint                              | Source de données       | Spec   |
|---------------------------------------|-------------------------|--------|
| `GET /shariah/{symbol}`               | Halal Terminal (free tier) | §5    |
| `GET /financials/{ticker}`            | SEC EDGAR XBRL          | §3.2.2 |
| `GET /investissable/{symbol}`         | SEC + Halal Terminal    | §4.2   |
| `GET /valuation/{symbol}`             | SEC + YFinance          | §4.3   |
| `GET /synthesis/{symbol}`             | agrégation 3 couches    | §4.4   |
| `GET /portfolio/positions`            | DB locale + YFinance live | §11.2 |
| `GET /portfolio/summary`              | DB locale + YFinance live | §11.2 |
| `GET /portfolio/transactions`         | DB locale               | §11.2  |
| `POST /portfolio/transactions`        | DB locale (USD only V1) | §11.2  |
| `DELETE /portfolio/transactions/{id}` | DB locale               | §11.2  |

→ 240 tests pytest, anti-régression à chaque commit.

### Frontend — 11 onglets analyse + page Portfolio

| Onglet         | Statut V1     | Source                                          |
|----------------|---------------|-------------------------------------------------|
| GÉNÉRALE       | 🟢 LIVE        | `/api/v1/financials/{ticker}`                   |
| AAOIFI         | 🟢 LIVE        | `/api/v1/shariah/{symbol}`                      |
| INVESTISSABLE  | 🟢 LIVE        | `/api/v1/investissable/{symbol}`                |
| VALORISÉE      | 🟢 LIVE        | `/api/v1/valuation/{symbol}`                    |
| SCORE          | 🟢 LIVE (alias) | `/api/v1/investissable/{symbol}` (reuse panel)  |
| PLAN           | 🟢 LIVE        | `/api/v1/synthesis/{symbol}`                    |
| RISQUE         | 🟡 V2 scaffold | placeholder ComingSoonTab                       |
| CALENDRIER     | 🟡 V2 scaffold | placeholder ComingSoonTab                       |
| MGMT           | 🟡 V2 scaffold | placeholder ComingSoonTab                       |
| RELS           | 🟡 V2 scaffold | placeholder ComingSoonTab                       |
| HOLDERS        | 🟡 V2 scaffold | placeholder ComingSoonTab                       |

Plus un **SynthesisBanner** transverse au-dessus des onglets : 3 badges
(HALAL · INVESTISSABLE · VALORISÉE) + verdict global cascade. La page
**Portfolio** est accessible via la sidebar (nav réduite à 2 items :
ANALYSE + PORTFOLIO).

→ 39 tests vitest + `tsc --noEmit` strict.

### Cascade halal stricte — éthique avant tout

Le verdict global `synthesis.overall_verdict` cascade les 3 couches :
1. **Halal FAIL** → `BLOCKED` (hard stop, jamais d'override)
2. Halal `ERROR` / `NOT_COVERED` → `REQUIRES_REVIEW` + warning
3. Investissable `NON` → `NOT_INVESTABLE`
4. Investissable `INCERTAIN` ou Valuation `NON`/`INDÉTERMINÉ` → `REQUIRES_REVIEW`
5. Sinon → `INVESTABLE` (composite label "INVESTISSABLE — QUALITÉ X — Y")

## Étapes 0 → 7

| Étape | Livrable                                                          | Commit principal |
|-------|-------------------------------------------------------------------|------------------|
| 0     | Skeleton FastAPI + Alembic 5 tables §11.2 + auth fail-fast       | `c3...`           |
| 1     | Halal Terminal AAOIFI gate (free-tier aggregate mode)             | `9b...`           |
| 1.5   | Frontend ShariahPanel + stubs `/ticker`                            | —                |
| 2     | SEC EDGAR XBRL + `financials_cache` (TTL 24h)                     | `9ce4bcb`         |
| 3     | Portfolio service Decimal-exact (P&L weighted-avg USD only)       | `7807b18`         |
| 4     | INVESTISSABLE 3 gates + 5 composantes qualité (Altman/Piotroski/…) | `7d746bb`         |
| 4.5   | Valuation Graham + 3 scaffolds                                     | `64b6d1e`         |
| 5     | YFinance integration → portfolio live + valuation M1 + M4         | `6ef4465`         |
| 6     | Couche synthèse + SynthesisBanner (asyncio.gather × 3 services)   | `d54c384`         |
| 7.1   | InvestissablePanel + ValuationPanel + hotfix per-branch session   | `c5685ea` + `c71b3fb` |
| 7.2   | GeneraleTab + ScoreTab + PlanTab branchés + 5 ComingSoonTab       | `a168ef6`         |
| 7.3   | Portfolio refactor + chart placeholder + bug fiscal year aggr.     | `72d3528` + `08ffe33` |

## Déviations V1 documentées

Chaque écart par rapport à la spec a sa note dédiée :

- **`docs/SHARIAH_FREE_TIER_DEVIATION.md`** — Halal Terminal free tier
  retourne un verdict agrégé, pas les 4 ratios bloquants détaillés
  §5.1/§5.2. Décision : consommer l'agrégat tel quel, expliciter
  l'absence de calcul personnel custom-thresholds.
- **`docs/INVESTISSABLE_NON_US_LIMITATION.md`** — SEC EDGAR couvre US
  uniquement → tickers non-US (AIXA.DE etc.) tombent toujours en
  `INCERTAIN`. Documenté frontend par le badge amber + warning.
- **`docs/SCORE_PARTIAL_COMPONENTS.md`** — 2 composantes qualité sur 5
  non encore branchées (Smart Money 13F/Form 4 reporté V2 ; Earnings
  Stability nécessite données trimestrielles non extraites V1).
- **`docs/VALUATION_PARTIAL_METHODS.md`** — Méthode 2 (sector
  multiples) reste scaffold, Méthode 1 limitée au P/E (P/S +
  EV/EBITDA différés). M3 + M4 + M1-P/E sont LIVE.
- **`docs/YFINANCE_INTEGRATION_NOTES.md`** — Yahoo lib synchrone +
  rate-limit IP fréquent. Stratégie : `asyncio.to_thread` + retry 3× +
  fail-graceful (jamais 5xx). Caches 1h (info) + 24h (history).
- **Portfolio USD-only V1** — multi-currency reporté V2. Signalé dans
  `app/schemas/portfolio.py`, l'exception `UnsupportedCurrencyError`,
  le label UI "USD only — V1", et le form qui hardcode `currency=USD`.
- **financials_cache + yfinance_cache hors §11.2** — tables techniques
  de caching, pas du domaine. Rationale verbose en tête des migrations
  alembic 005 et 006.

## Roadmap V2 (priorités probables, à valider après usage user)

| Feature                                              | Effort estimé | Bloqueur principal |
|------------------------------------------------------|---------------|--------------------|
| Valuation Méthode 2 (FMP sector multiples)           | ~6-8h         | clé API FMP        |
| Extension M1 (P/S + EV/EBITDA historiques)            | ~7h           | SEC quarterly      |
| Smart Money composante SCORE (Finnhub insiders/13F)  | ~6h           | clé API Finnhub    |
| Earnings Stability composante SCORE (SEC quarterly)  | ~4h           | aucun              |
| Multi-currency portfolio (FX rates daily)            | ~6h           | source FX (ECB ?)   |
| `simulate_position_add` (HHI + correlation matrix)   | ~5h           | aucun              |
| 5 onglets RISQUE/CALENDRIER/MGMT/RELS/HOLDERS        | ~3h chacun    | sources externes   |
| Chart de prix réel (historical YFinance bars)        | ~4h           | aucun              |
| Macro Dashboard + Map réactivation                   | ~1-2 sem.     | source macro       |

## Stack technique

**Backend** : FastAPI 0.115 + Python 3.11 + PostgreSQL 16 + asyncpg +
SQLAlchemy 2.0 async + Alembic, déployé Railway. Decimal-everywhere
pour les flux monétaires (jamais de `float`). Tenacity pour retry
exponentiel sur tous les clients externes. 7 migrations alembic, 5
tables domaine §11.2 + 2 tables cache.

**Frontend** : React 18 + TypeScript strict (`exactOptionalPropertyTypes`)
+ Vite 7 + Tailwind + recharts (legacy, à élaguer en V2). Types
OpenAPI auto-générés via `openapi-typescript` (`npm run types:generate`
pull depuis la branche backend). Pas de `any`, pas de `@ts-ignore`.

**Sources externes intégrées V1** : Halal Terminal (Shariah verdict),
SEC EDGAR (XBRL company facts + submissions), YFinance (live quotes +
analyst targets + monthly bars 5y).

## Statut

✅ **Livré V1 — 14 mai 2026**

11 étapes complètes (0 → 7), 279 tests automatisés (240 backend + 39
frontend), 7 docs de déviations / limitations + 2 docs frontend
(tech-debt + V2 deferred). Le terminal est utilisable au quotidien
pour le screening halal + qualité + valorisation + suivi de
portefeuille personnel USD.

Prochain palier : usage réel + collecte de feedback pour prioriser la
roadmap V2 ci-dessus.
