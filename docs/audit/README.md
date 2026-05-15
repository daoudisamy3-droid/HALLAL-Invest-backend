# Dossier d'audit — Architecture Analyse FinTerminal V1

> Périmètre : tout le pipeline d'analyse d'un ticker, du clic utilisateur
> au verdict rendu à l'écran. État du code au **14 mai 2026** (post-Étape 8,
> commits `dff8e71` backend + `28539cb` frontend).

## Index des documents

| Fichier | Sujet |
|---------|-------|
| [`00-OVERVIEW.md`](./00-OVERVIEW.md) | Vue d'ensemble + diagramme bout-en-bout + principes d'architecture |
| [`01-DATA-SOURCES.md`](./01-DATA-SOURCES.md) | Sources externes : SEC EDGAR, YFinance, Halal Terminal |
| [`02-BACKEND-LAYERS.md`](./02-BACKEND-LAYERS.md) | Découpage clients HTTP / services / endpoints / schémas Pydantic |
| [`03-ENDPOINTS.md`](./03-ENDPOINTS.md) | Inventaire exhaustif des 12 endpoints REST sous `/api/v1/` |
| [`04-CALCULATIONS.md`](./04-CALCULATIONS.md) | Formules complètes Altman, Piotroski, Growth, Fraud, Capital, Valuation |
| [`05-DATABASE.md`](./05-DATABASE.md) | Schéma PostgreSQL : 5 tables domaine + 2 tables cache + 7 migrations |
| [`06-FRONTEND-COMPONENTS.md`](./06-FRONTEND-COMPONENTS.md) | Composants React : SynthesisBanner + 9 onglets + ExpandableCalc |
| [`07-DATA-FLOWS.md`](./07-DATA-FLOWS.md) | Séquences détaillées pour les 5 use-cases principaux |
| [`08-CACHE-STRATEGY.md`](./08-CACHE-STRATEGY.md) | Stratégie de caching : 3 tables, TTLs, invalidation |
| [`09-ERROR-HANDLING.md`](./09-ERROR-HANDLING.md) | Hiérarchie d'exceptions + fail-graceful contracts |
| [`10-DEVIATIONS-LIMITATIONS.md`](./10-DEVIATIONS-LIMITATIONS.md) | Toutes les déviations vs spec + features reportées V2 |

## Conventions

- **Backend** : Python 3.11 + FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic. `Decimal` everywhere pour la finance.
- **Frontend** : React 18 + TypeScript strict + Vite + Tailwind. Types OpenAPI auto-générés depuis le backend.
- **Auth** : `X-Auth-Token` UUID, vérifié par `verify_api_key` au niveau du router `/api/v1/`. `/health` + `/openapi.json` exclus.
- **Cascade halal** : Halal FAIL court-circuite tout (verdict global `BLOCKED`). Aucun override possible.

## Statistiques rapides au moment de l'audit

- **257 tests pytest** + **51 tests vitest** = 308 tests automatisés
- **12 endpoints REST** publics
- **5 sources de données** (3 actives, 2 cache techniques)
- **7 migrations alembic**
- **11 calculs auditables** par ticker (avec accordéon formule + variables + étapes)
- **0 € de coût mensuel** en V1 (sources free-tier)
