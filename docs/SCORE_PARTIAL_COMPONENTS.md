# Limitation V1 — Composantes SCORE non calculables

## Contexte

La spec §4.2.2 prévoit 5 composantes pour le score qualité de l'onglet
INVESTISSABLE :

| Composante | Poids | Sources | Disponible V1 ? |
|---|---|---|---|
| A. Piotroski F-Score | 30 % | SEC EDGAR | ✅ |
| B. Croissance pluri-annuelle (CAGR) | 25 % | SEC EDGAR | ✅ |
| C. Smart money (institutionnels + insiders + short) | 20 % | YFinance + Finnhub | ❌ |
| D. Capital allocation (buybacks + intensité d'investissement) | 15 % | SEC EDGAR | ✅ |
| E. Stabilité des bénéfices (beat rate trimestriel) | 10 % | Finnhub estimates | ❌ |

Les composantes **C** (Smart Money) et **E** (Earnings Stability)
exigent des sources non encore intégrées au backend FinTerminal :

- **YFinance** : détention institutionnelle, structure du float, beta,
  short interest. Implémentation prévue à l'étape 5 (Portfolio
  Analytics partie 2, validation croisée SEC vs YFinance pour les
  données critiques §3.3).
- **Finnhub** : insider transactions (`mspr` = monthly share purchase
  ratio), analyst estimates / actuals trimestriels pour le beat rate.
  Pas de plan d'intégration prioritaire.

## Comportement en V1

Conformément au mécanisme prévu par la spec elle-même (§4.2.2 dernier
paragraphe) :

- Les composantes C et E retournent `score: None`, `available: false`,
  avec dans `details.reason` une référence vers ce document.
- Le score qualité pondéré **renormalise les poids sur les composantes
  disponibles** :

  ```text
  weights_available = {piotroski: 0.30, growth: 0.25, capital_allocation: 0.15}
  total_weight     = 0.70
  score            = Σ (w_i × score_i) / total_weight
  ```

- `data_completeness` passe à `"PARTIAL"` (3 composantes / 5).
- Le payload renvoie 2 warnings explicites dans `warnings[]` pour
  surfacer la limitation à l'UI.

## Impact sur la précision du verdict

Sans Smart Money et Earnings Stability, on perd :

- L'angle "validation par les capitaux institutionnels et insiders"
  (les detenteurs informés sortent-ils de la position ?).
- L'angle "qualité prévisionnelle des bénéfices" (l'entreprise tient-elle
  ses guidances ?).

L'analyse reste solide sur les fondamentaux purs (Piotroski + Growth +
Capital) mais le verdict est légèrement plus conservateur — un
ticker avec d'excellents fondamentaux mais une fuite institutionnelle
agressive obtiendra le même score qu'un ticker similaire avec une
détention institutionnelle stable. C'est une limitation acceptée pour
V1.

## Plan de réintégration

### Étape 5 — Smart Money via YFinance

Le master-prompt liste YFinance comme une dépendance étape 5 (validation
croisée). Au moment de l'intégrer :

1. Créer `app/integration/yfinance_client.py` (wrapper async sur
   `yfinance` lib avec gestion 429 / blocages IP).
2. Implémenter `app/services/smart_money.py` :
   - C1 : `institutional_holdings_change_12m` via `Ticker.institutional_holders`
   - C2 : insider mspr → fallback YFinance `Ticker.insider_transactions` ou Finnhub
   - C3 : `short_interest_ratio` via `Ticker.info["shortRatio"]` ou similaire
3. Brancher dans `investissable_service` en remplacement du None actuel.
4. Tests respx + scenarios crafted (3 sub-signaux × seuils).

Estimation : ~6h.

### Étape 7 (ou plus tôt) — Earnings Stability via Finnhub

1. Créer `app/integration/finnhub_client.py`.
2. Endpoint Finnhub `/stock/earnings` retourne `{actual, estimate}` par
   trimestre.
3. `app/services/earnings_stability.py` : compute beat rate sur 8 trimestres.

Plan tier Finnhub : free tier 60 calls/min — largement suffisant pour
usage perso. Estimation : ~3h.

## Décision

L'utilisateur a explicitement validé en plan étape 4 (Q3) que les
composantes C et E restent `None` en V1 avec rebalancing automatique.
La réintégration se fera dans le cadre de l'étape 5 (YFinance) puis
éventuellement étape 7 (Finnhub) selon priorisation.

Ce document doit être mis à jour quand l'une ou l'autre composante
revient en service.
