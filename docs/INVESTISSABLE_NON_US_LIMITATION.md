# Limitation V1 — INVESTISSABLE pour les tickers non-US

## Contexte

L'endpoint `GET /api/v1/investissable/{symbol}` (étape 4) repose
intégralement sur SEC EDGAR (spec §3.2.2) pour les calculs financiers :

- **Gate Altman Z''** : Working Capital, Retained Earnings, Operating
  Income, Stockholders Equity, Total Liabilities, Total Assets
- **Gate Fraud (3 signaux)** : Operating Cash Flow, CapEx, Net Income,
  Accounts Receivable, Revenue, ainsi que les filings restatement
- **Composante A Piotroski** : NetIncome, ROA, OCF, GrossMargin,
  Shares, CurrentRatio, LongTermDebt, AssetTurnover sur 2 ans
- **Composante B Growth** : Revenue / EPS / FCF CAGR 3 ans
- **Composante D Capital allocation** : Shares outstanding 3y, FCF 3y,
  Buybacks 3y, CapEx + R&D / Revenue

SEC EDGAR ne couvre **que les actions cotées aux États-Unis**. Pour
tout ticker non-US (`AIXA.DE`, `RIO.L`, ADR sans filings SEC, etc.) :

- `financials_service.get_facts_payload` retourne `None` (le ticker
  n'est pas dans `company_tickers.json` de la SEC).
- Aucun gate ni composante du SCORE n'est calculable.

## Comportement en V1

- **Verdict** : `INCERTAIN` systématique pour les tickers non-US.
- **`data_completeness`** : `INSUFFICIENT`.
- **`gates`** : uniquement le gate AAOIFI (étape 1, Halal Terminal) est
  remonté ; les gates Altman + Fraud sont absents.
- **`quality_components`** : `{}` (aucune composante évaluée).
- **`warnings`** : liste contenant un message explicite référent ce doc.

L'utilisateur garde donc le verdict halal de l'étape 1, mais perd
l'analyse fondamentale. **C'est une limitation connue et assumée**.

## Tickers du portefeuille de l'utilisateur impactés (avril 2026)

- `AIXA.DE` (Aixtron, Allemagne) → `INCERTAIN` en V1
- Tout futur ajout non-US

`AAPL`, `MSFT`, `NVDA`, `AMZN`, `EOG`, `RIO` (listing US) : intégralement
supportés.

## Plan de résolution

### Option A — Intégration FMP global financials (étape 5 ou 7)

[Financial Modeling Prep](https://site.financialmodelingprep.com/) expose
les états financiers d'actions internationales (~70 000 tickers couverts
selon le plan). Coût : 14-50 $/mois selon le plan.

Refacto requis :
- Nouveau `app/integration/fmp_client.py`
- `financials_service.get_facts_payload` essaie d'abord SEC, puis FMP en
  fallback, et normalise les payloads dans un format commun (mapping
  GAAP-tags → champs FMP).
- Score components testés avec données FMP pour valider la cohérence des
  calculs sur AIXTRON / RIO.

Estimation : ~8h.

### Option B — Intégration YFinance financials

[YFinance](https://github.com/ranaroussi/yfinance) (gratuit, scraping
non-officiel) expose `Ticker.financials` / `.balance_sheet` / `.cashflow`
pour la plupart des actions internationales.

Risques : Yahoo a bloqué l'IP en 2023 et 2024 ; instabilité possible. À
combiner avec FMP / SEC en fallback en cascade.

### Option C — Limiter explicitement le scope FinTerminal aux US

Décision méta : si l'utilisateur fait du DCA sur des ETFs / actions US
uniquement, on n'investit pas dans l'intégration non-US et on documente
la limite dans l'UI ("FinTerminal V1 supporte uniquement les actions
cotées aux US").

## Décision à prendre

Le choix entre A / B / C est **différé à l'étape 7 (raffinements)** ou
plus tôt si un besoin métier critique se présente (par ex. l'utilisateur
veut investir activement sur Aixtron et a besoin du verdict SCORE).

Ce document doit être mis à jour ou supprimé lors de l'application
d'une des trois options.
