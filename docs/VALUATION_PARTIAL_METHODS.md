# Limitation V1 — Méthodes de valuation (§4.3) partiellement disponibles

## Contexte

La spec §4.3 prévoit 4 méthodes de fair value pour l'onglet
**BIEN VALORISÉE ?**, agrégées en médiane avec mesure de dispersion :

| Méthode | Spec | Sources requises | Dispo V1 ? |
|---|---|---|---|
| 1 — Multiples vs historique 5y | §4.3.1 | Live price + 5y prix + 5y P/E, P/S, EV/EBITDA | ❌ |
| 2 — Multiples vs secteur | §4.3.2 | Peer group (FMP `/stock_peers`) + sector medians | ❌ |
| 3 — Graham Number | §4.3.3 | EPS + BVPS (= StockholdersEquity / SharesOutstanding) | ✅ |
| 4 — Target médian analystes | §4.3.4 | YFinance ou Finnhub consensus | ❌ |

**Seule la méthode 3 (Graham Number) est calculable en V1** à partir des
données SEC EDGAR déjà intégrées (étape 2). Les 3 autres exigent des
sources non encore intégrées au backend.

## Conséquence sur le verdict global

La spec §4.3.5 exige un **minimum de 2 méthodes disponibles** pour
produire un verdict global cohérent. Avec une seule méthode (Graham) :

- `verdict` : `"INDÉTERMINÉ"`
- `confidence` : `"N/A"`
- `ratio_price_to_fair_value` : `null` (pas de prix live)
- `n_methods` : 1
- `fair_value_median` / `fair_value_mean` : valeur Graham
- `dispersion` : `null`
- `methods.graham_number` : **toujours exposé avec sa valeur réelle**
  pour servir de référence chiffrée conservatrice
- `warnings[]` : message explicite référent ce document

Cette transparence respecte le Principe 3 du master-prompt
(transparence des calculs) — l'utilisateur voit le Graham number ET la
raison de l'INDÉTERMINÉ global.

## Pourquoi Graham seul ne suffit pas (et c'est OK)

Comme la spec §4.3.3 le mentionne explicitement, le **Graham Number
est conservateur**. Calibré pour des entreprises mûres value, il donne
systématiquement un fair value **inférieur** au prix de marché pour les
growth stocks (AAPL, MSFT, NVDA…). Exemple typique :

- AAPL : EPS ≈ 6, BVPS ≈ 5 → Graham ≈ `sqrt(22.5 × 6 × 5)` ≈ **$25.98**
- Prix marché AAPL ≈ $235

Le Graham seul classerait toute action croissance comme "fortement
surévaluée" — c'est pourquoi la spec exige la combinaison avec 3 autres
méthodes (historique, secteur, analystes) qui rééquilibrent le verdict.

D'où le choix V1 d'afficher Graham mais de **ne pas conclure de
verdict** (INDÉTERMINÉ) plutôt que d'émettre une opinion biaisée.

## Plug-in points pour les 3 méthodes futures

Le code de `app/services/valuation_service.py` contient des stubs
documentés pour chaque méthode indisponible. Quand les sources seront
intégrées, le refactor consiste à **remplir le corps de la fonction
sans toucher au schéma de réponse**.

### Méthode 1 — `_method_vs_historical_5y(facts)` — YFinance integration

**Données à fetcher** :
- Current price (e.g. `yfinance.Ticker(symbol).info["currentPrice"]`)
- 5 ans de prix journaliers + EPS / Revenue / EBITDA annuel
- Calculer historical P/E médian, P/S médian, EV/EBITDA médian
- Pour chaque multiple : `fair_value = current_price × (historical_median / current_value)`
- `fair_value` final = `median(fair_values)` des multiples disponibles

**Effort estimé** : ~4h (intégration YFinance + retries + cache + tests).

**Étape prévisible** : 4.6 ou inclusion dans 5 (Portfolio Analytics
partie 2 — qui dépend déjà de YFinance pour les prix marché).

### Méthode 2 — `_method_vs_sector(facts)` — FMP integration

**Données à fetcher** :
- FMP `/v3/stock_peers/{symbol}` → liste de peers (~10-30 tickers)
- Multiples actuels du ticker + de chaque peer
- Calculer la médiane sectorielle de P/E, P/S, EV/EBITDA
- `fair_value = current_price × (sector_median / ticker_value)` par multiple
- `fair_value` final = `median(fair_values)`

**Note spec §4.3.2** : "construction des peer groups par secteur est un
travail à part entière (V2 ou itération continue)".

**Effort estimé** : ~6-8h (FMP integration + sector classification +
median computation + tests).

**Étape prévisible** : 7 (raffinements) ou itération continue.

### Méthode 4 — `_method_analyst_target(facts)` — YFinance ou Finnhub

**Données à fetcher** :
- YFinance: `Ticker.info["targetMedianPrice"]`, `targetLowPrice`,
  `targetHighPrice`, `numberOfAnalystOpinions`
- Ou Finnhub: `/stock/price-target` (`targetMedian`, `targetLow`,
  `targetHigh`, `numberOfAnalysts`)
- `fair_value = targetMedian` si `numberOfAnalysts >= 5`

**Effort estimé** : ~2h (intégration YFinance ou Finnhub minimal).

**Étape prévisible** : 4.6 ou 5 (si YFinance déjà intégré pour méthode 1).

## Confidence des verdicts à terme

Une fois les 4 méthodes intégrées, le confidence sera mappé sur :

| n_methods | dispersion | confidence |
|---|---|---|
| 4 | < 0.15 | HIGH |
| 4 | 0.15 - 0.30 | MEDIUM |
| 4 | ≥ 0.30 | LOW |
| 3 | quelconque | MEDIUM |
| 2 | quelconque | LOW |
| < 2 | n/a | N/A (cas V1 actuel) |

## Mise à jour de ce document

Quand une méthode passe disponible, retirer sa ligne du tableau
"Sources requises" et mettre à jour la section "Conséquence sur le
verdict global". Quand les 4 méthodes sont opérationnelles, ce document
peut être archivé / supprimé.
