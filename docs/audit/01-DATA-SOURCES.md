# 01 — Sources de données externes

## 1. SEC EDGAR

### Configuration

| Setting                       | Valeur                                                   |
|-------------------------------|----------------------------------------------------------|
| `SEC_EDGAR_BASE_URL`          | `https://data.sec.gov`                                   |
| `SEC_EDGAR_TICKER_MAP_URL`    | `https://www.sec.gov/files/company_tickers.json`         |
| `SEC_EDGAR_USER_AGENT`        | **fail-fast** (CI placeholder : `"FinTerminal CI ci@example.com"`) |
| `SEC_EDGAR_TIMEOUT_S`         | 15.0 secondes                                            |

### Endpoints externes consommés

| Méthode client                       | URL                                                                | Réponse |
|--------------------------------------|--------------------------------------------------------------------|---------|
| `lookup_cik(ticker)`                 | `GET /files/company_tickers.json` (1×, in-memory)                  | dict[ticker → (cik, title)] |
| `get_company_facts(cik)`             | `GET /api/xbrl/companyfacts/CIK{cik:010d}.json`                    | dict facts complet (us-gaap + ifrs-full + dei) |
| `get_submissions(cik)`               | `GET /submissions/CIK{cik:010d}.json`                              | dict filings.recent + sicCode |

### Code

- **Client HTTP** : `app/integration/sec_edgar_client.py` (`SecEdgarClient` class)
- **Retry** : `tenacity.AsyncRetrying` 3 attempts + exponential backoff (0.5..2.0s)
- **Cache CIK map** : in-memory par instance, lock asyncio (rebuild après restart container)
- **Cache submissions** : in-memory par CIK, lock asyncio
- **Exception** : `SECEdgarError` (héritage `ExternalAPIError`)

### Données extraites (21 concepts comptables annuels)

Chaque concept est mappé à une liste ordonnée de tags GAAP/IFRS via `_CONCEPT_TAGS`. Tous les tags listés sont parcourus, les entrées annuelles agrégées, et la plus récente retenue (corrige le bug Étape 7.3.D).

```python
_CONCEPT_TAGS: dict[str, list[str]] = {
    "revenues": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
        "Revenue",
        "ifrs-full:Revenue",
    ],
    "net_income": [
        "NetIncomeLoss", "ProfitLoss",
        "ifrs-full:ProfitLoss",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
    ],
    "total_assets": ["Assets", "ifrs-full:Assets"],
    "long_term_debt": [
        "LongTermDebt", "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations", "LongTermBorrowings",
        "ifrs-full:LongtermBorrowings", "ifrs-full:NoncurrentBorrowings",
    ],
    # ... 17 autres concepts (cf. financials_service.py:56-179)
}
```

### Reconnaissance des formes annuelles (Étape 8 Phase A)

```python
_ANNUAL_FORMS = frozenset({
    "10-K", "10-K/A", "10-KSB", "10-KSB/A",      # US domestic
    "20-F", "20-F/A",                              # foreign private issuers
    "40-F", "40-F/A",                              # Canadian filers (MJDS)
})

_ANNUAL_TAXONOMIES = ("us-gaap", "ifrs-full")
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 320, 400  # période ~12 mois
```

Une entrée est annuelle si :
- `fp == "FY"` (US 10-K) **OU**
- `form` ∈ `_ANNUAL_FORMS` ET durée (end - start) entre 320 et 400 jours

### Coût

- **$0** — endpoint public
- Pas de quota journalier
- Rate limit auto-rate-limited à 10 req/sec (jamais frôlé en V1 avec le caching 24h)

---

## 2. YFinance

### Configuration

| Setting                       | Valeur                  |
|-------------------------------|-------------------------|
| `YFINANCE_TIMEOUT_S`          | 15.0 secondes           |
| `YFINANCE_INFO_TTL_HOURS`     | 1h (quote live-ish)     |
| `YFINANCE_HISTORY_TTL_HOURS`  | 24h (barres mensuelles) |
| `_CALENDAR_TTL_HOURS`         | 6h (next earnings date) |
| `_MANAGEMENT_TTL_HOURS`       | 24h (officers stables)  |
| `_HOLDERS_TTL_HOURS`          | 24h (13F trimestriel)   |

### Méthodes client

| Méthode                        | Source yfinance                          | Norme retour |
|--------------------------------|------------------------------------------|--------------|
| `get_info(symbol)`             | `Ticker(s).info`                         | `dict | None` |
| `get_history(s, period, ...)`  | `Ticker(s).history(...)`                 | `list[{date, close}] | None` |
| `get_calendar(symbol)`         | `.calendar` + `.earnings_history` + `.dividends` | `dict | None` |
| `get_management(symbol)`       | `.info["companyOfficers"]`               | `list[dict] | None` |
| `get_holders(symbol)`          | `.major_holders` + `.institutional_holders` + `.insider_transactions` | `dict | None` |

### Code

- **Client** : `app/integration/yfinance_client.py` (`YFinanceClient` class)
- **Async wrapper** : `asyncio.to_thread(sync_fn, ...)` (yfinance est sync)
- **Retry** : 3 attempts + exponential backoff (transient errors only)
- **Timeout** : `asyncio.wait_for` cancel après `_timeout` secondes
- **Fail-graceful** : retourne `None` après échec/retries → caller décide (`available=false`)
- **Helpers JSON** : `_coerce_jsonable` (Timestamp / NaN / numpy → JSON), `_df_to_records`, `_series_to_records`
- **Exception interne** : `_RetryableYFError` (jamais propagée à l'extérieur du client)

### Clés `.info` consommées

| Clé                          | Consommateur                                          |
|------------------------------|--------------------------------------------------------|
| `regularMarketPrice`         | `_extract_current_price`, fallback `currentPrice`, `previousClose` |
| `targetMedianPrice`          | `_method_analyst_target` (Méthode 4 valuation) |
| `targetLowPrice`, `targetHighPrice` | idem (affichés en détails) |
| `numberOfAnalystOpinions`    | guard ≥5 analystes (spec §4.3.4) |
| `companyOfficers[]`          | `_fetch_management_sync` (Étape 8 Phase C) |

### Caveats Yahoo (documentés `docs/YFINANCE_INTEGRATION_NOTES.md`)

- Rate limit IP fréquent depuis Railway → cache 1h sur info pour limiter
- `.info` peut retourner `{}` ou stub `{"trailingPegRatio": None}` → traité comme transient (retry)
- NaN occasionnel → filtré (`_sanitize_for_jsonb`)
- Clés `.info` instables : 3 keys testées en cascade pour le prix

### Coût

- **$0** — lib community-maintained
- Aucune clé API requise
- Risque : Yahoo peut bloquer une IP partagée (Railway free tier) — d'où la stratégie fail-graceful

---

## 3. Halal Terminal

### Configuration

| Setting                       | Valeur                                                   |
|-------------------------------|----------------------------------------------------------|
| `HALAL_TERMINAL_BASE_URL`     | `https://api.halalterminal.com`                          |
| `HALAL_TERMINAL_API_KEY`      | **fail-fast** UUID                                       |
| `HALAL_TERMINAL_TIMEOUT_S`    | 10.0 secondes                                            |
| `SHARIAH_SCREEN_TTL_DAYS`     | 7 jours                                                  |

### Endpoint externe consommé

`GET /screening/{symbol}` (free-tier mode — retourne un verdict agrégé, pas les 4 ratios bloquants détaillés)

### Code

- **Client** : `app/integration/halal_terminal_client.py` (`HalalTerminalClient` class)
- **Retry** : tenacity sur transient (timeout / 5xx)
- **Verbose logging** (Fix B Étape 1.5) : INFO / WARNING / ERROR + body preview
- **Exception** : `HalalTerminalError` (héritage `ExternalAPIError`), sous-classe `HalalTerminalNotCovered` (HTTP 404)

### Mapping verdict (free-tier)

| Réponse provider                          | Verdict interne | Cache ? |
|-------------------------------------------|------------------|---------|
| `error = "ticker_unknown"`                | `NOT_COVERED`    | ❌ |
| Tous champs agrégés `= true`              | `PASS`           | ✅ 7j |
| Au moins un champ agrégé `= false`        | `FAIL`           | ✅ 7j |
| Tous champs `null` sans erreur            | `ERROR`          | ❌ |
| Échec HTTP / timeout / network            | `ERROR`          | ❌ |

### Déviation V1 — documentée `docs/SHARIAH_FREE_TIER_DEVIATION.md`

Spec §5.1/§5.2 prévoit le calcul personnel des 4 ratios bloquants AAOIFI custom (debt/MC ≤ 0.30, cash/MC ≤ 0.30, impure_revenue ≤ 0.03, interest_income ≤ 0.03). Le free-tier du provider ne retourne pas les ratios bruts → on consomme l'agrégat tel quel. Les `raw_ratios` du schema `ShariahReport` sont `None` en free-tier.

### Coût

- **$0** — free-tier
- Limitations exactes du quota free non documentées par le provider
- Le caching 7j minimise l'usage (~1 appel/ticker/semaine en pratique)

---

## 4. Sources configurées mais non consommées

| Setting              | Provider visé        | Statut V1 | Usage prévu V2 |
|----------------------|----------------------|-----------|----------------|
| `FMP_API_KEY`        | Financial Modeling Prep | ❌ non câblée | Méthode 2 valuation (sector peer groups) |
| `ALPHA_VANTAGE_KEY`  | Alpha Vantage        | ❌ non câblée | TBD |
| `MUSAFFA_API_KEY`    | Musaffa              | ❌ non câblée | Provider Shariah alternatif |

Ces clés sont déclarées dans `app/core/config.py` mais aucun service ne les utilise en V1.

---

## 5. Synthèse coûts & dépendances

| Source            | Clé API requise   | Coût mensuel V1 | Tables cache          | Statut |
|-------------------|-------------------|------------------|-----------------------|--------|
| SEC EDGAR         | Aucune (UA seul)  | $0               | `financials_cache`   | 🟢 active |
| YFinance          | Aucune (lib)      | $0               | `yfinance_cache`     | 🟢 active |
| Halal Terminal    | UUID (free-tier)  | $0               | `screen_history` (§11.2) | 🟢 active |
| FMP               | $14-29/mois (V2)  | $0               | —                     | 🟡 reportée V2 |
| Alpha Vantage     | $50+/mois (V2)    | $0               | —                     | 🟡 reportée V2 |
| Musaffa           | TBD               | $0               | —                     | 🟡 reportée V2 |

**Total V1 : 0 € de coût mensuel** pour les sources de données externes.
