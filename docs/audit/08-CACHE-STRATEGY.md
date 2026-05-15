# 08 — Stratégie de cache

## 1. Vue d'ensemble

Le système caching de FinTerminal V1 est **multi-niveaux** sans Redis :

| Niveau                      | Storage                  | Périmètre              |
|------------------------------|--------------------------|------------------------|
| L1 — Process in-memory       | Python dict (singleton)  | CIK map, submissions   |
| L2 — Postgres JSONB          | `screen_history`         | Shariah verdicts (§11.2) |
| L2 — Postgres JSONB          | `financials_cache`       | SEC company_facts      |
| L2 — Postgres JSONB          | `yfinance_cache`         | YFinance 5 kinds       |
| L3 — Provider HTTP cache     | (externe, non géré)      | n/a                    |

Aucun cache HTTP côté Railway (pas de Redis V1). Tout est Postgres-backed.

## 2. Cache L1 — In-memory par process

### `SecEdgarClient` instance cache

```python
class SecEdgarClient:
    self._cik_map: dict[str, tuple[int, str]] | None = None   # ticker → (cik, name)
    self._cik_lock = asyncio.Lock()
    self._submissions_cache: dict[int, dict] = {}             # cik → submissions
    self._submissions_lock = asyncio.Lock()
```

- **Persistance** : durée du process Railway (~heures à jours avant redéploiement)
- **Invalidation** : restart container (déclenché par git push)
- **Sérialisation** : Lock asyncio évite double-fetch concurrent

## 3. Cache L2 — Postgres JSONB

### `screen_history` (Shariah)

Table §11.2 domaine. Sert également de cache via lecture filtrée par TTL :

```sql
SELECT *
  FROM screen_history
 WHERE symbol = $1
   AND screen_date >= NOW() - INTERVAL '7 days'
 ORDER BY screen_date DESC
 LIMIT 1
```

| Aspect           | Valeur                                    |
|------------------|-------------------------------------------|
| TTL              | 7 jours (`SHARIAH_SCREEN_TTL_DAYS`)         |
| Hit ratio cible  | > 90 % (les verdicts éthiques sont stables) |
| Persistance      | seuls `PASS` + `FAIL` cachés ; `ERROR` + `NOT_COVERED` non |
| Volume           | ~1 row par (symbol, semaine) — négligeable |
| Cleanup          | Aucun (table append-only). Alembic 003 + 004 ont purgé les rows corrompues historiques |

### `financials_cache` (SEC EDGAR)

```sql
SELECT *
  FROM financials_cache
 WHERE ticker = $1
   AND fetched_at >= NOW() - INTERVAL '24 hours'
 ORDER BY fetched_at DESC
 LIMIT 1
```

| Aspect            | Valeur                                       |
|-------------------|----------------------------------------------|
| TTL               | 24 heures (`FINANCIALS_CACHE_TTL_HOURS`)       |
| Hit ratio cible   | > 99 % (1 fetch par ticker par jour ouvré)    |
| Volume row        | ~50-200 KB JSONB (company_facts entier)        |
| Volume total estimé | quelques dizaines de MB pour le périmètre V1 |
| Cleanup           | aucun — Postgres TOAST gère la compression     |

### `yfinance_cache` (YFinance)

```sql
SELECT *
  FROM yfinance_cache
 WHERE ticker = $1
   AND payload_kind = $2          -- 'info' | 'history' | 'calendar' | 'management' | 'holders'
   AND params = $3                 -- '' or 'period=5y/interval=1mo' etc.
   AND fetched_at >= NOW() - INTERVAL $4
 ORDER BY fetched_at DESC
 LIMIT 1
```

| `payload_kind` | TTL                                  | Justification |
|----------------|--------------------------------------|---------------|
| `info`         | 1h (`YFINANCE_INFO_TTL_HOURS`)        | Prix live-ish — short TTL |
| `history`      | 24h (`YFINANCE_HISTORY_TTL_HOURS`)    | Barres mensuelles stables |
| `calendar`     | 6h                                   | Earnings date bouge sur guidance |
| `management`   | 24h                                  | Officers changent ~1-2 fois/an |
| `holders`      | 24h                                  | 13F est trimestriel anyway |

**Discriminant `params`** : permet de cacher plusieurs variantes (`period=5y/interval=1mo` vs `period=1y/interval=1mo`) sous le même `payload_kind`. Pour Étape 8 Phase C, `params` est toujours `""` (un seul variant par kind).

### Sanitisation JSONB

```python
def _sanitize_for_jsonb(payload):
    # NaN / inf → None
    # datetime / date → isoformat string
    # Récursif dans dict / list
```

Évite les `InvalidTextRepresentation` côté asyncpg sur les valeurs Yahoo bordées (NaN occasionnel, Timestamps pandas).

## 4. Cache miss-fail policy

| Cas                                          | Action |
|----------------------------------------------|--------|
| Cache miss + provider returns valid data     | Insert cache row, return data |
| Cache miss + provider returns None / empty   | **Pas** d'insert, return None / verdict ERROR |
| Cache miss + provider raises (exception)     | **Pas** d'insert, propagate ou retourne None selon le client |
| Provider data malformed (parse failure)      | **Pas** d'insert, log WARNING, return None |

→ Le cache n'enregistre **jamais** les états dégradés. Re-tentative sera faite au prochain appel, ce qui permet une recovery dès que la source externe est rétablie.

## 5. Invalidation manuelle

Pas d'API d'invalidation côté V1. Stratégies pour forcer un refresh :

1. **Truncate ciblé** via psql Railway :
   ```sql
   DELETE FROM yfinance_cache WHERE ticker = 'AAPL' AND payload_kind = 'info';
   ```
2. **Migration alembic dédiée** (cf. alembic 003, 004, 007 pour des purges historiques)
3. **Restart container** — invalide les caches L1 in-memory (CIK map + submissions)

V2 envisagé : endpoint admin `POST /api/v1/admin/cache/invalidate?ticker=AAPL` (hors scope V1).

## 6. Pré-warming

Aucun pré-warming en V1. Le premier appel par ticker est cold. Tickers chauds (AAPL, MSFT, etc.) restent dans le cache 24h tant qu'utilisés au moins une fois par jour.

V2 envisagé : pré-warming au boot via la liste des tickers de portfolio actifs.

## 7. Métriques cache observables (logs)

Les services émettent des log lines structurées :

```
INFO yfinance_service cache HIT kind=info ticker=AAPL
INFO yfinance_service MISS+fresh kind=info ticker=AAPL
WARNING yfinance_service MISS+fail kind=info ticker=ZZZZ
INFO financials_service ... source="cache (financials_cache)" cache_age_hours=12.5
```

V1 : pas d'aggregation Prometheus/Grafana. Logs Railway console seulement.

## 8. Concurrent access — async-safe ?

**Postgres caches** : INSERT concurrent OK (chaque row a un UUID unique, pas de UNIQUE constraint sur `(ticker, fetched_at)`). Au pire, deux rows fresh pour le même ticker, l'ORDER BY DESC LIMIT 1 sélectionne la plus récente.

**In-memory caches** : `asyncio.Lock` autour de la fetch initiale du CIK map et des submissions. Double-check après acquire pour éviter le re-fetch.

**SQLAlchemy AsyncSession** : **NON thread-safe / NON concurrent-safe**. C'est ce qui a causé le bug Étape 7.1A (`InterfaceError: another operation in progress` quand `asyncio.gather` partageait une session). Fix : `synthesis_service` ouvre une session par branche via `session_factory()`.

## 9. Sécurité du cache

- Le cache n'expose pas de PII (ce sont des données financières publiques)
- Pas de chiffrement at-rest spécifique (Postgres Railway gère le storage encryption au niveau infrastructure)
- Aucun secret ni token API caché dans les payloads JSONB

## 10. Estimation taille DB

| Table                | Rows estimés V1 | Taille moyenne row | Total estimé |
|----------------------|------------------|--------------------|--------------|
| `positions`           | < 100            | ~200 B             | < 20 KB      |
| `transactions`       | < 1000           | ~300 B             | < 300 KB     |
| `screen_history`      | < 10 000         | ~5 KB              | < 50 MB      |
| `financials_cache`    | < 1000           | ~100 KB            | < 100 MB     |
| `yfinance_cache`     | < 5000           | ~30 KB             | < 150 MB     |
| **Total**            |                  |                    | **< 300 MB** |

Largement sous le quota Railway free-tier (~1 GB).
