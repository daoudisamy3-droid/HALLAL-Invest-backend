# 📊 FinTerminal — Backend (V1 livré)

API du **FinTerminal**, un terminal personnel d'investissement halal
long terme. Le backend sert le screening Shariah, l'analyse
qualité/valuation et le suivi de portefeuille, consommés par le
frontend React du même projet.

## Quoi ?

Un seul utilisateur (le mainteneur), des décisions hebdo de type
"acheter / garder / vendre" sur ~10 positions long terme, avec un
filtre éthique strict (AAOIFI custom thresholds) en première porte
puis une cascade qualité → valorisation. Pas de trading intraday,
pas de signaux quantitatifs court terme.

## Stack

- **FastAPI 0.115** + Python 3.11, déployé Railway
- **PostgreSQL 16** + asyncpg + SQLAlchemy 2.0 async + Alembic
- **Sources externes** : Halal Terminal (Shariah), SEC EDGAR (XBRL),
  YFinance (live + historique + analyst targets)
- **Decimal-everywhere** pour les flux monétaires (jamais de `float`)
- **Tenacity** retry exponentiel + caches Postgres TTL'd (pas de Redis V1)

## Périmètre V1

Couvert dans `docs/V1_RELEASE_NOTES.md` :
- 9 endpoints REST sous `/api/v1`
- 5 tables domaine §11.2 + 2 tables cache technique
- 240 tests pytest
- Cascade halal stricte (Halal FAIL → BLOCKED, hard stop)

## Architecture

Spec fonctionnelle et mapping technique conservés dans :
- `docs/finterminal-spec.md` v2.1.1 (3147 lignes, source de vérité)
- `docs/master-prompt.md` v1.0 (mapping technique source de vérité)

Ne pas modifier ces deux fichiers — ils figent le contrat.

## Limitations V1 → V2

Chaque déviation / scaffold V2 a sa note dédiée :

- `docs/SHARIAH_FREE_TIER_DEVIATION.md` — agrégat free-tier consommé tel quel
- `docs/INVESTISSABLE_NON_US_LIMITATION.md` — non-US = INCERTAIN
- `docs/SCORE_PARTIAL_COMPONENTS.md` — Smart Money + Earnings Stability
- `docs/VALUATION_PARTIAL_METHODS.md` — M2 secteur scaffold + M1 P/E only
- `docs/YFINANCE_INTEGRATION_NOTES.md` — Yahoo lib caveats + fallback playbook

## Tests

```bash
pytest                       # 240 tests (15 deselected = mark live)
pytest -m unit               # tests rapides sans DB
pytest -m integration        # tests avec Postgres function-scoped
```

## Statut

✅ **Livré V1 — 14 mai 2026.** Voir `docs/V1_RELEASE_NOTES.md` pour le
récapitulatif des 11 étapes + roadmap V2.
