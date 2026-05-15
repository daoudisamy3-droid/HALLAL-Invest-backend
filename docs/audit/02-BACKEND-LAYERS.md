# 02 — Découpage couches backend

## 1. Vue couches

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 4 — API (FastAPI routers + Pydantic schemas)            │
│   app/api/v1/router.py          (router racine + auth dep)    │
│   app/api/v1/endpoints/*.py     (8 fichiers, 12 endpoints)    │
│   app/schemas/*.py              (10 fichiers Pydantic)        │
└──────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — Services (orchestration métier)                     │
│   app/services/shariah_service.py                              │
│   app/services/financials_service.py                           │
│   app/services/investissable_service.py                        │
│   app/services/valuation_service.py                            │
│   app/services/synthesis_service.py                            │
│   app/services/portfolio_service.py                            │
│   app/services/yfinance_service.py                             │
└──────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ Layer 2 — Calculs (formules + transparence)                   │
│   app/services/altman.py             (Z'' faillite)            │
│   app/services/piotroski.py          (F-Score 9 critères)      │
│   app/services/growth.py             (CAGR 3y)                 │
│   app/services/fraud.py              (3 signaux fraud)         │
│   app/services/capital_allocation.py (D1 buybacks + D3 invest) │
│   app/services/sector.py             (SIC classifier)          │
└──────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ Layer 1 — Clients HTTP (intégrations externes)                │
│   app/integration/sec_edgar_client.py                          │
│   app/integration/yfinance_client.py                           │
│   app/integration/halal_terminal_client.py                     │
└──────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ Layer 0 — Persistance + Config                                │
│   app/core/database.py    (engine + AsyncSessionLocal)        │
│   app/core/config.py       (Settings Pydantic fail-fast)      │
│   app/core/security.py    (verify_api_key)                    │
│   app/core/exceptions.py  (hiérarchie ExternalAPIError)       │
│   app/models/*.py         (7 SQLAlchemy models)               │
│   alembic/versions/*.py   (7 migrations)                      │
└──────────────────────────────────────────────────────────────┘
```

## 2. Détail couche 4 — API

### `app/api/v1/router.py`

```python
api_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(verify_api_key)],  # ← auth pour TOUTES les routes
)

api_router.get("/ping")                                    # smoke
api_router.include_router(shariah_endpoint.router)         # /shariah
api_router.include_router(financials_endpoint.router)      # /financials
api_router.include_router(portfolio_endpoint.router)       # /portfolio/*
api_router.include_router(investissable_endpoint.router)   # /investissable
api_router.include_router(valuation_endpoint.router)       # /valuation
api_router.include_router(synthesis_endpoint.router)       # /synthesis
api_router.include_router(yftabs_endpoint.calendar_router)   # /calendar
api_router.include_router(yftabs_endpoint.management_router) # /management
api_router.include_router(yftabs_endpoint.holders_router)    # /holders
```

`/health` et `/openapi.json` restent au niveau de `app/main.py`, **hors** du router authentifié.

### Fichiers endpoints

| Fichier                                  | Lignes | Endpoints exposés |
|------------------------------------------|--------|--------------------|
| `endpoints/shariah.py`                    | ~30    | `GET /shariah/{symbol}` |
| `endpoints/financials.py`                 | ~35    | `GET /financials/{ticker}` |
| `endpoints/investissable.py`              | ~40    | `GET /investissable/{symbol}` |
| `endpoints/valuation.py`                  | ~30    | `GET /valuation/{symbol}` |
| `endpoints/synthesis.py`                  | ~35    | `GET /synthesis/{symbol}` |
| `endpoints/portfolio.py`                  | ~85    | 5 routes : `POST/GET/DELETE /portfolio/transactions`, `GET /portfolio/positions`, `GET /portfolio/summary` |
| `endpoints/yfinance_tabs.py`              | ~135   | `GET /calendar/{s}`, `GET /management/{s}`, `GET /holders/{s}` |

### Schémas Pydantic (`app/schemas/`)

| Fichier            | Modèles exposés                                                |
|--------------------|----------------------------------------------------------------|
| `shariah.py`        | `ShariahReport`, `ShariahCheck`, `ShariahRatios`, `MethodologyVerdict` |
| `financials.py`    | `FinancialsSnapshot`                                            |
| `investissable.py` | `InvestissableReport`, `GateResult`, `QualityComponent`         |
| `valuation.py`     | `ValuationReport`, `MethodResult`                               |
| `synthesis.py`     | `SynthesisReport`, `SynthesisLayerStatus`                       |
| `portfolio.py`     | `PortfolioSummary`, `PositionRead`, `TransactionCreate`, `TransactionRead` |
| `yfinance_tabs.py` | `CalendarReport`, `ManagementReport`, `HoldersReport`, `Officer` |
| `calculation.py`   | `CalculationDetail`, `ThresholdSpec` (Étape 8 Phase B)          |

## 3. Détail couche 3 — Services

### `shariah_service.py`

**Fonction publique** : `async screen_with_personal_thresholds(symbol, db, client) → ShariahReport`

- Cache-first sur table `screen_history` (TTL 7j)
- Appel `client.screen(symbol)` si cache miss
- 5 cas mappés (cf. `01-DATA-SOURCES.md` §3) → verdict `PASS/FAIL/ERROR/NOT_COVERED`
- Persistence conditionnelle : seuls `PASS` + `FAIL` sont cachés

### `financials_service.py`

**Fonctions publiques** :
- `async get_financials(ticker, db, client) → FinancialsSnapshot` (endpoint `/financials`)
- `async get_facts_payload(ticker, db, client) → tuple[cik, entity_name, raw_facts] | None` (helper interne pour scoring modules)
- `extract_latest_annual_value(facts, concept_name) → Decimal | None`
- `extract_n_year_annuals(facts, concept_name, n) → list[(date, Decimal)]`

**Helpers privés** :
- `_walk_concept_entries(facts, tags, unit)` — parcourt us-gaap + ifrs-full, agrège FY entries
- `_is_annual_entry(entry)` — détecte fp=FY OU foreign annual form + ~12mo
- `_extract_latest_annual(facts, tags, unit)` — picks most recent end
- `_to_decimal(value)`, `_to_date(value)` — coercion safe

**Cache** : table `financials_cache` (alembic 005), TTL 24h, key = (ticker, fetched_at).

### `investissable_service.py`

**Fonction publique** : `async compute_investissable(symbol, db, halal_client, sec_client) → InvestissableReport`

**Pipeline** :
1. Récupère `ShariahReport` (Gate 1 AAOIFI). Si FAIL → court-circuit `NON` + `blocked_at: "aaoifi"`
2. Récupère `get_facts_payload` SEC EDGAR. Si non-US → `INCERTAIN` (documenté `docs/INVESTISSABLE_NON_US_LIMITATION.md`)
3. Récupère `get_submissions` SEC EDGAR pour SIC classification (`sector.classify_from_submissions`)
4. Calcule Gate 2 Altman Z'' (`altman.compute(facts)`). Si FAIL → court-circuit `NON` + `blocked_at: "altman"`
5. Calcule Gate 3 Fraud (`fraud.compute(facts, sector_info, submissions)`). Si FAIL → court-circuit `NON` + `blocked_at: "fraud"`
6. Calcule les 5 composantes qualité :
   - `piotroski.compute(facts)` (30% poids)
   - `growth.compute(facts)` (25% poids)
   - `smart_money` — V1 stub (`available=false`, documenté `docs/SCORE_PARTIAL_COMPONENTS.md`)
   - `capital_allocation.compute(facts, sector_info)` (15% poids)
   - `earnings_stability` — V1 stub
7. Agrège `quality_score` = somme pondérée des composantes disponibles, rebalancé sur les poids présents
8. Verdict final via `_final_verdict(quality_score)` : OUI (≥45) / NON / INCERTAIN (None)

### `valuation_service.py`

**Fonction publique** : `async compute_valuation(symbol, db, sec_client, *, yfinance_client=None) → ValuationReport`

**Pipeline** :
1. Récupère `get_facts_payload` SEC EDGAR (idem investissable)
2. Si yfinance_client fourni :
   - `yfinance_service.get_info()` → `current_price` (regularMarketPrice fallback chain)
   - `yfinance_service.get_history(period="5y", interval="1mo")` → 60 barres mensuelles
3. Calcule 4 méthodes :
   - M1 `_method_vs_historical_5y(facts, history, current_price)` — P/E médian 5y × EPS courant
   - M2 `_method_vs_sector(facts)` — scaffold V2 (FMP requis)
   - M3 `_method_graham_number(facts)` — `sqrt(22.5 × EPS × BVPS)`
   - M4 `_method_analyst_target(info)` — targetMedianPrice si ≥5 analystes
4. Agrège via `_aggregate(methods, current_price)` :
   - `fair_value_median` = médiane sur méthodes disponibles
   - `dispersion` = `(max - min) / median`
   - `ratio_price_to_fair_value` = `current_price / fair_value_median`
   - `verdict` = mapping FR strict : OUI / OUI_NEUTRE / NON / INDÉTERMINÉ
   - `confidence` = HIGH (4 méthodes + dispersion <0.15), MEDIUM (3 méthodes), LOW (2), N/A (<2)

### `synthesis_service.py`

**Fonction publique** : `async compute_synthesis(symbol, halal_client, sec_client, yfinance_client, *, session_factory=AsyncSessionLocal) → SynthesisReport`

**Per-branch session** (hotfix Étape 7.1A) : chaque branche `asyncio.gather` ouvre sa propre `AsyncSession` via `session_factory()` (sinon `InterfaceError` asyncpg).

```python
async def _halal_branch():
    async with factory() as session:
        return await shariah_service.screen_with_personal_thresholds(sym, session, halal_client)

async def _inv_branch():
    async with factory() as session:
        return await investissable_service.compute_investissable(sym, session, halal_client, sec_client)

async def _val_branch():
    async with factory() as session:
        return await valuation_service.compute_valuation(sym, session, sec_client, yfinance_client=yfinance_client)

halal_res, inv_res, val_res = await asyncio.gather(
    _safe(_halal_branch(), "shariah"),
    _safe(_inv_branch(), "investissable"),
    _safe(_val_branch(), "valuation"),
)
```

`_safe(coro, layer_name)` enveloppe chaque branche pour catcher toute exception → `(None, "type: msg")` plutôt qu'un raise (jamais de 5xx).

**Cascade verdict** : cf. `_overall(...)` (cf. `00-OVERVIEW.md` §3 P1).

### `portfolio_service.py`

**Fonctions publiques** :
- `async create_transaction(db, payload) → TransactionRead`
- `async list_transactions(db) → list[TransactionRead]`
- `async list_positions(db, *, yfinance_client=None) → list[PositionRead]`
- `async get_summary(db, *, yfinance_client=None) → PortfolioSummary`
- `async delete_transaction(db, tx_id) → None` (cascade Position si dernière tx)

**Algorithme P&L** (Decimal-exact, signed qty) :
```
for tx ordered by (date ASC, id ASC):
    if tx.qty > 0:  # BUY
        running_cost += tx.qty * tx.price + tx.fee
        running_qty  += tx.qty
    else:           # SELL (qty < 0)
        sell_qty     = -tx.qty
        avg_at_sell  = running_cost / running_qty
        cost_removed = avg_at_sell * sell_qty
        running_cost -= cost_removed
        running_qty  -= sell_qty
        realized_pnl += sell_qty * tx.price - cost_removed - tx.fee
    last_price = tx.price
```

**Live price** : si `yfinance_client` fourni, `_fetch_live_price()` → `regularMarketPrice` ; `price_source` = `"live"` / `"transaction"` / `"unavailable"`.

**Restriction V1** : USD only (raise `UnsupportedCurrencyError` → 400 sinon).

### `yfinance_service.py`

5 fonctions publiques, toutes avec cache-first sur `yfinance_cache` :
- `async get_info(ticker, db, client) → dict | None` (TTL 1h)
- `async get_history(ticker, db, client, *, period, interval) → list | None` (TTL 24h)
- `async get_calendar(ticker, db, client) → dict | None` (TTL 6h)
- `async get_management(ticker, db, client) → list | None` (TTL 24h)
- `async get_holders(ticker, db, client) → dict | None` (TTL 24h)

Helper `_persist()` sanitise NaN / Timestamp avant insert JSONB.

## 4. Détail couche 2 — Calculs

Cf. [`04-CALCULATIONS.md`](./04-CALCULATIONS.md) pour les formules détaillées de chaque module.

Chaque module expose une fonction `compute(facts, ...)` qui retourne un `dataclass` enrichi d'un champ `calculation_detail: dict[str, Any]` (Étape 8 Phase B) contenant formula + variables + intermediates + computation_steps + result + thresholds + interpretation.

## 5. Détail couche 1 — Clients HTTP

Cf. [`01-DATA-SOURCES.md`](./01-DATA-SOURCES.md) §1-§3 pour les détails par client.

Caractéristiques communes :
- **Singletons** : récupérés via `Depends(get_*_client)` côté FastAPI ; tests overridés via `app.dependency_overrides`
- **Retry** : `tenacity.AsyncRetrying` 3 attempts + exponential backoff
- **Fail-graceful** : SEC EDGAR raise `SECEdgarError` (caller catch) ; YFinance retourne `None` ; Halal Terminal raise `HalalTerminalError` (caller catch)

## 6. Détail couche 0 — Persistance + Config

### `app/core/config.py`

`Settings(BaseSettings)` Pydantic, lecture `.env` + env vars.

Fail-fast (sans valeur par défaut) :
- `FINTERMINAL_API_KEY` (UUID auth)
- `HALAL_TERMINAL_API_KEY`
- `SEC_EDGAR_USER_AGENT`

Avec valeur par défaut :
- `DATABASE_URL` (normalisé `postgresql://` → `postgresql+asyncpg://`)
- `SEC_EDGAR_BASE_URL`, `SEC_EDGAR_TICKER_MAP_URL`
- `HALAL_TERMINAL_BASE_URL`
- `SHARIAH_*` (5 seuils AAOIFI)
- `YFINANCE_*` (3 TTLs)
- `DCA_*` (4 réglages)
- `PORTFOLIO_*` (5 seuils)

### `app/core/database.py`

```python
engine = create_async_engine(settings.DATABASE_URL, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = DeclarativeBase
```

### `app/core/security.py`

`verify_api_key(x_auth_token: str | None = Header(...))` : compare avec `settings.FINTERMINAL_API_KEY` ; raise 401 si mismatch.

### `app/core/exceptions.py`

```
Exception
└─ ExternalAPIError
   ├─ HalalTerminalError
   │  └─ HalalTerminalNotCovered
   ├─ SECEdgarError
   │  └─ SECEdgarNotFound
   └─ YFinanceError
```

### Models SQLAlchemy (`app/models/`)

7 fichiers, cf. [`05-DATABASE.md`](./05-DATABASE.md).
