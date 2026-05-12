# YFinance integration notes — Étape 5

Cette note documente le **pourquoi** et le **comment** de l'intégration
YFinance (étape 5 du roadmap FinTerminal v2.1). Elle complète :

- `docs/VALUATION_PARTIAL_METHODS.md` (V1 limitations §4.3) — désormais
  partiellement levées par cette étape.
- `app/integration/yfinance_client.py` (code) — référence d'autorité
  technique.

## 1. Pourquoi `yfinance` (lib) et pas HTTP direct

YFinance scrape les endpoints non documentés de Yahoo Finance. Ces
endpoints :

- requièrent un handshake CSRF (cookies + crumb) qui évolue dans le temps
- changent de forme (renommage de clés, restructuration) sans préavis
- rate-limitent agressivement par IP, sans header standard

La lib `yfinance` (community-maintained) absorbe ces dérives. Le coût :

- **synchrone** → on l'enveloppe dans `asyncio.to_thread` pour ne jamais
  bloquer l'event loop FastAPI (Q1 plan validated).
- **pas de SLA** → on retry 3× avec backoff exponentiel, puis on
  retourne `None` (jamais d'exception qui remonte au handler HTTP).
- **pas de clé API** → aucun secret à provisionner ; pas non plus de
  variable fail-fast à ajouter dans `backend-ci.yml`.

L'alternative HTTP directe (réimplémenter le handshake Yahoo) aurait
représenté ~1-2 jours de dev + maintenance perpétuelle. Hors scope V1.

## 2. TTL stratégie

Deux types de payloads, deux TTL distincts (cf.
`app/services/yfinance_service.py`) :

| Payload kind | TTL              | Justification                                                         |
| ------------ | ---------------- | --------------------------------------------------------------------- |
| `info`       | 1h (`YFINANCE_INFO_TTL_HOURS`)    | `regularMarketPrice` doit rester "live-ish" pour le portfolio.       |
| `history`    | 24h (`YFINANCE_HISTORY_TTL_HOURS`) | Barres mensuelles 5y — stale-OK pendant 24h (le dernier mois ne bouge pas autant qu'un cours intraday). |

Les deux TTLs sont des **réglables `config.py`**, pas des magic
numbers. Doublez `YFINANCE_INFO_TTL_HOURS` (1 → 2h) si Yahoo nous
rate-limite agressivement sans tuer la valeur fonctionnelle.

## 3. Caveats Yahoo connus

- **Rate limit IP** : Yahoo bloque parfois une IP partagée (cas Railway
  free tier) sans préavis. Symptôme : `yfinance` retourne un `.info`
  vide (`{}`) ou un dict stub (`{"trailingPegRatio": None}`). Notre
  client traite ces deux cas comme retryable → après 3 retries → `None`
  → fallback gracieux.

- **Clés `.info` instables** : la même `Ticker.info` peut renvoyer
  `regularMarketPrice` pour AAPL et `currentPrice` pour MSFT le même
  jour. Le code (`portfolio_service._fetch_live_price` et
  `valuation_service._extract_current_price`) essaie 3 clés en
  priorité : `regularMarketPrice` → `currentPrice` → `previousClose`.

- **NaN** : les float NaN/inf reviennent occasionnellement dans `.info`
  (forwardPE pour une boîte en perte) ou dans `history()` (week-end /
  jour férié). Notre client filtre NaN/inf de history. Le service
  `yfinance_service` sanitise NaN → `None` avant insertion JSONB pour
  éviter les erreurs asyncpg.

- **Symboles non-US** : `yfinance` accepte les suffixes (`AIXA.DE`, etc.)
  mais la qualité des données est très variable. V1 ne dépend pas de
  YFinance pour les non-US (la couverture SEC EDGAR US-only domine déjà
  ces tickers à `INDÉTERMINÉ`).

## 4. Fallback playbook (Yahoo down 24h+)

Que se passe-t-il si Yahoo est indisponible une journée entière ?

| Feature                       | Comportement                                                                 |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `GET /portfolio/positions`    | `last_price` = dernier prix de transaction, `price_source = "transaction"`.  |
| `GET /portfolio/summary`      | `current_value`, `unrealized_pnl` calculés sur prix tx — pas de 5xx.          |
| `GET /valuation/{symbol}`     | Method 1 + Method 4 `available=false`. Graham (Method 3) reste calculé.       |
| Aucun                         | Le verdict global devient `INDÉTERMINÉ` (n_methods < 2 si Yahoo down et que seul Graham reste). |

Aucune route ne renvoie 5xx à cause de YFinance. C'est la garantie
"fail-graceful" (Q3 plan validated). À surveiller via les logs
`yfinance_service MISS+fail` (level WARNING).

Si Yahoo reste down > 48h **et** qu'on a besoin du portfolio live :
provisionner une intégration FMP (`/v3/quote/{symbol}`) comme fallback
secondaire. Hors scope V1 — à anticiper dans étape 5.5 ou 6.

## 5. Plug-in points pour les étapes suivantes

### Étape 5.5 — Smart Money + simulate_position_add

YFinance ne couvre pas le 13F institutionnel ni les insider transactions
de manière fiable. Smart Money est donc reporté à 5.5 et nécessitera
une intégration distincte (Finnhub `/stock/insider-transactions` ou
SEC Form 4 direct).

`simulate_position_add` : besoin uniquement de `current_price` (déjà
livré par `yfinance_service.get_info`). C'est une feature **service +
endpoint nouveau** sans intégration externe additionnelle.

### Méthode 2 (multiples vs sector) — étape 7 ou continue

Toujours non implémentée. Nécessite FMP (`/v3/stock_peers/{symbol}`) ou
la construction manuelle de peer groups (lourd). Le scaffold
`_method_vs_sector(facts)` reste en place avec sa raison V1.

### Méthode 1 — ouverture future de P/S et EV/EBITDA

V1 limite Method 1 à **P/E uniquement** pour rester sous le cap 3h. Les
deux multiples manquants requièrent :

- **P/S** : shares-outstanding historique par trimestre (SEC EDGAR le
  contient — `CommonStockSharesOutstanding` peut être extrait
  `n_year_annuals`). Plug-in : étendre `_method_vs_historical_5y` pour
  calculer market-cap-per-bar puis P/S.
- **EV/EBITDA** : nécessite EBITDA historique (SEC `OperatingIncomeLoss`
  + `DepreciationAndAmortization`) + cash + total debt. ~4h de dev
  supplémentaire.

## 6. Risques résiduels

| Risque                              | Sévérité | Mitigation actuelle                                  |
| ----------------------------------- | -------- | ---------------------------------------------------- |
| Yahoo change la shape de `.info`    | Moyenne   | 3 clés essayées en cascade ; tests fragility unit.   |
| Rate limit IP en production         | Haute     | Cache 1h sur info, 24h sur history ; retries 3×.      |
| Dérive lib yfinance (breaking change) | Faible | `>=0.2.40` pin minimal, pas de upper bound serré.    |
| Différence `current_price` vs DB    | N/A       | `price_source` field expose l'origine au consumer.   |

## 7. Mise à jour de ce document

Quand une feature passe **online** (Method 2, Smart Money, simulate),
retirer sa section "Plug-in point" et mettre à jour
`docs/VALUATION_PARTIAL_METHODS.md` en parallèle. Quand FMP remplace
YFinance comme source primaire, archiver ce document.
