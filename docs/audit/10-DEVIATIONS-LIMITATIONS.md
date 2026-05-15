# 10 — Déviations vs spec + Limitations V1 → V2

Toutes les déviations sont documentées dans des fichiers `docs/*` dédiés
et liées depuis ce document audit.

## 1. Déviations actives (V1 vs spec)

### D1 — Shariah free-tier mode

- **Doc** : `docs/SHARIAH_FREE_TIER_DEVIATION.md`
- **Section spec impactée** : §5.1 (4 ratios bloquants AAOIFI custom thresholds) + §5.2 (algorithme verdict)
- **Déviation** : Halal Terminal free-tier ne retourne pas les 4 ratios bruts → on consomme le **verdict agrégé** (`PASS/FAIL/ERROR/NOT_COVERED`) tel quel. Les `raw_ratios` du schema `ShariahReport` sont `None` en V1.
- **Conséquence UX** : ShariahPanel n'affiche pas les 4 checks détaillés (debt/MC, cash/MC, impure_revenue, interest_income) — seulement le verdict global + raison.
- **Plan V2** : passage au plan payant Halal Terminal qui exposera les ratios bruts, ou intégration provider alternatif (Musaffa) → activer le calcul personnel des 4 checks avec les seuils SHARIAH_*_MAX déjà gravés dans `config.py`.

### D2 — Investissable Non-US = INCERTAIN

- **Doc** : `docs/INVESTISSABLE_NON_US_LIMITATION.md`
- **Cause** : SEC EDGAR couvrait historiquement les US-only. Depuis Étape 8 Phase A, la couverture 20-F / 40-F a été ajoutée mais reste partielle (certains foreign filers n'ont pas tous les concepts requis pour Altman + Piotroski).
- **Comportement V1** : si `get_facts_payload` retourne `None` (ticker absent du SEC ticker map) → `verdict: "INCERTAIN"` + warning explicite.
- **Plan V2** : intégrer une source complémentaire (FMP ou EOD Historical Data) pour combler les concepts manquants chez les foreign filers.

### D3 — SCORE composantes partielles (Smart Money + Earnings Stability)

- **Doc** : `docs/SCORE_PARTIAL_COMPONENTS.md`
- **Cause** :
  - **Smart Money** (20 % poids) : nécessite données 13F institutionnelles + Form 4 insider transactions. Step 5.5 reportée à V2.
  - **Earnings Stability** (10 % poids) : nécessite consensus analystes Finnhub ou estimations historiques. Reportée V2.
- **Comportement V1** : ces 2 composantes retournent `available: false` avec `reason: "Not computable in V1 — see docs/SCORE_PARTIAL_COMPONENTS.md"`. Le `quality_score` est rebalancé sur les 3 composantes restantes (Piotroski 30 % + Growth 25 % + Capital 15 %, normalisé à 70 % → 30/70 + 25/70 + 15/70).
- **Plan V2** : intégrer Finnhub (insider + estimates) pour activer Smart Money et Earnings Stability simultanément.

### D4 — Valuation Méthode 2 (sector) + extensions Méthode 1

- **Doc** : `docs/VALUATION_PARTIAL_METHODS.md` (mis à jour post-Step 5)
- **Limitation 1 — M2 sector medians** : nécessite intégration FMP `/stock_peers/{symbol}` (V2). Le service `_method_vs_sector(facts)` retourne `available: false` avec raison "V2 FMP".
- **Limitation 2 — M1 P/E only** : V1 n'utilise que P/E historique. P/S et EV/EBITDA différés (nécessitent shares-outstanding trimestriel + EBITDA historique). Plug-in points documentés.
- **Comportement V1 nominal** : 3 méthodes sur 4 disponibles (M1 P/E + M3 Graham + M4 Analyst Target) → `n_methods=3`, confidence MEDIUM, verdict réel calculé.

### D5 — Z-Score uniforme Z'' (pas Z classique)

- **Module** : `app/services/altman.py`
- **Cause** : Z classique (5 facteurs, inclut MarketCap/TL) requiert prix live au moment du calcul. Step 4 a appliqué Z'' (4 facteurs) uniformément.
- **Note inline** : "Spec explicitement documente Z'' pour entreprises non-manufacturières. On l'applique uniformément V1, divergence < 5 % pour non-financières."
- **Plan V2** : éventuellement réintroduire le Z classique pour US manufacturers maintenant que le prix YFinance est disponible (Step 5+).

### D6 — EBIT proxy via OperatingIncomeLoss

- **Module** : `app/services/altman.py`
- **Cause** : SEC GAAP n'expose pas un tag EBIT direct. `OperatingIncomeLoss` est la meilleure approximation.
- **Conséquence** : divergence typique < 5 % pour non-financials, plus pour financials.
- **Plan V2** : composition EBIT = NetIncome + InterestExpense + IncomeTaxExpense pour précision exacte.

### D7 — financials_cache + yfinance_cache hors §11.2

- **Tables** : `financials_cache` (alembic 005), `yfinance_cache` (alembic 006)
- **Cause** : Spec §11.2 liste 5 tables domaine. Pas de Redis V1 → caching Postgres-backed nécessaire → 2 tables techniques supplémentaires.
- **Justification documentée** : tête des migrations 005 et 006 expliquent que ce sont des "extensions d'infrastructure, pas du domaine".
- **Plan V2** : éventuellement remplacer par Redis si nécessaire (perf). Tables resteraient archivables via migration descendante.

### D8 — Portfolio USD-only V1

- **Inline doc** : `app/schemas/portfolio.py` docstring + `UnsupportedCurrencyError`
- **Cause** : Multi-currency nécessite source FX daily (ECB ?). Reportée V2.
- **Comportement V1** : `POST /portfolio/transactions` avec `currency != "USD"` → 400 + détail explicite. Form UI hardcode `USD`. Label "USD only — V1" affiché dans le header de la page Portfolio.
- **Plan V2** : intégration FX rates + colonne `position.fx_rate_to_usd` pour conversion à l'affichage.

### D9 — TradingChart placeholder

- **Module** : `src/components/TradingChart.tsx`
- **Cause** : `/api/v1/ticker/{s}/price` + `/.../ohlcv` endpoints du legacy n'ont jamais été reimplementés (V1 scope). Le composant restait bloqué en "Chargement…" sur ~50 % de l'écran.
- **Comportement Étape 7.3A** : remplacé par un strip compact ~52 px "📊 Graphique de prix — {symbol} · Bientôt disponible — V2".
- **Plan V2** : nouvel endpoint backend `GET /api/v1/ticker/{symbol}/history?period=1Y` via YFinance + composant Recharts.

### D10 — Bug fiscal year tag-aggregation (corrigé Étape 7.3D)

- **Bug** : `_extract_latest_annual` short-circuitait sur le premier tag GAAP yielding any entry. Pour Apple (legacy `Revenues` stoppé en FY2018, modern `RevenueFromContractWithCustomerExcludingAssessedTax` continu), retournait `fiscal_year=2018` au lieu de 2024.
- **Fix** : agrégation des FY entries cross-tags, sort par `end` desc, pick latest. Même fix appliqué à `extract_n_year_annuals` (utilisé par tous les modules Step 4).
- **Régression** : 4 tests dédiés (`test_extract_latest_annual_aggregates_across_tags`, ...).

## 2. Features reportées V2 (hors scope V1)

| Feature                                              | Effort estimé | Bloqueur principal | Doc référence |
|------------------------------------------------------|---------------|----------------------|---------------|
| Valuation Méthode 2 (sector medians)                 | ~6-8h         | clé API FMP            | `docs/VALUATION_PARTIAL_METHODS.md` |
| Extension M1 P/S + EV/EBITDA                         | ~7h           | SEC quarterly extraction | idem |
| Smart Money composante SCORE                          | ~6h           | clé API Finnhub        | `docs/SCORE_PARTIAL_COMPONENTS.md` |
| Earnings Stability composante SCORE                  | ~4h           | aucun (SEC quarterly)  | idem |
| Multi-currency portfolio                              | ~6h           | source FX daily         | inline |
| `simulate_position_add` (HHI + correlation matrix)   | ~5h           | aucun                  | inline |
| Onglet RISQUE (Altman détaillé + stress tests)        | ~3h           | aucun                  | placeholder ComingSoonTab |
| Onglet RELS (corporate relationships + LEI)          | ~6h           | GLEIF API §3.2.8       | placeholder ComingSoonTab |
| Chart de prix réel (historical bars)                  | ~4h           | aucun                  | `docs/TECH_DEBT_FRONTEND_DEAD_ENDPOINTS.md` |
| Macro Dashboard + Map réactivation                    | ~1-2 semaines | source macro            | `docs/V2_FEATURES_DEFERRED.md` |

## 3. Onglets V1 status (synthèse)

| # | Onglet            | Statut V1     | Endpoint backend                   |
|---|--------------------|---------------|------------------------------------|
| 1  | GÉNÉRALE          | 🟢 LIVE        | `/financials/{ticker}`             |
| 2  | AAOIFI             | 🟢 LIVE        | `/shariah/{symbol}`                 |
| 3  | INVESTISSABLE     | 🟢 LIVE        | `/investissable/{symbol}`          |
| 4  | VALORISÉE         | 🟢 LIVE        | `/valuation/{symbol}`              |
| 5  | CALENDRIER         | 🟢 LIVE (Étape 8 C) | `/calendar/{symbol}`              |
| 6  | MGMT               | 🟢 LIVE (Étape 8 C) | `/management/{symbol}`            |
| 7  | RELS               | 🟡 V2 scaffold | — (`ComingSoonTab`)                 |
| 8  | HOLDERS            | 🟢 LIVE (Étape 8 C) | `/holders/{symbol}`               |
| 9  | SCORE              | 🟢 LIVE (alias) | `/investissable/{symbol}`         |
| 10 | PLAN               | 🟢 LIVE        | `/synthesis/{symbol}`              |
| 11 | RISQUE             | 🟡 V2 scaffold | — (`ComingSoonTab`)                 |

→ **9 onglets LIVE sur 11**, 2 en placeholder V2.

## 4. Sécurité & RGPD

V1 est **mono-user** (mainteneur) sur Railway personnel. Pas de PII tierce, pas de compte utilisateur.

- Auth : UUID `X-Auth-Token` partagé entre frontend et backend
- Pas de cookies, pas de sessions
- Pas de logging des tokens / clés API
- Postgres Railway : encryption-at-rest géré par l'infra (managed service)
- HTTPS forcé sur tous les endpoints publics (Railway TLS termination)

V2 si multi-user envisagé : OAuth + per-user data isolation + audit log.

## 5. Synthèse documentation existante

Tous les fichiers de documentation sont dans `docs/` :

| Fichier                                   | Contenu |
|-------------------------------------------|---------|
| `finterminal-spec.md` v2.1.1               | Spec métier source de vérité (3147 lignes) — **figée** |
| `master-prompt.md` v1.0                    | Mapping technique source de vérité (2278 lignes) — **figée** |
| `V1_RELEASE_NOTES.md`                       | Récap 11 étapes + déviations + roadmap V2 |
| `SHARIAH_FREE_TIER_DEVIATION.md`            | Déviation D1 |
| `INVESTISSABLE_NON_US_LIMITATION.md`        | Déviation D2 |
| `SCORE_PARTIAL_COMPONENTS.md`                | Déviation D3 |
| `VALUATION_PARTIAL_METHODS.md`              | Déviation D4 (mis à jour post-Step 5) |
| `YFINANCE_INTEGRATION_NOTES.md`              | Caveats YFinance + fallback playbook |
| `audit/00-OVERVIEW.md` → `10-DEVIATIONS.md` | Ce dossier d'audit (10 fichiers) |

Et côté frontend :
- `docs/TECH_DEBT_FRONTEND_DEAD_ENDPOINTS.md` — endpoints morts purgés vs restants
- `docs/V2_FEATURES_DEFERRED.md` — GLOBAL NEWS + MACRO conservés en squelettes V2
