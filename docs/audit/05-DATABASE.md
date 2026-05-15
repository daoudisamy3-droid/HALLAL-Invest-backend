# 05 — Schéma PostgreSQL

## 1. Inventaire des tables

| Table                | Origine           | Spec §11.2 ? | Étape    | TTL applicatif |
|----------------------|-------------------|--------------|----------|----------------|
| `positions`           | alembic 001        | ✅           | 0        | n/a (persisté) |
| `transactions`       | alembic 001        | ✅           | 0        | n/a (persisté) |
| `screen_history`      | alembic 001        | ✅           | 0 (utilisé Étape 1) | 7 jours |
| `conviction_history` | alembic 001        | ✅           | 0 (réservé V2)      | n/a |
| `fair_value_history` | alembic 001        | ✅           | 0 (réservé V2)      | n/a |
| `financials_cache`    | alembic 005        | ❌ technique | 2        | 24 heures |
| `yfinance_cache`     | alembic 006        | ❌ technique | 5 (étendu Étape 8C) | 1h / 6h / 24h |

Les 5 tables `§11.2` sont domaine spec. Les 2 caches techniques sont
documentées comme extension d'infrastructure (hors §11.2).

## 2. Migrations alembic

| Revision | Description                                                    | Étape |
|----------|----------------------------------------------------------------|-------|
| 001      | Schéma initial — 5 tables §11.2                                | 0     |
| 002      | Index composé DESC sur `screen_history(symbol, screen_date)`   | 1     |
| 003      | Purge corrupted shariah cache rows (bug ratios vides Étape 1.5) | 1.5  |
| 004      | Purge today's pre-refactor cache rows (idem)                    | 1.5  |
| 005      | `financials_cache` table                                       | 2     |
| 006      | `yfinance_cache` table                                         | 5     |
| 007      | `purge_zzz_bidon_test_rows` (housekeeping)                      | 7    |

Toutes les migrations sont jouées automatiquement au boot Railway via le
Procfile : `alembic upgrade head && uvicorn app.main:app`.

## 3. Schémas détaillés

### `positions`

```sql
CREATE TABLE positions (
    id          UUID PRIMARY KEY,
    symbol      TEXT NOT NULL,
    currency    TEXT NOT NULL,
    opened_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (symbol, currency)
);
```

V1 : `currency = 'USD'` strict (cf. `UnsupportedCurrencyError`).

### `transactions`

```sql
CREATE TABLE transactions (
    id            UUID PRIMARY KEY,
    position_id   UUID NOT NULL REFERENCES positions(id),
    date          DATE NOT NULL,
    qty           NUMERIC(18, 8) NOT NULL,  -- signed: positif=BUY, négatif=SELL
    price         NUMERIC(18, 8) NOT NULL CHECK (price > 0),
    fee           NUMERIC(18, 8) NOT NULL DEFAULT 0 CHECK (fee >= 0)
);

CREATE INDEX ix_transactions_position_date ON transactions(position_id, date);
```

Decimal-exact via `NUMERIC(18,8)`. P&L dérivé à la lecture (jamais stocké).

### `screen_history` (Shariah cache)

```sql
CREATE TABLE screen_history (
    id             UUID PRIMARY KEY,
    symbol         TEXT NOT NULL,
    screen_date    TIMESTAMPTZ NOT NULL,
    verdict        TEXT NOT NULL,
    failed_checks  JSONB NOT NULL DEFAULT '[]',
    checks_json    JSONB,
    methodology_verdicts JSONB,
    raw_ratios_json JSONB,
    as_of_date     DATE,
    source         TEXT NOT NULL,
    reason         TEXT
);

CREATE INDEX ix_screen_history_symbol_date_desc
    ON screen_history(symbol, screen_date DESC);  -- alembic 002
```

TTL applicatif 7j (`SHARIAH_SCREEN_TTL_DAYS`). Lecture : `WHERE symbol = ? AND screen_date >= now() - 7d ORDER BY screen_date DESC LIMIT 1`. Seuls `PASS` et `FAIL` sont persistés.

### `conviction_history` (réservé V2)

```sql
CREATE TABLE conviction_history (
    id            UUID PRIMARY KEY,
    symbol        TEXT NOT NULL,
    captured_at   TIMESTAMPTZ NOT NULL,
    score         NUMERIC(5, 2),
    label         TEXT,
    components_json JSONB
);
```

Réservée V2 (timeseries des verdicts INVESTISSABLE).

### `fair_value_history` (réservé V2)

```sql
CREATE TABLE fair_value_history (
    id              UUID PRIMARY KEY,
    symbol          TEXT NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL,
    fair_value_median NUMERIC(18, 8),
    confidence       TEXT,
    methods_json    JSONB
);
```

Réservée V2 (timeseries des verdicts VALORISÉE).

### `financials_cache` (alembic 005)

```sql
CREATE TABLE financials_cache (
    id          UUID PRIMARY KEY,
    ticker      TEXT NOT NULL,
    cik         BIGINT NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    facts_json  JSONB NOT NULL
);

CREATE INDEX ix_financials_cache_ticker
    ON financials_cache(ticker);
CREATE INDEX ix_financials_cache_ticker_fetched_at_desc
    ON financials_cache(ticker, fetched_at DESC);
```

TTL 24h (`FINANCIALS_CACHE_TTL_HOURS`). Stocke le blob complet `company_facts` SEC EDGAR pour permettre extraction multi-concept sans re-fetch. Lecture : `WHERE ticker = ? AND fetched_at >= now() - 24h ORDER BY fetched_at DESC LIMIT 1`.

### `yfinance_cache` (alembic 006, étendu Étape 8C)

```sql
CREATE TABLE yfinance_cache (
    id            UUID PRIMARY KEY,
    ticker        TEXT NOT NULL,
    payload_kind  TEXT NOT NULL,   -- 'info' | 'history' | 'calendar' | 'management' | 'holders'
    params        TEXT NOT NULL DEFAULT '',  -- ex: 'period=5y/interval=1mo'
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload_json  JSONB NOT NULL
);

CREATE INDEX ix_yfinance_cache_lookup
    ON yfinance_cache(ticker, payload_kind, params, fetched_at);
```

5 `payload_kind` distincts en V1, chacun avec son TTL applicatif :
- `info` 1h (`YFINANCE_INFO_TTL_HOURS`)
- `history` 24h (`YFINANCE_HISTORY_TTL_HOURS`)
- `calendar` 6h
- `management` 24h
- `holders` 24h

Le discriminant `params` permet de cacher plusieurs variantes (`period=5y/interval=1mo` vs `period=1y/interval=1mo`) sous le même payload_kind.

## 4. Modèles SQLAlchemy

| Fichier                            | Classe                | Mapping table        |
|------------------------------------|-----------------------|----------------------|
| `app/models/position.py`            | `Position`             | positions            |
| `app/models/transaction.py`         | `Transaction`          | transactions         |
| `app/models/screen_history.py`     | `ScreenHistory`        | screen_history       |
| `app/models/conviction_history.py` | `ConvictionHistory`   | conviction_history   |
| `app/models/fair_value_history.py` | `FairValueHistory`    | fair_value_history   |
| `app/models/financials_cache.py`   | `FinancialsCache`      | financials_cache     |
| `app/models/yfinance_cache.py`     | `YFinanceCache`        | yfinance_cache       |

Tous héritent de `Base` (`DeclarativeBase` SQLAlchemy 2.0). UUIDs auto-générés via `uuid.uuid4()`.

## 5. Connexion DB

```python
# app/core/database.py
engine = create_async_engine(
    settings.DATABASE_URL,  # postgresql+asyncpg://...
    pool_size=10,
    max_overflow=20,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False,
)
```

Normalisation `postgresql://` → `postgresql+asyncpg://` via `field_validator` dans `Settings` (Railway injecte le préfixe sync par défaut).

Côté Alembic, transformation inverse `+asyncpg` → `+psycopg2` pour les migrations sync.

## 6. Test isolation (conftest.py)

```python
_TABLES_TO_TRUNCATE = (
    "transactions", "positions", "screen_history",
    "conviction_history", "fair_value_history",
    "financials_cache", "yfinance_cache",
)
```

Stratégie d'isolation :
- Engine **function-scoped** (asyncpg ↔ event loop binding)
- TRUNCATE de toutes les tables sur teardown (real commits + reset)
- SAVEPOINT/join-transaction pattern écarté (fragile avec asyncpg)

## 7. Variables d'environnement obligatoires

| Variable               | Format                                           | Fail-fast ? |
|------------------------|--------------------------------------------------|-------------|
| `DATABASE_URL`         | `postgresql[+driver]://user:pass@host:port/db`    | ❌ (default) |
| `FINTERMINAL_API_KEY`  | UUID                                             | ✅          |
| `HALAL_TERMINAL_API_KEY` | UUID                                            | ✅          |
| `SEC_EDGAR_USER_AGENT` | `"AppName email@example.com"`                    | ✅          |

CI : tous hoist au job-level dans `.github/workflows/backend-ci.yml` avec placeholders.
