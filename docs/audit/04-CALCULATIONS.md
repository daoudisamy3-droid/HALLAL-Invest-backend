# 04 — Formules et modules de calcul

Chaque module retourne un `dataclass` enrichi d'un champ
`calculation_detail` (Étape 8 Phase B) qui contient :

```python
{
    "formula":           str,                          # littéral
    "variables":         dict[str, str | None],        # substitution Decimal-safe
    "intermediates":     dict[str, str | None],        # ratios mid-flight
    "computation_steps": list[str],                    # lignes hand-calc
    "result":             str | None,
    "thresholds":         list[{label, condition}],
    "interpretation":    str | None,                   # phrase verdict
}
```

Cette payload est exposée au frontend dans `gate.details["calculation_detail"]`,
`quality_component.details["calculation_detail"]`, et
`method.details["calculation_detail"]`.

---

## 1. Altman Z'' (faillite) — `app/services/altman.py`

### Formule

```
Z'' = 6.56 · (WC / TA)
    + 3.26 · (RE / TA)
    + 6.72 · (EBIT / TA)
    + 1.05 · (BV / TL)
```

### Variables

| Symbole | Source SEC                                       | Description |
|---------|--------------------------------------------------|-------------|
| WC      | `current_assets − current_liabilities`            | Working capital |
| RE      | `RetainedEarningsAccumulatedDeficit`             | Bénéfices non distribués |
| EBIT    | `OperatingIncomeLoss` (V1 proxy)                  | Earnings before interest & taxes |
| BV      | `StockholdersEquity`                             | Book value |
| TL      | `Liabilities`                                    | Total liabilities |
| TA      | `Assets`                                         | Total assets |

### Seuils & verdict

| Z'' | Zone     | Verdict gate           |
|-----|----------|------------------------|
| ≥ 2.6 | SAFE     | PASS                   |
| ∈ [1.1, 2.6) | GREY     | WARNING (gate passé + bannière orange) |
| < 1.1 | DISTRESS | FAIL (court-circuit `investissable = NON`) |
| données manquantes | N/A | INSUFFICIENT_DATA |

### Note V1

EBIT proxy via `OperatingIncomeLoss` (SEC GAAP n'expose pas EBIT direct). Divergence typique < 5 % pour les non-financières.

### Note V1bis (Z'' uniforme)

Le Z classique (5 facteurs avec market cap) n'est pas implémenté V1 (pas de prix live au moment de l'écriture). Z'' (4 facteurs) est appliqué uniformément à toutes les entreprises. La spec décrit Z'' comme "variant pour entreprises non-manufacturières", on l'élargit en V1.

---

## 2. Piotroski F-Score — `app/services/piotroski.py`

### Formule

```
Score = (raw / N_évalué) × 100
```

avec `raw` = nombre de critères passés (parmi `N_évalué` ≤ 9).

### Les 9 critères (1 point chacun)

| # | Critère                                                       |
|---|---------------------------------------------------------------|
| 1 | NetIncome > 0                                                  |
| 2 | ROA = NetIncome / TotalAssets > 0                              |
| 3 | OperatingCashFlow > 0                                          |
| 4 | OCF > NetIncome (earnings quality)                             |
| 5 | ΔLongTermDebt < 0 (deleveraging YoY)                           |
| 6 | ΔCurrentRatio > 0 (current_assets / current_liabilities)       |
| 7 | shares_outstanding_y0 ≤ shares_outstanding_y1 × 1.01 (no equity raise) |
| 8 | ΔGrossMargin > 0                                               |
| 9 | ΔAssetTurnover > 0 (revenue / total_assets)                    |

### Garde minimum

Si `N_évalué < 6` → `available=false`, score `None`, la pondération 30 % est redistribuée sur les autres composantes (cf. `investissable_service`).

### Seuils interprétation

| Score | Label    |
|-------|----------|
| ≥ 78 (≥ 7/9) | STRONG   |
| 55-78 | AVERAGE  |
| < 55  | WEAK     |
| None  | N/A      |

---

## 3. Growth CAGR — `app/services/growth.py`

### Formule

```
CAGR = (end / start)^(1/n) - 1
```

Décimal-safe via `Decimal.ln() / Decimal.exp()` (Decimal n'accepte pas exposant Decimal directement).

Garde signes : retourne `None` si `start == 0`, `end == 0`, ou signes différents (CAGR indéfini lors d'un changement de signe).

### Sous-métriques (CAGR 3 ans)

| Sous-score      | Source SEC                                  |
|------------------|---------------------------------------------|
| Revenue CAGR     | `revenues` série 4 ans                       |
| EPS CAGR         | `eps_diluted` série 4 ans                    |
| FCF CAGR         | `(OCF − CapEx)` série 4 ans (alignée par period_end) |

### Barème par sous-score

| CAGR     | Sous-score |
|----------|------------|
| ≥ 15 %   | 100        |
| ≥ 10 %   | 80         |
| ≥ 5 %    | 60         |
| ≥ 0 %    | 40         |
| ≥ -5 %   | 20         |
| < -5 %   | 0          |

### Agrégation

`score_final = mean(sous-scores disponibles)`. Sous-scores `None` exclus.

---

## 4. Fraud — `app/services/fraud.py`

### Gate verdict

Si **2 sur 3 signaux positifs** → gate FAIL → court-circuit `investissable = NON` + `blocked_at: "fraud"`.

### Signal 1 — FCF/NI quality (3 ans)

```
ratio_3y = Σ FCF(y0..y-2) / Σ NetIncome(y0..y-2)
         où FCF = OCF − CapEx (CapEx aligned par period_end)
```

| Cas | Verdict |
|-----|---------|
| Σ NI ≤ 0 | False (clean by default — ratio non significatif) |
| SIC ∈ EXEMPT_SIC_RANGES | False (capital-intensive exempt — §4.2.1) |
| ratio < 0.5 | True (alerte fraude) |
| ratio ≥ 0.5 | False (clean) |
| n_ocf_years < 3 ou n_ni_years < 3 | None (insuffisant) |

### Signal 2 — Receivables growth anomaly (CAGR 2y)

```
ratio = CAGR2y(Receivables) / CAGR2y(Revenue)
```

| Cas | Verdict |
|-----|---------|
| CAGRs indéfinis (sign change, start=0) | None |
| Revenue CAGR ≤ 0 | False (anomaly check non significatif en déclin) |
| ratio > 2.0 | True (alerte : créances accélèrent vs revenus) |
| ratio ≤ 2.0 | False (clean) |

### Signal 3 — Restatements / late-filings (3 ans)

```
count = nombre de filings dans {10-K/A, 10-Q/A, NT 10-K, NT 10-Q}
        sur les 3 dernières années glissantes depuis aujourd'hui
```

| Cas | Verdict |
|-----|---------|
| submissions indisponible (non-US) | None |
| count ≥ 3 | True (alerte) |
| count < 3 | False (clean) |

### SIC exempt logic (Étape 8 Phase A guard)

```python
EXEMPT_SIC_RANGES = (
    (1000, 1499),  # Mining + Basic Materials + Energy extracted
    (4900, 4999),  # Utilities
    (6500, 6599),  # Real Estate
    (6700, 6799),  # Holding & Investment Offices (REITs at 6798)
)
```

Le test `test_exempt_sic_only_affects_fraud_signal_1_not_pipeline` garantit via `pathlib.rglob` qu'aucun callsite hors `fraud.py` / `sector.py` n'utilise `is_exempt_from_absolute_fcf_ni` (le pipeline reste complet pour les capital-intensive — Piotroski + Growth + Capital tournent normalement).

---

## 5. Capital Allocation — `app/services/capital_allocation.py`

Deux sous-signaux équi-pondérés (D1, D3). D2 (dividende) supprimé en v2.1 refactor.

### D1 — Buybacks responsables

```
reduction = (shares_3y_ago − shares_now) / shares_3y_ago
fcf_total = Σ FCF(y0..y-2)
buyback_total = Σ buybacks(y0..y-2)
fcf_covers = fcf_total ≥ buyback_total
```

| Cas                                                    | Score |
|--------------------------------------------------------|-------|
| reduction ≥ 5 % AND fcf_covers                          | 100   |
| reduction ≥ 5 % AND NOT fcf_covers                      | 60 (financé par dette) |
| 0 ≤ reduction < 5 %                                     | 60 (neutre) |
| reduction < 0                                           | 20 (dilution) |
| données insuffisantes                                   | None  |

### D3 — Intensité d'investissement

```
intensity = (CapEx + R&D) / Revenue  (latest annual)
```

V1 fallback absolu (Q4 plan, pas de médiane sectorielle disponible) :

| Intensity     | Score |
|---------------|-------|
| 5 % ≤ ii ≤ 30 % | 80   |
| ii < 5 %       | 50    |
| ii > 30 %      | 60    |
| Revenue = 0    | None  |

### Score Capital final

```
score = mean(d1, d3)  # None exclus
```

---

## 6. Valuation — `app/services/valuation_service.py`

### Méthode 1 — Multiples vs historique 5y (P/E only en V1)

```
P/E_t = close_mensuel_t / EPS_FY(t)  pour chaque barre yfinance 5y (60 barres)
historical_P/E_median = median(P/E_t)
fair_value = historical_P/E_median × latest_EPS
```

Garde : ≥ 12 observations P/E exploitables, latest_EPS > 0, n_eps_years ≥ 2.

**Limitation V1** : P/S et EV/EBITDA différés (nécessitent shares-outstanding trimestriel + EBITDA historique). Documenté `docs/VALUATION_PARTIAL_METHODS.md`.

### Méthode 2 — Multiples vs secteur

**V1 status** : scaffold (`available=false`). Nécessite intégration FMP `/stock_peers/{symbol}` pour construire le peer group, calculer P/E, P/S, EV/EBITDA médianes sectorielles. Reportée V2.

### Méthode 3 — Graham Number

```
Graham = sqrt(22.5 × EPS × BVPS)
       où BVPS = StockholdersEquity / SharesOutstanding
```

Garde : EPS > 0 ET BVPS > 0.

Calcul Decimal-exact via `Decimal.sqrt()`.

### Méthode 4 — Analyst Target

```
fair_value = YFinance.info["targetMedianPrice"]
```

Garde : `numberOfAnalystOpinions ≥ 5` (spec §4.3.4).

### Agrégation

```python
available_values = [m.fair_value for m in methods.values() if m.available]
n_methods = len(available_values)

if n_methods >= 1:
    fair_value_median = median(available_values)
    fair_value_mean   = sum(available_values) / n_methods

if n_methods >= 2:
    dispersion = (max(available_values) - min(available_values)) / fair_value_median
    if current_price:
        ratio = current_price / fair_value_median
```

### Verdict (FR strict)

| ratio price/FV | Verdict       | Label              |
|----------------|---------------|--------------------|
| < 0.85         | OUI           | SOUS-ÉVALUÉE       |
| ∈ [0.85, 0.95) | OUI           | JUSTE PRIX (LÉGÈRE DÉCOTE) |
| ∈ [0.95, 1.05) | OUI_NEUTRE   | JUSTE PRIX         |
| ∈ [1.05, 1.50) | NON           | SURÉVALUÉE         |
| ≥ 1.50         | NON           | FORTEMENT SURÉVALUÉE |
| ratio indéfini ou n_methods < 2 | INDÉTERMINÉ | null |

### Confidence

| n_methods | dispersion        | confidence |
|-----------|-------------------|------------|
| 4         | < 0.15            | HIGH       |
| 4         | 0.15 - 0.30       | MEDIUM     |
| 4         | ≥ 0.30            | LOW        |
| 3         | quelconque        | MEDIUM     |
| 2         | quelconque        | LOW        |
| < 2       | n/a               | N/A        |

V1 actuel : n_methods ≤ 3 (M2 scaffold) → confidence ≤ MEDIUM en pratique.

---

## 7. Sector classifier — `app/services/sector.py`

```python
@dataclass(frozen=True)
class SectorInfo:
    sic_code: int | None
    sic_description: str | None
    is_exempt_from_absolute_fcf_ni: bool
    exempt_reason: str | None
```

Source : `submissions.sicCode` (SEC EDGAR). Le `is_exempt_*` est consommé **uniquement** par `fraud._signal_fcf_quality` (verrouillé par test guard).

---

## 8. Score qualité agrégé — `investissable_service.py`

### Pondération (spec §4.2.2)

| Composante           | Poids nominal |
|----------------------|----------------|
| Piotroski            | 30 %           |
| Growth               | 25 %           |
| Smart Money          | 20 % (V1 stub) |
| Capital Allocation   | 15 %           |
| Earnings Stability   | 10 % (V1 stub) |

### Rebalancement

Les composantes `None` sont **exclues**, et les poids des composantes restantes sont **rebalancés** pour somme = 100 %.

Exemple V1 (Smart Money + Earnings Stability indisponibles) :
- Piotroski : 30 / (30+25+15) = 42.86 %
- Growth :    25 / 70 = 35.71 %
- Capital :   15 / 70 = 21.43 %

### Verdict Investissable

| quality_score | Verdict      | Label                |
|---------------|--------------|----------------------|
| ≥ 75          | OUI          | QUALITÉ EXCELLENTE   |
| 60-75         | OUI          | QUALITÉ BONNE        |
| 45-60         | OUI          | QUALITÉ ACCEPTABLE   |
| < 45          | NON          | QUALITÉ INSUFFISANTE |
| None (data insuffisante)  | INCERTAIN    | null                 |

Court-circuits préalables :
- Gate AAOIFI FAIL → NON `blocked_at: "aaoifi"`
- Gate Altman FAIL → NON `blocked_at: "altman"`
- Gate Fraud FAIL → NON `blocked_at: "fraud"`
- SEC non couvert (non-US) → INCERTAIN

---

## 9. P&L Portfolio — `portfolio_service.py`

Decimal-exact weighted-average cost, signed qty.

```
running_qty = 0
running_cost = 0  # cost basis des actions détenues (fees incl.)
realized_pnl = 0
last_price = None

pour chaque tx ordonnée par (date ASC, id ASC):
    si tx.qty > 0:  # BUY
        running_cost += tx.qty * tx.price + tx.fee
        running_qty  += tx.qty
    sinon:          # SELL (qty < 0)
        sell_qty     = -tx.qty
        avg_at_sell  = running_cost / running_qty
        cost_removed = avg_at_sell * sell_qty
        running_cost -= cost_removed
        running_qty  -= sell_qty
        realized_pnl += sell_qty * tx.price - cost_removed - tx.fee
    last_price = tx.price

# Live override
si yfinance_client fourni et quote disponible:
    last_price = yfinance_quote
    price_source = "live"

current_value = running_qty * last_price
unrealized_pnl = current_value - running_cost
```

5 scénarios canoniques testés Decimal-exact (sans `pytest.approx`) :
1. BUY simple
2. Weighted avg 2 buys
3. Partial sell + realized P&L
4. Sell with fee
5. Closed position (qty = 0)
