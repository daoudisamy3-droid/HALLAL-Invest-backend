# Limitation V1 — Méthodes de valuation (§4.3) partiellement disponibles

> Dernière mise à jour : 14 mai 2026 (post-étape 5 + 7).
> État : **3 méthodes LIVE sur 4** ; M2 (secteur) seul scaffold restant.

## Contexte

La spec §4.3 prévoit 4 méthodes de fair value pour l'onglet
**BIEN VALORISÉE ?**, agrégées en médiane avec mesure de dispersion :

| Méthode | Spec | Sources requises | Dispo V1 ? |
|---|---|---|---|
| 1 — Multiples vs historique 5y | §4.3.1 | SEC EDGAR EPS 5y + YFinance prix mensuels 5y | ✅ **P/E seulement** (P/S + EV/EBITDA differred) |
| 2 — Multiples vs secteur       | §4.3.2 | Peer group (FMP `/stock_peers`) + sector medians | ❌ scaffold V2 |
| 3 — Graham Number              | §4.3.3 | SEC EDGAR EPS + BVPS                            | ✅ |
| 4 — Target médian analystes     | §4.3.4 | YFinance `targetMedianPrice` (guard ≥ 5 analystes) | ✅ |

→ Au quotidien, 3 méthodes sur 4 contribuent au verdict pour un ticker
US-listed couvert par YFinance. Le seul scaffold restant est la méthode 2
(comparaison sectorielle), reportée V2 le temps d'intégrer une source
de peer groups.

## Conséquence sur le verdict global (post-Step 5)

La spec §4.3.5 exige un **minimum de 2 méthodes disponibles** pour
produire un verdict global cohérent. Avec 3 méthodes typiquement
disponibles en V1 (M1 + M3 + M4) :

- `verdict` : `OUI` / `OUI_NEUTRE` / `NON` / `INDÉTERMINÉ` (vraie cascade)
- `confidence` : `MEDIUM` (3 méthodes) — pourra monter `HIGH` quand M2 est intégrée
- `ratio_price_to_fair_value` : valeur réelle, calculée depuis `regularMarketPrice` YFinance
- `n_methods` : `3` (sera `4` post-intégration M2)
- `fair_value_median` / `fair_value_mean` : médian/moyenne sur les 3 méthodes
- `dispersion` : `(max − min) / median` — bornée par les 3 valeurs

Exemple réel observé sur AAPL (14 mai 2026) :
```
verdict = NON
label = SURÉVALUÉE
confidence = MEDIUM
n_methods = 3 (vs_historical_5y + graham_number + analyst_target)
fair_value_median = $236.47
current_price = $298.87
ratio_price_to_fair_value = 1.26
```

Cas où le verdict tombe `INDÉTERMINÉ` en V1 :
- Ticker **non-US** : SEC EDGAR ne couvre pas → toutes méthodes échouent
- **YFinance rate-limit / IP-block** : M1 + M4 + current_price tombent → seule M3 reste → `n_methods=1` < 2
- EPS / BVPS non-positifs : M3 elle-même devient `available=false`

Cf. `app/services/valuation_service._aggregate` pour la logique précise.

## Pourquoi Graham seul ne suffit pas — pertinent historiquement, plus une limitation V1 active

Le Graham Number est conservateur par design (cf. spec §4.3.3). Pour
les growth stocks (AAPL, NVDA…), il produit un fair value très en
dessous du prix de marché. Exemple :
- AAPL : EPS ≈ 6, BVPS ≈ 5 → Graham ≈ `sqrt(22.5 × 6 × 5)` ≈ $25.98
- Prix marché AAPL ≈ $300

C'est pourquoi la spec exige la combinaison de plusieurs méthodes. En
V1 actuelle (post-Step 5), M1 (multiples P/E 5y) et M4 (consensus
analystes) rééquilibrent Graham et permettent au verdict d'être réel
plutôt que "INDÉTERMINÉ par défaut conservateur" (l'ancienne posture
Step 4.5).

## Plug-in points restants V2

### Activation Méthode 2 — secteur peer-group (FMP integration)

**Données à fetcher** :
- FMP `/v3/stock_peers/{symbol}` → liste de peers (~10-30 tickers)
- Multiples actuels du ticker + de chaque peer
- Calculer la médiane sectorielle de P/E, P/S, EV/EBITDA
- `fair_value = current_price × (sector_median / ticker_value)` par multiple
- `fair_value` final = `median(fair_values)`

**Note spec §4.3.2** : "construction des peer groups par secteur est un
travail à part entière (V2 ou itération continue)".

**Effort estimé** : ~6-8h (FMP integration + sector classification +
median computation + tests). Dépend de l'obtention d'une clé API FMP.

**Plug-in code** : `app/services/valuation_service._method_vs_sector(facts)`
retourne actuellement `available=false` avec la raison "Méthode 2
(multiples vs médiane sectorielle) requiert l'intégration FMP". Le
contrat `MethodResult` est stable — remplir le corps de la fonction
suffit, le schéma de réponse ne change pas.

### Extension Méthode 1 — P/S et EV/EBITDA (SEC EDGAR + YFinance)

La V1 actuelle de M1 ne calcule que le **P/E historique** (prix
mensuels YFinance ÷ EPS annuel SEC). Deux multiples supplémentaires
sont attendus par la spec :

**P/S historique** :
- Nécessite shares-outstanding **trimestriel** historique (SEC
  `CommonStockSharesOutstanding` peut être extrait sur ~5 ans via
  `extract_n_year_annuals` étendu pour `fp` quarterly).
- Multiple = market_cap_per_month / revenue_TTM
- Effort : ~3h (extension `_method_vs_historical_5y` + tests).

**EV/EBITDA historique** :
- Nécessite EBITDA historique (SEC `OperatingIncomeLoss` +
  `DepreciationAndAmortization`) + cash + total debt.
- EV = market_cap + total_debt − cash
- Effort : ~4-5h (concept fetching + composition + tests).

Une fois ces deux multiples actifs, M1 produira la médiane des 3
multiples (P/E + P/S + EV/EBITDA) au lieu de juste P/E.

## Confidence des verdicts à terme

Mapping appliqué par `_confidence()` :

| n_methods | dispersion | confidence | Cas V1 ? |
|---|---|---|---|
| 4 | < 0.15  | HIGH    | post-activation M2 |
| 4 | 0.15-0.30 | MEDIUM | post-activation M2 |
| 4 | ≥ 0.30 | LOW     | post-activation M2 |
| 3 | quelconque | MEDIUM | **V1 nominal aujourd'hui** |
| 2 | quelconque | LOW   | V1 partiel (1 source YF down) |
| < 2 | n/a | N/A    | V1 dégradé (non-US ou YF totalement HS) |

## Mise à jour de ce document

Quand la Méthode 2 passe LIVE, retirer le scaffold du tableau "Sources
requises" et mettre à jour les exemples. Quand l'extension P/S +
EV/EBITDA de M1 est intégrée, retirer la note "P/E seulement". Quand
les 4 méthodes sont opérationnelles avec leurs 3 multiples chacune, ce
document peut être archivé.
