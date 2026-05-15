# 09 — Gestion d'erreurs et fail-graceful

## 1. Principe directeur

**Aucun endpoint d'analyse ne propage les pannes externes en 5xx.**

Le client HTTP reçoit toujours :
- **200** avec verdict dégradé / `available=false` dans le body
- **401** si auth manquante / mauvaise
- **404** pour les routes inconnues (rare)
- **400** / **422** pour validations Portfolio uniquement

## 2. Hiérarchie d'exceptions

```python
# app/core/exceptions.py

class ExternalAPIError(Exception):
    """Base pour toute panne d'intégration externe."""

class HalalTerminalError(ExternalAPIError):
    """Halal Terminal API indisponible ou inattendue."""

class HalalTerminalNotCovered(HalalTerminalError):
    """HTTP 404 — provider ne couvre pas le symbol."""

class SECEdgarError(ExternalAPIError):
    """SEC EDGAR indisponible ou format inattendu."""

class SECEdgarNotFound(SECEdgarError):
    """SEC EDGAR 404 — CIK ou facts inconnus."""

class YFinanceError(ExternalAPIError):
    """YFinance fail (rate-limit, IP-block, lib breakage)."""
```

Les clients HTTP raise ces exceptions ; les services catchent et convertissent en `verdict: "ERROR"` ou `available: false`.

## 3. Stratégies par source

### Halal Terminal

Spec §3.4 + master-prompt §5.3 : **NO fallback** sur la gate AAOIFI (non-substituable).

| Cas                              | Service action                                                       |
|----------------------------------|----------------------------------------------------------------------|
| HTTP 200 + aggregate=true        | `verdict: "PASS"`, cache 7j                                          |
| HTTP 200 + at least one false    | `verdict: "FAIL"`, cache 7j                                          |
| HTTP 200 + all null              | `verdict: "ERROR"`, **pas** caché                                    |
| HTTP 404 (ticker_unknown)        | `verdict: "NOT_COVERED"`, **pas** caché                              |
| HTTP 5xx / timeout / network     | `verdict: "ERROR"`, **pas** caché                                    |

Cascade synthesis :
- `FAIL` → `BLOCKED` (hard stop éthique)
- `ERROR` / `NOT_COVERED` → `REQUIRES_REVIEW` + warning

### SEC EDGAR

Spec master-prompt §5.4 : fallback YFinance + FMP envisagé V2. V1 : retourne `verdict: "ERROR"` ou `verdict: "NOT_COVERED"` dans le body.

| Cas                                       | Service action                                       |
|-------------------------------------------|------------------------------------------------------|
| HTTP 200 + facts complets                  | `verdict: "AVAILABLE"`, cache 24h                    |
| HTTP 200 + facts vides / mal-formés        | `verdict: "ERROR"` + raison, **pas** caché           |
| HTTP 404 (CIK inconnu, non-US)            | `verdict: "NOT_COVERED"`, **pas** caché              |
| HTTP 5xx / timeout                         | raise `SECEdgarError` → service catch → `verdict: "ERROR"` |
| Anchor revenues absent                     | `verdict: "ERROR"` (impossible de remplir metadata)   |

Tenacity retry : 3 attempts + exponential backoff (0.5..2.0s) sur transient (5xx, timeout, network). 404 retourne `None` direct.

### YFinance

**Fail-graceful strict** : le client ne raise jamais (sauf bugs internes), retourne `None`.

| Cas                                         | Client action                                         |
|---------------------------------------------|--------------------------------------------------------|
| Yahoo HTTP 200 + dict utile                  | retourne dict                                         |
| Yahoo HTTP 200 + `{}` (rate-limit silencieux) | retry × 3, puis retourne `None`                       |
| Yahoo timeout                                | retry × 3, puis retourne `None`                       |
| Yahoo HTTP 4xx / 5xx                          | retry × 3, puis retourne `None`                       |
| Lib yfinance breaking                         | catch tout `Exception`, retry × 3, puis retourne `None`|

Service `yfinance_service` propage le `None` :
- `portfolio_service` → `price_source: "transaction"` (fallback prix de transaction)
- `valuation_service` M1 + M4 → `available: false`
- `/calendar`, `/management`, `/holders` → `available: false` + raison

### Halal Terminal Database

Halal Terminal lui-même peut avoir un cache stale interne. Hors de notre contrôle. Le V1 fait confiance au provider.

## 4. Stratégies par service

### `shariah_service.screen_with_personal_thresholds`

```python
try:
    cached = await _read_cache(db, sym)
    if cached is not None:
        return cached
    upstream = await client.screen(sym)
    report = _build_report_from_upstream(sym, upstream)
    if report.verdict in ("PASS", "FAIL"):
        await _persist(db, sym, report)
    return report
except HalalTerminalNotCovered:
    return ShariahReport(verdict="NOT_COVERED", ...)
except HalalTerminalError as exc:
    logger.warning("shariah upstream error: %s", exc)
    return ShariahReport(verdict="ERROR", reason=str(exc))
```

### `investissable_service.compute_investissable`

```python
# Gate 1 AAOIFI
shariah = await shariah_service.screen_with_personal_thresholds(sym, db, halal_client)
gate_aaoifi = _aaoifi_gate_from_shariah(shariah)
if gate_aaoifi.verdict == "FAIL":
    return _shortcircuit_verdict(verdict="NON", blocked_at="aaoifi", ...)

# SEC EDGAR
try:
    facts_tuple = await financials_service.get_facts_payload(sym, db, sec_client)
except SECEdgarError:
    return _shortcircuit_verdict(verdict="INCERTAIN", ...)
if facts_tuple is None:
    return _shortcircuit_verdict(verdict="INCERTAIN", reason="non-US", ...)

# Gates 2-3 + Quality components
# ... chaque calcul retourne available=true/false individuellement, jamais raise.
```

### `valuation_service.compute_valuation`

```python
try:
    facts_tuple = await financials_service.get_facts_payload(...)
except SECEdgarError as exc:
    return _indetermine_response(reason=f"SEC EDGAR indisponible: {exc}", ...)
if facts_tuple is None:
    return _indetermine_response(reason="non-US ticker", ...)

# YFinance fail-graceful — info / history peuvent être None
if yfinance_client is not None:
    info = await yfinance_service.get_info(sym, db, yfinance_client)         # None possible
    history = await yfinance_service.get_history(sym, db, yfinance_client)   # None possible
    current_price = _extract_current_price(info)                              # None possible

# 4 méthodes traitent None en local
methods = {
    "vs_historical_5y": _method_vs_historical_5y(facts, history, current_price),
    "graham_number":    _method_graham_number(facts),
    "analyst_target":   _method_analyst_target(info),
    "vs_sector":        _method_vs_sector(facts),
}
agg = _aggregate(methods, current_price)
return ValuationReport(... verdict=agg["verdict"] ...)
```

### `synthesis_service.compute_synthesis`

Wrapper `_safe(coro, layer_name)` :

```python
async def _safe(coro, layer_name):
    try:
        result = await coro
        return (result, None)
    except Exception as exc:  # catch-all intentionnel
        logger.warning("synthesis layer=%s raised err=%s", layer_name, exc)
        return (None, f"{type(exc).__name__}: {exc}"[:200])
```

Une exception dans n'importe quelle branche `asyncio.gather` est convertie en `available: false` + `error: "..."` sur la couche concernée. Les 2 autres couches continuent. `overall_verdict` tombe sur `REQUIRES_REVIEW` si une couche est down.

### `portfolio_service`

Exceptions métier (raise → endpoint mappe en HTTP 400/404) :

```python
class PortfolioError(Exception): ...
class UnsupportedCurrencyError(PortfolioError): ...  # 400
class OversellError(PortfolioError): ...              # 400
class TransactionNotFound(PortfolioError): ...        # 404
```

Endpoint :
```python
@router.post("/transactions")
async def create_transaction(payload, db):
    try:
        return await portfolio_service.create_transaction(db, payload)
    except portfolio_service.UnsupportedCurrencyError as exc:
        raise HTTPException(400, detail=str(exc))
    except portfolio_service.OversellError as exc:
        raise HTTPException(400, detail=str(exc))
```

YFinance fail dans `list_positions` → `price_source: "transaction"` (fallback transparent), pas d'erreur remontée.

## 5. Auth (verify_api_key)

```python
async def verify_api_key(x_auth_token: str | None = Header(...)):
    if x_auth_token != settings.FINTERMINAL_API_KEY:
        raise HTTPException(401, detail="Invalid or missing X-Auth-Token")
```

Toutes les routes `/api/v1/` sont gardées. `/health` + `/openapi.json` sont hors router.

## 6. Validation Pydantic (Portfolio uniquement)

```python
class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: Annotated[str, Field(min_length=1, max_length=20)]
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "USD"
    date: date
    qty: Decimal
    price: Annotated[Decimal, Field(gt=Decimal("0"))]
    fee: Annotated[Decimal, Field(ge=Decimal("0"))] = Decimal("0")

    @field_validator("qty")
    def _qty_non_zero(cls, v):
        if v == Decimal("0"):
            raise ValueError("qty must be non-zero")
        return v
```

FastAPI émet **422 Unprocessable Entity** sur échec validation. Pour les autres endpoints, le `Path(..., min_length=1, max_length=20)` empêche les symbols mal-formés.

## 7. Logging structuré

Niveau INFO :
- cache hits / cache miss + fresh fetch / persistence success
- service entry / exit avec ticker
- yfinance / sec_edgar 200 + body_size

Niveau WARNING :
- yfinance / sec_edgar transient errors avant retry
- cache miss + provider returns None
- per-layer synthesis fail caught by `_safe`

Niveau ERROR :
- non-2xx définitif HTTP (sec_edgar 5xx après retries)
- malformed JSON / unexpected payload shape
- service layer unexpected exceptions

Aucun log ne contient :
- la clé API (`X-Auth-Token`)
- les headers d'auth complets
- des PII utilisateur (le projet est mono-user)

## 8. Tests de fragilité (Phase A étape 5 + Phase A étape 8)

10+ tests fragility couvrent les cas dégradés :

| Test                                                                  | Couche |
|-----------------------------------------------------------------------|--------|
| `test_get_info_exhausts_retries_returns_none`                         | yfinance_client |
| `test_get_info_empty_dict_treated_as_transient`                        | yfinance_client |
| `test_get_info_stub_without_price_field_returns_none`                  | yfinance_client |
| `test_get_info_timeout_returns_none`                                   | yfinance_client |
| `test_synthesis_layer_crash_returns_200_with_errors_in_body`           | synthesis_endpoint |
| `test_all_three_layers_raise_yields_requires_review_with_errors`      | synthesis_service |
| `test_single_layer_exception_is_contained`                            | synthesis_service |
| `test_each_branch_gets_an_isolated_session`                           | synthesis_service (regression hotfix Étape 7.1A) |
| `test_calendar_returns_200_with_available_false_when_yfinance_fails` | yfinance_tabs_endpoints |
| `test_management_returns_unavailable_on_empty`                         | yfinance_tabs_endpoints |

## 9. Comportement Frontend en cas d'erreur

Pattern useEffect uniforme :

```tsx
try {
    const data = await fetchX(sym)
    if (!cancelled) setState({ status: 'success', data })
} catch (e) {
    if (cancelled) return
    setState({ status: 'error', message: e instanceof Error ? e.message : String(e) })
}
```

Rendu `<ErrorBlock>` :
- role="alert" pour a11y
- Couleur rouge (`#f87171`) + bordure
- Préfixe `✗ Erreur d'appel`
- Message brut sous-titré

`<UnavailableBlock>` (état dégradé, pas une erreur HTTP) :
- role="status" pour a11y
- Couleur ambre (`#fbbf24`)
- Préfixe `⚠ Données indisponibles`
- Raison du backend en sous-titre

Distinction nette entre :
- **Erreur HTTP** (401, 5xx réel, network) → `<ErrorBlock>` rouge
- **Donnée dégradée** (verdict ERROR / available=false) → `<UnavailableBlock>` ambre
