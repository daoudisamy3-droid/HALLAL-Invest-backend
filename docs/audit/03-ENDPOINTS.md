# 03 — Inventaire exhaustif des endpoints REST

Tous les endpoints sous `/api/v1/` sont protégés par `verify_api_key`
(header `X-Auth-Token: <uuid>`).

`/health` et `/openapi.json` sont **hors** du router authentifié (publics).

## 1. Tableau récapitulatif

| # | Méthode | Path                                       | Service appelé                     | Étape origine |
|---|---------|--------------------------------------------|------------------------------------|---------------|
| 0 | GET     | `/health`                                  | (smoke)                            | 0             |
| 1 | GET     | `/api/v1/ping`                             | (smoke authenticated)              | 0             |
| 2 | GET     | `/api/v1/shariah/{symbol}`                 | `shariah_service.screen_with_personal_thresholds` | 1 |
| 3 | GET     | `/api/v1/financials/{ticker}`              | `financials_service.get_financials` | 2             |
| 4 | POST    | `/api/v1/portfolio/transactions`           | `portfolio_service.create_transaction` | 3         |
| 5 | GET     | `/api/v1/portfolio/transactions`           | `portfolio_service.list_transactions`  | 3         |
| 6 | DELETE  | `/api/v1/portfolio/transactions/{tx_id}`   | `portfolio_service.delete_transaction` | 3         |
| 7 | GET     | `/api/v1/portfolio/positions`              | `portfolio_service.list_positions` (with live yfinance) | 3+5 |
| 8 | GET     | `/api/v1/portfolio/summary`                | `portfolio_service.get_summary`        | 3+5         |
| 9 | GET     | `/api/v1/investissable/{symbol}`           | `investissable_service.compute_investissable` | 4    |
| 10 | GET    | `/api/v1/valuation/{symbol}`               | `valuation_service.compute_valuation`  | 4.5+5       |
| 11 | GET    | `/api/v1/synthesis/{symbol}`               | `synthesis_service.compute_synthesis`  | 6           |
| 12 | GET    | `/api/v1/calendar/{symbol}`                | `yfinance_service.get_calendar`        | 8C          |
| 13 | GET    | `/api/v1/management/{symbol}`              | `yfinance_service.get_management`      | 8C          |
| 14 | GET    | `/api/v1/holders/{symbol}`                 | `yfinance_service.get_holders`         | 8C          |

## 2. Détails par endpoint

### `GET /api/v1/shariah/{symbol}`

**Auth** : `X-Auth-Token` requis
**Statut** : toujours 200 (verdict dans le body)
**Schema** : `ShariahReport`

```jsonc
{
  "symbol": "AAPL",
  "verdict": "PASS",              // PASS | FAIL | ERROR | NOT_COVERED
  "failed_checks": [],            // empty in free-tier
  "checks": {},                   // empty in free-tier
  "halal_terminal_methodology_verdicts": {},
  "raw_ratios": { /* null fields */ },
  "as_of_date": null,
  "source": "Halal Terminal API (aggregate verdict)",
  "cached": false,
  "cache_age_days": null,
  "reason": null
}
```

### `GET /api/v1/financials/{ticker}`

**Schema** : `FinancialsSnapshot`

```jsonc
{
  "ticker": "AAPL",
  "cik": 320193,
  "entity_name": "Apple Inc.",
  "verdict": "AVAILABLE",         // AVAILABLE | NOT_COVERED | ERROR
  "fiscal_year": 2024,
  "period_end": "2024-09-28",
  "form": "10-K",
  "accession_number": "0000320193-24-000123",
  "filed": "2024-11-01",
  "revenues": "391035000000",     // Decimal as string
  "net_income": "93736000000",
  "total_assets": "352755000000",
  "long_term_debt": "85750000000",
  "short_term_borrowings": "9967000000",
  "cash_and_equivalents": "29943000000",
  "interest_income_operating": null,
  "eps_diluted": "6.11",
  "operating_cash_flow": "118254000000",
  "capex": "9447000000",
  "stockholders_equity": "56950000000",
  "source": "SEC EDGAR API",
  "cached": false,
  "cache_age_hours": null,
  "reason": null
}
```

### `GET /api/v1/investissable/{symbol}`

**Schema** : `InvestissableReport`

```jsonc
{
  "symbol": "AAPL",
  "verdict": "OUI",               // OUI | NON | INCERTAIN
  "label": "QUALITÉ EXCELLENTE",
  "quality_score": 74.1,
  "blocked_at": null,             // "aaoifi" | "altman" | "fraud" | null
  "gates": {
    "aaoifi":  { "verdict": "PASS", "details": {...}, "reason": null },
    "altman":  { "verdict": "PASS", "details": { "z_score": "3.5116", "calculation_detail": {...} } },
    "fraud":   { "verdict": "PASS", "details": { "signals": {...} } }
  },
  "quality_components": {
    "piotroski":          { "score": 78.0, "available": true,  "details": {...} },
    "growth":             { "score": 82.0, "available": true,  "details": {...} },
    "smart_money":        { "score": null, "available": false, "details": { "reason": "..." } },
    "capital_allocation": { "score": 70.0, "available": true,  "details": { "d1": {...}, "d3": {...} } },
    "earnings_stability": { "score": null, "available": false, "details": { "reason": "..." } }
  },
  "data_completeness": "PARTIAL",  // FULL | PARTIAL | INSUFFICIENT
  "warnings": [],
  "reason": null
}
```

Chaque entrée `gates.*.details` ou `quality_components.*.details` peut porter un sous-objet `calculation_detail` (Étape 8 Phase B) pour rendu accordéon frontend.

### `GET /api/v1/valuation/{symbol}`

**Schema** : `ValuationReport`

```jsonc
{
  "symbol": "AAPL",
  "verdict": "NON",                  // OUI | OUI_NEUTRE | NON | INDÉTERMINÉ
  "label": "SURÉVALUÉE",             // verbose label or null
  "confidence": "MEDIUM",            // HIGH | MEDIUM | LOW | N/A
  "fair_value_median": "236.47",
  "fair_value_mean": "191.80",
  "dispersion": "1.18",
  "ratio_price_to_fair_value": "1.26",
  "current_price": "298.87",
  "n_methods": 3,
  "methods": {
    "vs_historical_5y": { "available": true,  "fair_value": "236.47", "details": { "calculation_detail": {...} } },
    "vs_sector":        { "available": false, "fair_value": null,     "reason": "Méthode 2 V2 (FMP)" },
    "graham_number":    { "available": true,  "fair_value": "28.94",  "details": { "calculation_detail": {...} } },
    "analyst_target":   { "available": true,  "fair_value": "310.0",  "details": { "calculation_detail": {...} } }
  },
  "source": "computed",
  "reason": null,
  "warnings": []
}
```

### `GET /api/v1/synthesis/{symbol}`

**Schema** : `SynthesisReport`

```jsonc
{
  "symbol": "AAPL",
  "overall_verdict": "REQUIRES_REVIEW", // INVESTABLE | NOT_INVESTABLE | REQUIRES_REVIEW | BLOCKED
  "overall_label": "À VÉRIFIER MANUELLEMENT",
  "halal":         { "verdict": "PASS", "label": null, "available": true, "error": null },
  "investissable": { "verdict": "OUI",  "label": "QUALITÉ EXCELLENTE", "available": true, "error": null },
  "valuation":     { "verdict": "NON",  "label": "SURÉVALUÉE", "available": true, "error": null },
  "warnings": [],
  "errors": [],
  "computed_at": "2026-05-14T08:30:00Z"
}
```

**Cascade** : cf. `00-OVERVIEW.md` §3 P1.

### `GET /api/v1/portfolio/positions`

**Schema** : `list[PositionRead]`

```jsonc
[
  {
    "id": "uuid",
    "symbol": "AAPL",
    "currency": "USD",
    "opened_at": "2025-05-01T00:00:00Z",
    "quantity": "10",
    "avg_cost": "237.42",
    "cost_basis": "2374.20",
    "last_price": "298.87",
    "price_source": "live",        // live | transaction | unavailable
    "current_value": "2988.70",
    "unrealized_pnl": "614.50",
    "realized_pnl": "0",
    "transactions_count": 1
  }
]
```

`last_price` est issue de YFinance si disponible, sinon retombe sur le dernier prix de transaction (`price_source` indique l'origine).

### `GET /api/v1/portfolio/summary`

**Schema** : `PortfolioSummary`

```jsonc
{
  "total_invested": "4001",
  "current_value": "4520",
  "unrealized_pnl": "519",
  "realized_pnl": "200",
  "total_pnl": "719",
  "pnl_pct": "0.1797",
  "n_positions": 2,
  "n_transactions": 3,
  "currency": "USD"
}
```

### `POST /api/v1/portfolio/transactions`

**Body** : `TransactionCreate`
```jsonc
{
  "symbol": "AAPL",
  "currency": "USD",
  "date": "2025-05-14",
  "qty": "10",          // signed: positive=BUY, negative=SELL
  "price": "237.42",
  "fee": "0"
}
```

**Validation** :
- `symbol` 1-20 chars, auto-uppercase
- `currency` 3 chars, **rejeté si != "USD"** (400 `UnsupportedCurrencyError`)
- `qty != 0` (422 sinon)
- `price > 0` (422 sinon)
- `fee >= 0` (422 sinon)
- Oversell guard : 400 si `cumulative_qty + qty < 0`

**Response** : `TransactionRead` (201 Created)

### `DELETE /api/v1/portfolio/transactions/{tx_id}`

**Response** : 204 No Content
**Cascade** : si dernière tx d'une position, la Position est aussi supprimée.

### `GET /api/v1/calendar/{symbol}` (Étape 8 Phase C)

**Schema** : `CalendarReport`

```jsonc
{
  "symbol": "AAPL",
  "available": true,
  "next_earnings_date": "2026-08-01",
  "next_dividend_date": "2026-08-22",
  "ex_dividend_date":   "2026-08-15",
  "dividend_yield": 0.0044,
  "earnings_history": [
    { "quarter": "2024Q4", "eps_estimate": 1.5, "eps_actual": 1.6, "surprise_percent": 6.67 },
    { "quarter": "2024Q3", "eps_estimate": 1.2, "eps_actual": 1.1, "surprise_percent": -8.33 }
  ],
  "dividends": [
    { "date": "2024-11-15", "amount": 0.25 }
  ],
  "reason": null,
  "source": "yfinance"
}
```

Si YFinance ne répond pas → `available: false`, listes vides, `reason: "..."`.

### `GET /api/v1/management/{symbol}` (Étape 8 Phase C)

**Schema** : `ManagementReport`

```jsonc
{
  "symbol": "AAPL",
  "available": true,
  "officers": [
    {
      "name": "Tim Cook",
      "title": "CEO",
      "age": 63,
      "year_born": 1960,
      "total_pay": 16000000,
      "exercised_value": 0,
      "unexercised_value": 0,
      "fiscal_year": 2024
    }
  ],
  "reason": null,
  "source": "yfinance"
}
```

### `GET /api/v1/holders/{symbol}` (Étape 8 Phase C)

**Schema** : `HoldersReport`

```jsonc
{
  "symbol": "AAPL",
  "available": true,
  "major_holders": [
    { "label": "% of Shares Held by Insiders", "value": "0.07%" },
    { "label": "% of Shares Held by Institutions", "value": "61.5%" }
  ],
  "institutional_holders": [
    { "Holder": "Vanguard Group Inc.", "Shares": 1300000000, "Value": 380000000000, "% Out": 8.7, "Date": "2024-06-30" },
    { "Holder": "BlackRock Inc.",       "Shares": 1050000000, "Value": 310000000000, "% Out": 6.9, "Date": "2024-06-30" }
  ],
  "insider_transactions": [
    { "Date": "2024-10-15", "Insider": "COOK TIMOTHY D", "Transaction": "Sale", "Shares": 200000, "Value": 50000000 }
  ],
  "reason": null,
  "source": "yfinance"
}
```

## 3. Comportement transverse

### Aucun 5xx propagé en V1

Toute défaillance externe est convertie en :
- `verdict: "ERROR"` (Shariah / Financials)
- `available: false` (YFinance tabs / quality components / méthodes valuation)
- `overall_verdict: "REQUIRES_REVIEW"` + `errors: [...]` (Synthesis)

Le client reçoit **toujours 200** sauf :
- 401 (auth manquante ou mauvaise)
- 404 (transaction inconnue sur DELETE)
- 400 (validation Portfolio : currency, oversell)
- 422 (Pydantic validation Portfolio)

### Conventions de nommage

- Symbol normalisé en uppercase à l'entrée de chaque endpoint (`symbol.strip().upper()`)
- Path param avec `min_length=1, max_length=20` (anti-injection trivial)
- Tous les Decimal monétaires sérialisés en **string** (jamais float)
- Dates : ISO 8601 (`"2024-09-28"`)
- Datetimes : ISO 8601 UTC (`"2026-05-14T08:30:00Z"`)
