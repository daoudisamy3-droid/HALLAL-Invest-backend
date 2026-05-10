# FinTerminal — Spécification Métier Consolidée

> **Document de référence pour la construction**
> Version 2.1.1 · Mai 2026
> Ce document remplace `data-inventory.md` et devient la source unique de vérité pour la logique métier.
> Toute règle codée doit être traçable à une section de ce document.
>
> **Changelog v2.1** : harmonisation seuils DCA (zone 1.05-1.15 résolue), suppression D2 dividende, formule D3 corrigée (CapEx + R&D), normalisation sectorielle détecteur fraude, recalcul conviction à la réactivation, seuil minimum tranche DCA 25€, vente partielle 33% paramétrable et documentée, K=2 SL ATR multiplier paramétrable, correction positions réelles (RIO/EOG/AIXTRON, plus VOX).
>
> **Changelog v2.1.1** (ajustements pré-master prompt) : harmonisation `>= 2.0` pour règle 6 compute_synthesis (cohérence frontières), commentaire explicite OUI vs OUI_NEUTRE pour éviter confusion entre schémas, ajout `FINTERMINAL_API_KEY` pour auth API minimale anti-bot (cf. master-prompt §9.6).

---

## Table des matières

1. [Philosophie et ambition](#partie-1--philosophie-et-ambition)
2. [Ce qui est tué et pourquoi](#partie-2--ce-qui-est-tu%C3%A9-et-pourquoi)
3. [Stack data finale](#partie-3--stack-data-finale)
4. [Architecture des 3 onglets et bandeau de synthèse](#partie-4--architecture-des-3-onglets-et-bandeau-de-synth%C3%A8se)
5. [Logique AAOIFI personnelle](#partie-5--logique-aaoifi-personnelle)
6. [Logique fair value et DCA](#partie-6--logique-fair-value-et-dca)
7. [Paliers de conviction et allocation](#partie-7--paliers-de-conviction-et-allocation)
8. [Portfolio analytics partie 1 et partie 2](#partie-8--portfolio-analytics)
9. [Roadmap d'exécution en 7 étapes](#partie-9--roadmap-dex%C3%A9cution)
10. [Annexes techniques](#partie-10--annexes-techniques)

---

# PARTIE 1 — Philosophie et ambition

## 1.1 Profil utilisateur

FinTerminal est conçu pour un profil utilisateur précis. Toute décision technique doit être évaluée contre ce profil.

| Dimension              | Valeur                                                          |
|------------------------|-----------------------------------------------------------------|
| Âge                    | 23 ans                                                          |
| Expérience dev         | Débutant                                                        |
| Expérience finance     | Débutant motivé, lecture autodidacte                            |
| Budget mensuel DCA     | 150-250 €                                                       |
| Horizon                | 3-10 ans (pas de trading court terme)                           |
| Cadre éthique          | Halal AAOIFI strict + éthique d'investir où ça fait du bien     |
| Cible portefeuille     | 6-10 positions actives à terme                                  |
| Sweet spot positions   | 7-8 positions (sweet spot diversification + lisibilité)         |
| Patrimoine investi     | ~250 € début mai 2026, croissance organique                     |
| Tolérance volatilité   | Modérée, sensible aux drawdowns émotionnellement                |

Cette précision compte. Une fonctionnalité qui ne sert ni un débutant en finance (besoin de pédagogie), ni un investisseur DCA long terme (besoin de discipline), ni un cadre halal strict (besoin de gates bloquants), n'a pas sa place dans le produit.

## 1.2 Ambition produit (figée)

Trois ambitions, dans cet ordre de priorité :

**Ambition principale — Maintenir un taux de réussite supérieur à la moyenne retail sur 5 ans.**
Cible quantifiée : battre le S&P 500 de 2 à 4 points annualisés sur horizon glissant 5 ans. Pas plus. La promesse de "10 % au-dessus du marché" est un mensonge marketing, pas un objectif tenable.

**Ambition complémentaire — Réduire la volatilité psychologique du portefeuille.**
L'outil doit être un garde-fou comportemental. Ses calculs portfolio-level (corrélations, concentration sectorielle, exposition devise) servent à éviter une fausse diversification qui se révélerait sous stress.

**Ambition périphérique — Apprendre la finance par la pratique.**
L'apprentissage est un bonus de la construction et de l'usage, pas un objectif premier. Le terminal n'est pas une plateforme éducative, c'est un outil de décision. La lisibilité des analyses est privilégiée pour favoriser cet apprentissage indirect.

## 1.3 Principes directeurs

Cinq principes qui arbitrent toute décision de design ou de code.

### Principe 1 — Discipline DCA mécanique

Le DCA fonctionne précisément parce qu'il ne timise pas. Toute fonctionnalité qui pousse à reporter un DCA mensuel régulier est une régression, pas une amélioration. Le seul cas où on suspend le DCA sur une position : prix actuel > 1.0× fair value médian (voir partie 6).

Conséquence : pas de signal court terme dans le terminal. Pas de prédiction directionnelle J+1. Pas d'alerte "VIX trop haut, attendre la semaine prochaine pour acheter".

### Principe 2 — Gates bloquants vs scores composites

Une dimension non-substituable est un gate. Une dimension substituable est un score.

| Dimension                   | Type                | Pourquoi                                                |
|-----------------------------|---------------------|---------------------------------------------------------|
| Conformité AAOIFI           | Gate bloquant       | Non-substituable par définition (cadre éthique)         |
| Risque de faillite (Altman) | Gate bloquant       | Une faillite annule toute autre qualité                 |
| Détection fraude            | Gate bloquant       | Idem                                                    |
| Rentabilité (Piotroski)     | Score qualité       | Substituable avec d'autres dimensions de qualité        |
| Croissance                  | Score qualité       | Substituable                                            |
| Smart money                 | Score qualité       | Substituable                                            |
| Management                  | Score qualité       | Substituable                                            |

Un score composite qui agrège des gates avec des qualités est mathématiquement et conceptuellement faux. C'est la raison principale de la refonte du SCORE actuel.

### Principe 3 — Transparence des calculs

Aucun verdict du terminal ne doit être une boîte noire. Pour chaque verdict (INVESTISSABLE, fair value, conviction), l'utilisateur doit pouvoir cliquer "voir le détail" et obtenir :
- La liste des critères évalués
- La valeur observée et le seuil appliqué
- La source de la donnée (YFinance, SEC EDGAR, FMP...)
- La date du calcul

Conséquence pratique : tous les services backend retournent un objet structuré avec `verdict` et `details`, jamais juste une décision finale.

### Principe 4 — Pas de prédiction court terme

Le terminal ne fait aucune prédiction directionnelle sur des horizons inférieurs à 6 mois. La littérature académique (revue Fama 1970-2020) est claire : les modèles techniques court terme ont une accuracy 50-65 % qui ne survit pas aux coûts de transaction et aux biais comportementaux qu'ils induisent.

Le terminal calcule des métriques observables (volatilité, beta, drawdown historique, ATR pour stop loss structurel), mais ne les transforme jamais en signal directionnel.

### Principe 5 — Primauté humaine sur les décisions éthiques

Le check Israël reste hors du terminal. C'est une décision assumée : une responsabilité éthique ne se code pas, elle s'assume humainement. L'utilisateur effectue son check via Gemini ou recherche manuelle **avant** de saisir un ticker dans FinTerminal. Le terminal traite uniquement la dimension quantitative (fondamentaux, valorisation, technique, conformité halal mesurable).

Conséquence : pas d'API OpenSanctions, pas de scraping BDS, pas de dataset OHCHR intégré. Cette dimension reste à la main de l'utilisateur.

## 1.4 Ce que FinTerminal n'est pas

- Pas un robo-advisor (pas d'achat/vente automatique)
- Pas un fournisseur de fatwa (la conformité halal calculée n'est pas un avis religieux qualifié)
- Pas un outil de trading (pas de scalping, pas de day trading, pas d'options)
- Pas une plateforme sociale (pas de partage, pas de classement public)
- Pas un agrégateur de news (la veille reste à l'utilisateur)
- Pas un outil de backtesting de stratégies (la validation se fait par l'usage réel sur 5 ans)

---

# PARTIE 2 — Ce qui est tué et pourquoi

Ces décisions sont définitives. Elles entraînent des suppressions de code, pas juste des désactivations.

## 2.1 Module XGBoost et onglet PRÉVISIONS J+1

**Statut** : suppression complète, pas désactivation.

**Fichiers à supprimer** :
- `app/ml/predictor.py`
- `app/ml/trainer.py`
- `app/ml/features.py`
- `app/ml/targets.py`
- `app/ml/data_loader.py` (si non utilisé ailleurs)
- `app/api/v1/endpoints/prediction.py`
- Composants frontend onglet PRÉVISIONS

**Dépendances à retirer** :
- `xgboost` dans `requirements.txt`
- `scikit-learn` si plus utilisé ailleurs

**Raison** : sur un horizon DCA 3-10 ans, un signal directionnel J+1 a un impact économique négligeable (estimation : <0.2 % du capital final sur 7 ans de DCA mensuel). Surtout, sa simple présence dans un onglet visible introduit un biais comportemental : invitation à reporter ou avancer un DCA mécanique sur la base d'un signal court terme. C'est un sabotage de la discipline.

**Pas de reconversion sur drawdown 3 mois** : l'argument psychologique tient même sur cet horizon. Tout signal qui invite à briser la discipline DCA est sabotant, peu importe sa précision technique.

## 2.2 Kelly Criterion sous sa forme actuelle

**Statut** : suppression du dimensionnement par Kelly.

**Fichiers à modifier** : retirer toute logique Kelly de `app/ml/scoring/risk_*` et de l'onglet RISQUE (qui sera lui-même réorganisé).

**Raison** : Kelly nécessite un win rate historique fiable et un ratio gain/perte calibré sur des trades du même type. Avec 4 positions ouvertes depuis mars 2026, le sample est statistiquement nul. Si Kelly utilise des valeurs par défaut (win rate 55 %, ratio 2:1), ce n'est pas Kelly, c'est une formule de dimensionnement déguisée qui donne une fausse impression de rigueur.

**Remplacement** : paliers de conviction explicites (voir partie 7). Plus honnête, plus simple, plus stable.

## 2.3 Take Profit ATR-based pour positions long terme

**Statut** : suppression du TP ATR. Conservation du SL structurel utilisant ATR comme marge de bruit.

**Distinction fondamentale entre les deux usages d'ATR** :

- **TP ATR-based (TUÉ)** — style swing : objectif = prix actuel + N×ATR. L'ATR définit l'objectif lui-même. Sortie automatique sur volatilité.
- **SL structurel (CONSERVÉ)** — l'objectif est une cassure de niveau structurel (MA200), l'ATR sert uniquement à mesurer la marge de bruit autour de ce niveau pour éviter les faux signaux.

Ces deux usages ont des logiques opposées. Le premier suit la volatilité comme objectif, le second l'utilise comme filtre de bruit autour d'un niveau structurel.

**Raison de la suppression du TP** : ATR × 2.5 est un objectif de swing court terme (quelques semaines). Sur un horizon long terme, sortir une position au premier objectif ATR atteint serait catastrophique. Exemple concret : AIXTRON aurait été sortie à +2.5 ATR alors qu'elle a fait +49 % en 7 semaines, soit une perte d'opportunité massive.

**Remplacement** :
- **Stop loss** : conservé sous la forme `MA200 - K×ATR` avec K=2 par défaut, paramétrable via `STOP_LOSS_ATR_MULTIPLIER`. Justification de K=2 : filtre ~95 % du bruit gaussien (équivalent à 2 sigmas sur returns gaussiens). K=1 trop serré (sortie sur bruit), K=3 trop large (sortie tardive).
- **Take profit** : remplacé par "objectif fondamental" — niveau auquel le prix atteint le fair value médian. Pas un seuil de sortie automatique, mais un **niveau de réévaluation** (voir matrice DCA partie 6).

## 2.4 SCORE composite à pondérations non-substituables

**Statut** : suppression de la formule actuelle (Piotroski 25 % + Altman 20 % + Valorisation 20 % + Momentum 15 % + Croissance 20 %).

**Raison** : permet à une excellente qualité de compenser un Altman dans la zone dangereuse. Une entreprise en risque de faillite reste à éviter, peu importe son Piotroski. C'est une erreur méthodologique classique : moyenner des dimensions non-substituables.

**Remplacement** : architecture en deux temps (gates bloquants → score qualité). Détail en partie 4.

## 2.5 Scraping Musaffa

**Statut** : suppression complète du scraper.

**Fichiers à supprimer** :
- `app/integration/musaffa_scraper.py`
- Tests associés

**Raison** : risque légal (CGU Musaffa), risque technique (changement de structure HTML qui casse le scraping), redondant avec l'API Halal Terminal qui couvre 200K+ securities avec une transparence des ratios.

**Note** : Musaffa peut rester un outil de **vérification manuelle ponctuelle** pour l'utilisateur (consultation du site web), mais en dehors de FinTerminal.

## 2.6 Check Israël intégré au terminal

**Statut** : non intégré.

**Raison** : décision assumée par l'utilisateur. La dimension éthique non quantitative (contrats d'État, présence R&D, signature de pétitions) reste à la main humaine via processus Gemini avant saisie du ticker. Pas d'API OpenSanctions intégrée, pas de scraping BDS, pas de dataset OHCHR.

## 2.7 Tableau récapitulatif des suppressions

| Élément supprimé              | Type de suppression       | Impact code              |
|-------------------------------|---------------------------|--------------------------|
| Onglet PRÉVISIONS J+1         | Suppression totale        | ~600 lignes Python + JS  |
| Module XGBoost                | Suppression dépendance    | ~400 lignes Python       |
| Kelly Criterion               | Suppression de la logique | ~50 lignes Python        |
| TP ATR-based                  | Refonte du concept        | ~30 lignes Python        |
| SCORE composite actuel        | Refonte complète          | ~300 lignes Python       |
| Scraper Musaffa               | Suppression complète      | ~150 lignes Python       |
| Check Israël intégré          | Non implémenté            | 0 (jamais ajouté)        |
| **Total estimé**              |                           | **~1530 lignes**         |

Ce n'est pas une perte, c'est un allègement. Moins de code = moins de bugs = plus de robustesse.

---

# PARTIE 3 — Stack data finale

## 3.1 Vue d'ensemble des 8 sources

```
COUCHE 1 — ÉLIGIBILITÉ (gates bloquants)
├── Halal Terminal API   → Conformité AAOIFI + ratios bruts pour seuils personnels
└── (Check Israël hors terminal — utilisateur)

COUCHE 2 — DONNÉES FONDAMENTALES (canonique + fallback)
├── SEC EDGAR            → Source officielle US (XBRL, Form 4, 10-K/Q)
├── YFinance             → Source primaire universelle
└── FMP                  → Validation croisée + ratios pré-calculés

COUCHE 3 — DONNÉES MARCHÉ
├── Alpaca               → OHLCV historique fiable (US)
└── YFinance             → OHLCV international + live prices

COUCHE 4 — CONTEXTE
├── FRED                 → Macro (taux, inflation, régime)
├── Finnhub              → News + sentiment + insider codes officiels
└── GLEIF                → Structure légale (filiales)

COUCHE 5 — INFRASTRUCTURE
└── Redis                → Cache TTL adaptatif
```

## 3.2 Détail par source

### 3.2.1 Halal Terminal API (NOUVEAU — gate AAOIFI)

| Dimension              | Valeur                                                |
|------------------------|-------------------------------------------------------|
| URL                    | `https://api.halalterminal.com`                       |
| Authentification       | `X-API-Key` header                                    |
| Plan                   | Free tier (sans CB, juste email)                      |
| Limite                 | Token-metered (suffisant pour usage perso)            |
| Couverture             | 200K+ securities mondialement                         |
| Données sourcées       | SEC filings + market data providers                   |

**Endpoints utilisés** :
- `POST /api/screen/{symbol}` — screening complet 5 méthodologies + ratios bruts
- `POST /api/zakat/calculate` — calcul zakat (V2, pas V1)

**Réponse `/api/screen/{symbol}` — champs critiques** :
```json
{
  "symbol": "AAPL",
  "overall_status": "compliant",
  "ratios": {
    "debt_to_marketcap": 0.020,
    "debt_to_assets": 0.085,
    "cash_to_marketcap": 0.0164,
    "impure_revenue_ratio": 0.0023,
    "interest_income_ratio": 0.0018,
    "receivables_to_assets": 0.082,
    "non_compliant_assets_ratio": 0.046
  },
  "methodologies": {
    "AAOIFI":  {"status": "compliant", "failed_ratios": []},
    "DJIM":    {"status": "compliant", "failed_ratios": []},
    "FTSE":    {"status": "compliant", "failed_ratios": []},
    "MSCI":    {"status": "compliant", "failed_ratios": []},
    "S&P":     {"status": "compliant", "failed_ratios": []}
  },
  "compliance_explanation": "Compliant under all 5 methodologies. Debt/MC 2.0% well under all caps.",
  "as_of_date": "2026-04-30"
}
```

**Stratégie d'usage FinTerminal** :
1. On extrait les `ratios` bruts (pas le verdict)
2. On applique nos seuils personnels (voir partie 5)
3. Le verdict des méthodologies est conservé en information secondaire

**TTL Redis** : 7 jours (les ratios changent à la publication des états financiers, pas plus souvent).

### 3.2.2 SEC EDGAR (NOUVEAU — source canonique US)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| URL                    | `https://data.sec.gov/api`                        |
| Authentification       | User-Agent header obligatoire                     |
| Limite                 | 10 req/sec, pas de daily limit                    |
| Coût                   | 100 % gratuit illimité                            |
| Couverture             | US uniquement (toutes les actions cotées SEC)     |

**Endpoints utilisés** :
- `GET /xbrl/companyfacts/CIK{cik}.json` — toutes les données XBRL d'une entreprise
- `GET /xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json` — un concept précis sur l'historique
- `GET /submissions/CIK{cik}.json` — filings récents (10-K, 10-Q, 8-K, Form 4)

**Concepts XBRL critiques pour FinTerminal** :

| Concept us-gaap                                       | Usage FinTerminal               |
|-------------------------------------------------------|---------------------------------|
| `Revenues` ou `RevenueFromContractWithCustomerExcludingAssessedTax` | Revenue canonique pour validation croisée |
| `NetIncomeLoss`                                       | Net income canonique            |
| `Assets`                                              | Total assets                    |
| `LongTermDebt` + `ShortTermBorrowings`                | Total debt canonique            |
| `CashAndCashEquivalentsAtCarryingValue`               | Cash canonique                  |
| `InterestIncomeOperating`                             | Interest income (AAOIFI screening — distinct de InterestExpense !) |
| `EarningsPerShareDiluted`                             | EPS canonique                   |
| `NetCashProvidedByUsedInOperatingActivities`          | Operating cash flow canonique   |
| `PaymentsToAcquirePropertyPlantAndEquipment`          | CapEx canonique                 |
| `StockholdersEquity`                                  | Book value canonique            |

**Lookup CIK depuis ticker** :
```
GET https://www.sec.gov/files/company_tickers.json
```
Retourne le mapping {ticker → CIK}, à cacher 7 jours.

**TTL Redis** :
- Mapping ticker→CIK : 7 jours
- Company facts : 24h (mise à jour SEC quotidienne)
- Submissions (filings list) : 24h

**Limitations** : actions US uniquement. Pour AIXA.DE, RIO.L, etc. on retombe sur YFinance.

### 3.2.3 YFinance (existant — source primaire universelle)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| Authentification       | Aucune                                            |
| Limite                 | Non documentée (prudence requise)                 |
| Risque                 | Ban IP de Yahoo (s'est produit en 2023, 2024)     |
| Couverture             | Mondiale                                          |

**Champs `Ticker.info` utilisés en V1** (sur les ~180 disponibles) :
Voir le mapping détaillé en partie 4 (par onglet) et l'index thématique en annexe.

**États financiers utilisés** :
- `income_stmt` (annuel) + `quarterly_income_stmt`
- `balance_sheet` (annuel) + `quarterly_balance_sheet`
- `cashflow` (annuel) + `quarterly_cashflow`

**Holders et insiders** :
- `major_holders`, `institutional_holders`, `mutualfund_holders`
- `insider_transactions` (champ `Transaction` texte canonique pour BUY/SELL)

**Earnings** :
- `earnings_dates`, `earnings_estimate`, `revenue_estimate`
- `eps_trend`, `eps_revisions`, `growth_estimates`

**Stratégie d'usage** : source primaire pour large/mid caps mondiales, **toujours doublée** d'un fallback (FMP ou SEC EDGAR) sur les métriques critiques.

**TTL Redis** :
- `info` : 6h (live prices changent)
- États financiers : 24h
- Holders : 24h
- Earnings : 24h

### 3.2.4 Alpaca Markets (existant — OHLCV US)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| Authentification       | API Key + Secret                                  |
| Limite                 | 200 req/min (basic plan)                          |
| Feed                   | IEX (gratuit) — ~2-3 % du volume US               |
| Couverture             | US uniquement                                     |

**Usage** :
- OHLCV historique journalier pour calculs techniques (MA, RSI, ATR Wilder, Fibonacci, support/résistance)
- Validation croisée des closes vs YFinance

**Limitations IEX** : volumes intraday sous-estimés. Acceptable pour analyses end-of-day, pas pour intraday précis.

**TTL Redis** : 6h (données end-of-day stables après close).

### 3.2.5 FMP — Financial Modeling Prep (existant — validation croisée)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| Authentification       | API Key                                           |
| Limite                 | 250 calls/jour (free tier)                        |
| Couverture             | US principalement                                 |

**Endpoints utilisés** :
- `/v3/key-metrics-ttm/{ticker}` — ~50 ratios pré-calculés (ROIC, Graham Number, working capital, etc.)
- `/v3/ratios-ttm/{ticker}` — ratios financiers
- `/v3/income-statement/{ticker}?limit=4` — états financiers (validation croisée)
- `/v3/stock_peers?symbol={ticker}` — peers identifiés (V2)

**Stratégie d'usage** : **validation croisée** pour les métriques critiques + récupération de ratios pré-calculés que YFinance ne donne pas (ROIC notamment).

**TTL Redis** : 24h.

### 3.2.6 FRED — Federal Reserve St. Louis (existant — macro)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| Authentification       | API Key gratuite                                  |
| Limite                 | 120 req/min                                       |
| Couverture             | Macro mondiale (800K+ séries)                     |

**Séries utilisées en V1** :

| Série          | Description                              | Usage                       |
|----------------|------------------------------------------|-----------------------------|
| FEDFUNDS       | Taux directeur Fed                       | Régime monétaire            |
| DGS10          | Taux 10Y Treasury                        | Yield curve                 |
| DGS2           | Taux 2Y Treasury                         | Yield curve                 |
| T10Y2Y         | Spread 10Y-2Y                            | Récession si négatif        |
| CPIAUCSL       | CPI inflation                            | Inflation US                |
| UNRATE         | Taux chômage US                          | Cycle économique            |
| BAMLH0A0HYM2   | Spread HY                                | Stress crédit               |
| VIXCLS         | VIX                                      | Volatilité S&P 500          |
| DCOILWTICO     | Pétrole WTI                              | Contexte sectoriel énergie  |

**Stratégie d'usage** : régime macro affiché en **information contextuelle** dans le dashboard global. **Pas de pondération** dans les scores d'actions (changement par rapport à v1).

**TTL Redis** : 24h.

### 3.2.7 Finnhub (NOUVEAU — news, sentiment, insiders)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| URL                    | `https://finnhub.io/api/v1`                       |
| Authentification       | API Key (free tier)                               |
| Limite                 | 60 req/min, 30 req/sec burst                      |
| Couverture             | Mondiale                                          |

**Endpoints utilisés en V1** :
- `/company-news?symbol={ticker}&from={date}&to={date}` — news par ticker
- `/news-sentiment?symbol={ticker}` — sentiment automatique (large caps)
- `/stock/insider-sentiment?symbol={ticker}` — sentiment mensuel insiders agrégé
- `/stock/insider-transactions?symbol={ticker}` — transactions Form 4 avec codes officiels (P/S/A/G)
- `/stock/recommendation?symbol={ticker}` — tendances recommandations
- `/calendar/economic?from={date}&to={date}` — calendrier économique mondial

**Avantage clé pour FinTerminal** : les `transactionCode` SEC sont canoniques (P=Purchase, S=Sale, A=Award, G=Gift), pas besoin de parser un texte ambigu comme avec YFinance.

**TTL Redis** :
- News : 1h
- Sentiment : 6h
- Insider : 6h
- Calendrier : 24h

### 3.2.8 GLEIF (existant — structure légale)

| Dimension              | Valeur                                            |
|------------------------|---------------------------------------------------|
| URL                    | `https://api.gleif.org/api/v1`                    |
| Authentification       | Aucune                                            |
| Limite                 | Non documentée                                    |
| Couverture             | Mondiale                                          |

**Usage en V1** : récupération du LEI et de la liste des filiales pour information.

**Note** : usage limité en V1, pas critique. Conservé pour compatibilité avec l'existant et utilité future (validation halal multi-entités si on découvre qu'une filiale opère un business problématique).

**TTL Redis** : 7 jours.

## 3.3 Stratégie de fallback consolidée

### Métriques **CRITIQUES** (validation croisée systématique, US uniquement)

Pour ces 7 métriques, on consulte **2 sources minimum** quand l'action est cotée aux US et on lève un warning si écart > 5 %.

| Métrique           | Source 1 (canonique) | Source 2 (validation) | Tolérance |
|--------------------|----------------------|------------------------|-----------|
| Revenue TTM        | SEC EDGAR            | YFinance               | 5 %       |
| Net Income TTM     | SEC EDGAR            | YFinance               | 5 %       |
| Total Debt         | SEC EDGAR            | YFinance               | 5 %       |
| Total Cash         | SEC EDGAR            | YFinance               | 5 %       |
| EPS TTM            | SEC EDGAR            | YFinance               | 3 %       |
| Free Cash Flow TTM | SEC EDGAR            | YFinance               | 5 %       |
| Interest Income    | SEC EDGAR            | YFinance               | 10 %      |

Pour les actions internationales (AIXA, RIO si on prend le listing UK), SEC EDGAR n'est pas disponible. On retombe sur YFinance + FMP en validation croisée si possible.

### Métriques **STANDARD** (1 source primaire, fallback en cas d'absence)

| Métrique               | Primaire     | Fallback         |
|------------------------|--------------|------------------|
| Marges                 | YFinance     | FMP              |
| ROE, ROA, ROIC         | YFinance/FMP | calcul SEC       |
| Multiples (P/E, P/B…)  | YFinance     | FMP              |
| MA50, MA200, RSI       | calcul Alpaca | calcul YF       |
| Beta                   | YFinance     | calcul propre    |
| Holders                | YFinance     | -                |
| Insider transactions   | Finnhub      | YFinance + SEC   |

### Métriques **OPPORTUNISTES** (skip si manquant)

| Métrique                 | Source         | Comportement si absent  |
|--------------------------|----------------|-------------------------|
| News sentiment           | Finnhub        | Section "non disponible"|
| Insider sentiment mensuel| Finnhub        | Section "non disponible"|
| LEI / structure          | GLEIF          | Skip section            |
| Recommendation trends    | Finnhub        | Skip section            |

## 3.4 Comportement si API down

| API down       | Conséquence                          | Mitigation                                   |
|----------------|--------------------------------------|----------------------------------------------|
| Halal Terminal | **Gate AAOIFI inopérant**            | Bloquer toute nouvelle analyse, alerte      |
| YFinance       | 60 % des données indisponibles       | Bascule sur SEC EDGAR + FMP + Finnhub        |
| Alpaca         | OHLCV US dégradé                     | Bascule sur YFinance OHLCV                   |
| FMP            | Validation croisée perdue            | Continue avec YF + warning                   |
| SEC EDGAR      | Validation officielle perdue         | Continue avec YF + FMP + warning             |
| Finnhub        | News + sentiment perdus              | Skip sections, continue analyse              |
| FRED           | Macro perdue                         | Cache stale acceptable jusqu'à 7 jours       |
| GLEIF          | Structure perdue                     | Skip section                                 |

**Règle critique** : Halal Terminal down = **bloquage total** des nouvelles entrées. C'est un gate non-substituable. Pour les analyses de positions existantes (re-screening), on tolère un cache plus long (jusqu'à 14 jours) en cas d'indisponibilité prolongée.

## 3.5 Tableau consolidé TTL cache

| Type de donnée            | TTL recommandé | Justification                               |
|---------------------------|----------------|---------------------------------------------|
| Prix temps réel           | 5 min          | Volatilité intraday                         |
| OHLCV journalier          | 6 h            | Stable après close                          |
| YFinance.info             | 6 h            | Mix prix live + fondamentaux                |
| Fondamentaux trimestriels | 24 h           | Mise à jour rare                            |
| États financiers annuels  | 7 jours        | Mise à jour annuelle                        |
| Holders                   | 24 h           | Reporting trimestriel                       |
| Insider transactions      | 6 h            | Form 4 dans les 2 jours                     |
| News                      | 1 h            | Fraîcheur importante                        |
| Sentiment news            | 6 h            | Évolue lentement                            |
| Calendrier earnings       | 24 h           | Stable                                      |
| Calendrier économique     | 24 h           | Stable                                      |
| Macro FRED                | 24 h           | Mise à jour quotidienne                     |
| LEI / GLEIF               | 7 jours        | Très stable                                 |
| Lookup CIK SEC            | 7 jours        | Très stable                                 |
| SEC company facts         | 24 h           | Mise à jour SEC quotidienne                 |
| Halal Terminal screen     | 7 jours        | Ratios changent à publication ÉF            |
| Peer groups (FMP)         | 7 jours        | Très stable                                 |
| Recommandations analystes | 24 h           | Évoluent lentement                          |
| Halal cache (re-screen)   | 14 j (extended)| Si Halal Terminal indisponible              |

---

# PARTIE 4 — Architecture des 3 onglets et bandeau de synthèse

## 4.1 Vue d'ensemble

L'analyse d'un ticker se compose de **3 onglets profonds** + **1 bandeau de synthèse** toujours visible.

```
┌─────────────────────────────────────────────────────────────┐
│  BANDEAU DE SYNTHÈSE (toujours visible en haut)              │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐│
│  │INVESTISSABLE│ │BIEN VALORISÉE│ │ DÉCISION SYNTHÈSE     ││
│  │     ✓       │ │      ⚠       │ │ WATCHLIST → 245$      ││
│  │  AAOIFI ✓  │ │ Ratio 1.16   │ │ Réact. DCA <240$      ││
│  │  Altman ✓  │ │ Disp. 0.18   │ │                        ││
│  └─────────────┘ └──────────────┘ └───────────────────────┘│
└─────────────────────────────────────────────────────────────┘
┌────────────────────┬───────────────────┬────────────────────┐
│ Onglet 1           │ Onglet 2          │ Onglet 3           │
│ INVESTISSABLE ?    │ BIEN VALORISÉE ?  │ COMMENT RENTRER ?  │
└────────────────────┴───────────────────┴────────────────────┘
```

Le bandeau **synthétise**, les onglets **détaillent**.

## 4.2 Onglet 1 — INVESTISSABLE ?

**Question** : Cette boîte mérite-t-elle mon argent ?

**Architecture en deux temps** :
```
ÉTAPE 1 — GATES BLOQUANTS (séquentiels, court-circuit possible)
  Gate 1 — Conformité AAOIFI personnelle (seuils stricts)
  Gate 2 — Risque de faillite (Altman Z-Score zone non-distress)
  Gate 3 — Pas de signaux de fraude détectés

  Si un gate échoue → INVESTISSABLE = NON, on n'évalue pas le score qualité

ÉTAPE 2 — SCORE QUALITÉ (sur les substituables, pondéré)
  Composante A — Piotroski F-Score              (poids 30 %)
  Composante B — Croissance pluri-annuelle      (poids 25 %)
  Composante C — Smart money (insiders/inst)    (poids 20 %)
  Composante D — Capital allocation             (poids 15 %)
  Composante E — Stabilité bénéfices (beat rate)(poids 10 %)

  Score normalisé 0-100
```

### 4.2.1 Détail des gates bloquants

#### Gate 1 — Conformité AAOIFI personnelle

Détaillé en partie 5. Synthèse :
- Halal Terminal API retourne ratios bruts
- Application des seuils personnels (debt/MC ≤ 30 %, revenus haram ≤ 3 %, cash/MC ≤ 30 %, intérêt ≤ 3 %)
- Tous doivent passer pour valider le gate

**Sortie** : `{verdict: PASS|FAIL, ratios: {...}, methodologies_info: {...}}`

#### Gate 2 — Risque de faillite

Calcul Altman Z-Score (formule pour entreprises cotées non-financières) :
```
Z = 1.2 × (WC/TA) + 1.4 × (RE/TA) + 3.3 × (EBIT/TA) + 0.6 × (MC/TL) + 1.0 × (Sales/TA)

WC  = Working Capital
RE  = Retained Earnings
TA  = Total Assets
EBIT = Earnings Before Interest and Taxes
MC  = Market Capitalization
TL  = Total Liabilities
Sales = Total Revenue
```

**Seuils Altman Z-Score** :
- Z ≥ 2.99 : zone safe → gate PASS
- 1.81 ≤ Z < 2.99 : zone grise → gate **WARNING** (passe mais bandeau orange)
- Z < 1.81 : zone distress → gate FAIL

**Pour entreprises non-manufacturières**, on utilise le Z'' (4 facteurs, pas X4 market cap) :
```
Z'' = 6.56 × (WC/TA) + 3.26 × (RE/TA) + 6.72 × (EBIT/TA) + 1.05 × (BV/TL)

BV = Book Value (au lieu de Market Cap)

Seuils : Z'' ≥ 2.6 safe, 1.1-2.6 grey, < 1.1 distress
```

**Si données insuffisantes pour calculer Z** (small caps, données partielles) : gate retourne `INSUFFICIENT_DATA` → l'onglet INVESTISSABLE retourne `verdict: UNCERTAIN` avec mention explicite.

#### Gate 3 — Détection signaux de fraude

Trois signaux composite, chacun pondéré 1 point. Si **2 signaux positifs sur 3** → gate FAIL.

**Important** : tous les signaux financiers sont normalisés sectoriellement. Un seuil absolu produirait des faux positifs sur les secteurs capital-intensive (Energy, Basic Materials, Real Estate, Utilities) où FCF/NI < 0.5 plusieurs années est normal en phase d'investissement.

##### Signal 1 — FCF/NI anormalement bas vs secteur

```python
def signal_fcf_quality_normalized(symbol: str, sector: str) -> dict:
    """
    Compare FCF/NI (3 ans) à la médiane sectorielle.
    Alerte si bottom quartile du secteur.
    """
    ticker_fcf_ni_3y = compute_fcf_to_ni_3y(symbol)
    if ticker_fcf_ni_3y is None:
        return {"signal": None, "reason": "Données insuffisantes"}

    sector_median = get_sector_median_fcf_ni_3y(sector)

    if sector_median is None:
        # Fallback : seuil absolu mais avec exemption sectorielle
        EXEMPT_SECTORS = ["Energy", "Basic Materials", "Real Estate", "Utilities"]
        if sector in EXEMPT_SECTORS:
            return {
                "signal": False,
                "reason": f"Secteur {sector} exempté (capital intensive normal)",
            }
        return {
            "signal": ticker_fcf_ni_3y < 0.5,
            "reason": f"FCF/NI = {ticker_fcf_ni_3y:.2f} (seuil absolu 0.5)",
        }

    # Comparaison sectorielle : alerte si bottom quartile (≈ médiane × 0.5)
    sector_q1 = sector_median * 0.5
    is_alert = ticker_fcf_ni_3y < sector_q1

    return {
        "signal": is_alert,
        "ticker_value": ticker_fcf_ni_3y,
        "sector_median": sector_median,
        "rationale": (
            f"FCF/NI {ticker_fcf_ni_3y:.2f} vs médiane secteur {sector_median:.2f}. "
            f"{'ALERTE: bottom quartile' if is_alert else 'Normal pour le secteur'}"
        ),
    }
```

##### Signal 2 — Croissance receivables anormalement supérieure au CA

```python
def signal_receivables_anomaly(symbol: str, sector: str) -> dict:
    """
    Compare la croissance receivables vs croissance CA sur 2 ans.
    Normalisé sectoriellement car certains secteurs ont des cycles
    longs de paiement (B2B vs B2C).
    """
    receivables_growth_2y = compute_receivables_cagr_2y(symbol)
    revenue_growth_2y = compute_revenue_cagr_2y(symbol)

    if receivables_growth_2y is None or revenue_growth_2y is None:
        return {"signal": None}

    if revenue_growth_2y <= 0:
        return {"signal": False}  # pas pertinent en décroissance

    ratio = receivables_growth_2y / revenue_growth_2y
    sector_median_ratio = get_sector_median_receivables_to_revenue_growth(sector)

    if sector_median_ratio is None:
        # Fallback : alerte si ratio > 2.0 sur 2 ans
        return {
            "signal": ratio > 2.0,
            "rationale": f"Receivables grow {ratio:.1f}× faster than revenue",
        }

    # Alerte si ratio > 1.5× médiane sectorielle
    return {
        "signal": ratio > 1.5 * sector_median_ratio,
        "rationale": (
            f"Receivables/Revenue growth ratio {ratio:.2f} "
            f"vs sector median {sector_median_ratio:.2f}"
        ),
    }
```

##### Signal 3 — Restatements SEC fréquents (US uniquement)

```python
def signal_restatements_frequent(cik: str) -> dict:
    """
    Détecte 3+ restatements ou amendments SEC en 3 ans.
    Seuil monté de 2 à 3 pour tolérer les restatements légitimes.
    """
    if not cik:
        return {"signal": None, "reason": "Non US, signal indisponible"}

    filings = get_recent_filings_sec(cik, lookback_years=3)
    restatement_count = sum(
        1 for f in filings
        if f["form"] in ("10-K/A", "10-Q/A", "NT 10-K", "NT 10-Q")
    )

    return {
        "signal": restatement_count >= 3,
        "count": restatement_count,
        "rationale": f"{restatement_count} amendments/late filings in 3y (alert if ≥3)",
    }
```

##### Verdict du gate fraude

```python
def compute_fraud_gate(symbol: str, sector: str, cik: Optional[str]) -> dict:
    s1 = signal_fcf_quality_normalized(symbol, sector)
    s2 = signal_receivables_anomaly(symbol, sector)
    s3 = signal_restatements_frequent(cik) if cik else {"signal": None}

    positive_signals = sum(
        1 for s in (s1, s2, s3) if s.get("signal") is True
    )

    return {
        "verdict": "FAIL" if positive_signals >= 2 else "PASS",
        "positive_signals_count": positive_signals,
        "signals": {"fcf_ni": s1, "receivables": s2, "restatements": s3},
    }
```

**Note** : c'est un signal d'alerte, pas une preuve de fraude. Un gate FAIL signifie "à investiguer manuellement avant d'investir", pas "fraude confirmée".

**Liste des secteurs exemptés du seuil absolu FCF/NI** (utilisée en fallback si médiane sectorielle indisponible) :
- Energy
- Basic Materials
- Real Estate
- Utilities

### 4.2.2 Détail du score qualité (étape 2)

Évalué uniquement si tous les gates passent.

#### Composante A — Piotroski F-Score (30 %)

Score Piotroski 0-9, normalisé 0-100 :
```
piotroski_normalized = (piotroski_raw / 9) × 100
```

**Important** : si certains critères Piotroski ne sont pas calculables (données manquantes), le score est calculé sur N critères évalués (`raw / N × 100`), avec :
- Si N < 6 : composante non disponible, retourne `null`, n'entre pas dans la moyenne pondérée

#### Composante B — Croissance pluri-annuelle (25 %)

Trois sous-métriques, moyennées :
```
- Revenue CAGR 3 ans
- EPS CAGR 3 ans
- FCF CAGR 3 ans

Pour chacune :
  if CAGR ≥ 15% : 100
  elif CAGR ≥ 10%: 80
  elif CAGR ≥ 5% : 60
  elif CAGR ≥ 0% : 40
  elif CAGR ≥ -5%: 20
  else            : 0

growth_normalized = mean(revenue_score, eps_score, fcf_score)
```

#### Composante C — Smart money (20 %)

Trois sous-signaux, équipondérés :
```
Signal C1 — Détention institutionnelle stable ou en hausse
  Variation % détention institutionnelle sur 12 mois (YFinance + Finnhub)
  Si variation ≥ +1 % : 100
  Si variation entre -1 % et +1 % : 60
  Si variation < -1 % : 30

Signal C2 — Insider transactions nettes (Finnhub mspr)
  Monthly Share Purchase Ratio agrégé sur 6 mois
  Si MSPR ≥ +0.1 (achat net significatif) : 100
  Si MSPR entre -0.1 et +0.1 : 50
  Si MSPR < -0.1 (vente nette) : 20

Signal C3 — Pas de short interest excessif
  Short interest / Float
  Si < 5 %  : 100
  Si 5-15 % : 60
  Si > 15 % : 20

smart_money_normalized = mean(C1, C2, C3)
```

#### Composante D — Capital allocation (15 %)

**Refonte v2.1** : suppression de D2 (dividende), considéré comme dimension culturelle/sectorielle non universelle. La composante D évalue désormais uniquement deux signaux structurels.

**Justification de la suppression de D2** : un growth stock comme AIXTRON qui réinvestit son cash en R&D plutôt que de distribuer ne devrait pas être pénalisé. Le dividende est un choix d'allocation parmi d'autres, pas un signe de qualité en soi.

##### D1 — Buybacks responsables (50 % de la composante)

```python
def signal_d1_buybacks(symbol: str) -> int:
    """
    Évalue la qualité des rachats d'actions.
    Bon buyback = réduction du nombre d'actions ET financé par FCF
    (pas par de la dette nouvelle).
    """
    shares_3y_ago = get_shares_outstanding(symbol, years_ago=3)
    shares_now = get_shares_outstanding(symbol)
    fcf_3y_total = sum(get_fcf_annual(symbol, year) for year in last_3_years)
    buybacks_3y_total = sum(get_buybacks_annual(symbol, year) for year in last_3_years)

    if shares_3y_ago is None or shares_now is None:
        return None

    reduction_pct = (shares_3y_ago - shares_now) / shares_3y_ago

    # Vérifier que les buybacks sont financés par FCF (pas par dette)
    fcf_covers_buybacks = fcf_3y_total >= buybacks_3y_total

    if reduction_pct >= 0.05 and fcf_covers_buybacks:
        return 100  # excellent : réduction sensible et auto-financée
    if reduction_pct >= 0.05 and not fcf_covers_buybacks:
        return 60   # buybacks financés par dette, prudence
    if 0 <= reduction_pct < 0.05:
        return 60   # neutre
    return 20       # dilution (augmentation actions)
```

##### D3 — Intensité d'investissement (CapEx + R&D) maîtrisée (50 % de la composante)

**Refonte v2.1** : la formule considère désormais `(CapEx + R&D) / Revenue` plutôt que `CapEx / Revenue` seul. Justification : le R&D passe en charge plutôt qu'en CapEx capitalisé, et certaines entreprises (AIXTRON, semiconducteurs, software) investissent massivement en R&D mais peu en CapEx pur. Les pénaliser serait une erreur méthodologique.

```python
def signal_d3_investment_intensity(symbol: str, sector: str) -> int:
    """
    Évalue l'intensité d'investissement (CapEx + R&D) vs médiane sectorielle.
    Une entreprise qui investit comme son secteur = score élevé.
    """
    capex = get_capex_ttm(symbol)
    rd = get_rd_expense_ttm(symbol)
    revenue = get_revenue_ttm(symbol)

    if revenue is None or revenue == 0:
        return None

    capex = capex or 0
    rd = rd or 0
    investment_intensity = (capex + rd) / revenue

    sector_median = get_sector_median_investment_intensity(sector)

    if sector_median is None:
        # Fallback : seuils absolus prudents
        if 0.05 <= investment_intensity <= 0.30:
            return 80  # zone normale
        if investment_intensity < 0.05:
            return 50  # peu d'investissement
        return 60  # très intensif

    ratio_to_sector = investment_intensity / sector_median
    if 0.7 <= ratio_to_sector <= 1.5:
        return 90  # aligné avec le secteur
    if ratio_to_sector < 0.7:
        return 50  # sous-investissement vs secteur
    if ratio_to_sector <= 2.0:
        return 70  # sur-investissement modéré
    return 40      # sur-investissement excessif
```

**Vérification chiffrée sur les positions actuelles** :

| Ticker  | CapEx/Rev | R&D/Rev | (CapEx+R&D)/Rev | Sector median | Score D3 |
|---------|-----------|---------|------------------|---------------|----------|
| AIXTRON | ~4 %      | ~11 %   | ~15 %            | ~12 % (semis) | 90       |
| RIO     | ~12 %     | ~0.3 %  | ~12 %            | ~10 % (mining)| 90       |
| EOG     | ~25 %     | ~0 %    | ~25 %            | ~22 % (E&P)   | 90       |

Sans la correction, AIXTRON aurait eu un score 50 ("peu de CapEx") alors que l'entreprise investit massivement.

##### Calcul final de la composante D

```python
def compute_capital_allocation(symbol: str, sector: str) -> dict:
    d1 = signal_d1_buybacks(symbol)
    d3 = signal_d3_investment_intensity(symbol, sector)

    # Moyenne des composantes disponibles (D1 et D3 équipondérés)
    available = [s for s in (d1, d3) if s is not None]
    if not available:
        return {"score": None, "available": False}

    return {
        "score": round(mean(available), 1),
        "components": {"d1_buybacks": d1, "d3_investment": d3},
        "available": True,
    }
```

#### Composante E — Stabilité des bénéfices (10 %)

```
Beat rate sur 8 trimestres (EPS réel > EPS estimé)
beat_count = nombre de trimestres avec beat sur 8

Si beat_count ≥ 7 : 100
Si beat_count ≥ 5 : 75
Si beat_count ≥ 3 : 50
Si beat_count < 3 : 25
```

#### Calcul final score qualité

```python
def compute_quality_score(components: dict) -> dict:
    """
    Calcule le score qualité pondéré.
    Si une composante est None (data insuffisante), on rebalance les poids.
    """
    weights = {
        "piotroski":         0.30,
        "growth":            0.25,
        "smart_money":       0.20,
        "capital_allocation":0.15,
        "earnings_stability":0.10,
    }

    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return {"score": None, "available": False}

    total_weight = sum(weights[k] for k in available)
    weighted_sum = sum(weights[k] * available[k] for k in available)
    score = weighted_sum / total_weight  # rebalanced

    return {
        "score": round(score, 1),
        "components_used": list(available.keys()),
        "components_missing": [k for k in weights if k not in available],
        "available": len(available) >= 3,  # min 3 composantes pour score crédible
    }
```

### 4.2.3 Verdict final onglet 1

```python
def compute_investissable_verdict(
    aaoifi_gate: dict,
    altman_gate: dict,
    fraud_gate: dict,
    quality_score: dict,
) -> dict:
    # Gates en cascade
    if aaoifi_gate["verdict"] == "FAIL":
        return {"verdict": "NON", "reason": "AAOIFI non conforme",
                "blocked_at": "gate_aaoifi"}

    if altman_gate["verdict"] == "FAIL":
        return {"verdict": "NON", "reason": "Risque de faillite (Altman Z-Score)",
                "blocked_at": "gate_altman"}

    if altman_gate["verdict"] == "INSUFFICIENT_DATA":
        return {"verdict": "INCERTAIN", "reason": "Altman non calculable",
                "blocked_at": "gate_altman"}

    if fraud_gate["verdict"] == "FAIL":
        return {"verdict": "NON", "reason": "Signaux de fraude détectés",
                "blocked_at": "gate_fraud"}

    # Tous les gates passés, on évalue la qualité
    if not quality_score["available"]:
        return {"verdict": "INCERTAIN",
                "reason": "Données qualité insuffisantes",
                "quality_score": None}

    score = quality_score["score"]
    if score >= 75:
        label = "OUI - QUALITÉ EXCELLENTE"
    elif score >= 60:
        label = "OUI - QUALITÉ BONNE"
    elif score >= 45:
        label = "OUI - QUALITÉ MOYENNE"
    else:
        label = "NON - QUALITÉ INSUFFISANTE"

    return {
        "verdict": "OUI" if score >= 45 else "NON",
        "label": label,
        "quality_score": score,
        "components": quality_score["components_used"],
        "altman_warning": altman_gate["verdict"] == "WARNING",
    }
```

### 4.2.4 Sortie API onglet 1

```json
{
  "verdict": "OUI",
  "label": "OUI - QUALITÉ EXCELLENTE",
  "quality_score": 78.5,
  "gates": {
    "aaoifi":  {"verdict": "PASS", "ratios": {...}, "methodologies_info": {...}},
    "altman":  {"verdict": "PASS", "z_score": 4.2, "zone": "SAFE"},
    "fraud":   {"verdict": "PASS", "signals": {...}}
  },
  "quality_components": {
    "piotroski":          {"score": 88, "raw": "8/9", "details": {...}},
    "growth":             {"score": 75, "details": {...}},
    "smart_money":        {"score": 67, "details": {...}},
    "capital_allocation": {"score": 80, "details": {...}},
    "earnings_stability": {"score": 88, "beat_count": "7/8"}
  },
  "data_completeness": "FULL",  # FULL | PARTIAL | INSUFFICIENT
  "warnings": []
}
```

## 4.3 Onglet 2 — BIEN VALORISÉE ?

**Question** : Le prix est-il raisonnable aujourd'hui ?

**Architecture** : 4 méthodes de fair value calculées indépendamment, puis agrégation médiane + dispersion.

### 4.3.1 Méthode 1 — Multiples vs historique 5 ans

```python
def valuation_vs_historical(symbol: str) -> dict:
    """
    Compare les multiples actuels à leur médiane sur 5 ans.
    """
    current = get_current_multiples(symbol)
    historical = get_historical_multiples(symbol, years=5)

    fair_values = {}

    # P/E
    if current["pe"] and historical["pe_median"]:
        # fair_price_pe = current_price × (median_pe / current_pe)
        fair_values["pe_based"] = current["price"] * (historical["pe_median"] / current["pe"])

    # P/S
    if current["ps"] and historical["ps_median"]:
        fair_values["ps_based"] = current["price"] * (historical["ps_median"] / current["ps"])

    # EV/EBITDA
    if current["ev_ebitda"] and historical["ev_ebitda_median"]:
        fair_values["ev_ebitda_based"] = current["price"] * (historical["ev_ebitda_median"] / current["ev_ebitda"])

    if not fair_values:
        return {"available": False}

    return {
        "available": True,
        "fair_value": median(fair_values.values()),
        "details": fair_values,
        "method": "Multiples vs historique 5 ans",
    }
```

**Conditions de fiabilité** : nécessite 5 ans d'historique. Pour IPO récente (< 3 ans), méthode non disponible.

### 4.3.2 Méthode 2 — Multiples vs secteur

```python
def valuation_vs_sector(symbol: str) -> dict:
    """
    Compare les multiples actuels à la médiane sectorielle.
    """
    current = get_current_multiples(symbol)
    sector_medians = get_sector_medians(symbol)
    # sector_medians construites à partir des peers du secteur GICS
    # (idéalement 20+ entreprises pour médiane stable)

    fair_values = {}

    if current["pe"] and sector_medians.get("pe"):
        fair_values["pe_based"] = current["price"] * (sector_medians["pe"] / current["pe"])
    # idem P/S, EV/EBITDA

    if not fair_values:
        return {"available": False}

    return {
        "available": True,
        "fair_value": median(fair_values.values()),
        "sector_size": sector_medians["n_peers"],
        "details": fair_values,
        "method": "Multiples vs médiane sectorielle",
    }
```

**Note** : la construction des peer groups par secteur est un travail à part entière (V2 ou itération continue). En V1, on utilise les peers FMP `/v3/stock_peers` comme starting point.

### 4.3.3 Méthode 3 — Graham Number

```python
def valuation_graham(symbol: str) -> dict:
    """
    Graham Number = sqrt(22.5 × EPS × BVPS)
    Valable uniquement si EPS > 0 et BVPS > 0.
    """
    eps = get_eps_ttm(symbol)
    bvps = get_book_value_per_share(symbol)

    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return {"available": False, "reason": "EPS ou BVPS négatif/nul"}

    graham_number = math.sqrt(22.5 * eps * bvps)

    return {
        "available": True,
        "fair_value": round(graham_number, 2),
        "inputs": {"eps": eps, "bvps": bvps},
        "method": "Graham Number",
    }
```

**Note** : Graham Number est conservateur, calibré pour des entreprises mûres value. Sur des growth stocks comme AAPL, donnera systématiquement un fair value inférieur au prix de marché — c'est attendu et c'est l'intérêt (méthode conservative).

### 4.3.4 Méthode 4 — Target médian analystes

```python
def valuation_analyst_target(symbol: str) -> dict:
    """
    Target médian Wall Street à 12 mois.
    """
    target = get_analyst_target(symbol)  # YFinance + Finnhub

    if target is None or target.get("median") is None:
        return {"available": False}

    n_analysts = target.get("number_of_analysts", 0)

    return {
        "available": n_analysts >= 5,  # min 5 analystes pour fiabilité
        "fair_value": target["median"],
        "low": target["low"],
        "high": target["high"],
        "n_analysts": n_analysts,
        "method": "Target médian analystes (12 mois)",
    }
```

**Note** : les analystes sell-side ont un biais haussier connu (~3-5 % au-dessus de la valeur réalisée à 12 mois). À utiliser comme borne haute, pas comme central.

### 4.3.5 Agrégation : fair value médian + dispersion

```python
def aggregate_fair_value(methods: list[dict], current_price: float) -> dict:
    """
    Agrège les fair values des méthodes disponibles.
    Calcule la dispersion entre méthodes pour mesurer la confiance.
    """
    available_values = [m["fair_value"] for m in methods if m["available"]]

    if len(available_values) < 2:
        return {
            "available": False,
            "reason": "Moins de 2 méthodes disponibles",
            "methods_run": len(methods),
            "methods_available": len(available_values),
        }

    fv_median = median(available_values)
    fv_mean = mean(available_values)

    # Dispersion : coefficient de variation (std / mean)
    dispersion = stdev(available_values) / fv_mean if fv_mean > 0 else 0

    # Ratio prix/fair value
    ratio = current_price / fv_median

    return {
        "available": True,
        "fair_value_median": round(fv_median, 2),
        "fair_value_mean": round(fv_mean, 2),
        "dispersion": round(dispersion, 3),  # 0.0-1.0+
        "ratio_price_to_fair_value": round(ratio, 3),
        "n_methods": len(available_values),
        "methods_breakdown": {
            m["method"]: m["fair_value"] for m in methods if m["available"]
        },
    }
```

**Interprétation de la dispersion** :
- `dispersion < 0.15` : méthodes en accord, confiance élevée
- `0.15 ≤ dispersion < 0.30` : accord modéré
- `dispersion ≥ 0.30` : méthodes en désaccord fort, confiance faible

Cette dispersion alimente directement la règle de synthèse "ENTRÉE PRUDENTE" (cas 4 du bandeau, voir 4.5).

### 4.3.6 Verdict final onglet 2

```python
def compute_valuation_verdict(aggregate: dict) -> dict:
    if not aggregate["available"]:
        return {
            "verdict": "INDÉTERMINÉ",
            "reason": "Méthodes insuffisantes",
        }

    ratio = aggregate["ratio_price_to_fair_value"]
    dispersion = aggregate["dispersion"]

    if ratio < 0.85:
        label = "SOUS-ÉVALUÉE"
        verdict = "OUI"
    elif ratio < 0.95:
        label = "JUSTE PRIX (LÉGÈRE DÉCOTE)"
        verdict = "OUI"
    elif ratio < 1.05:
        label = "JUSTE PRIX"
        verdict = "OUI_NEUTRE"
    elif ratio < 1.5:
        label = "SURÉVALUÉE"
        verdict = "NON"
    else:
        label = "FORTEMENT SURÉVALUÉE"
        verdict = "NON"

    return {
        "verdict": verdict,
        "label": label,
        "ratio": ratio,
        "dispersion": dispersion,
        "fair_value_median": aggregate["fair_value_median"],
        "confidence": "HIGH" if dispersion < 0.15 else "MEDIUM" if dispersion < 0.30 else "LOW",
    }
```

### 4.3.7 Sortie API onglet 2

```json
{
  "verdict": "NON",
  "label": "SURÉVALUÉE",
  "ratio_price_to_fair_value": 1.16,
  "dispersion": 0.08,
  "confidence": "HIGH",
  "fair_value_median": 245.0,
  "fair_value_mean": 247.3,
  "n_methods": 4,
  "current_price": 284.23,
  "methods": {
    "vs_historical_5y": {"available": true, "fair_value": 240, "details": {...}},
    "vs_sector":        {"available": true, "fair_value": 252, "details": {...}},
    "graham_number":    {"available": true, "fair_value": 198, "inputs": {...}},
    "analyst_target":   {"available": true, "fair_value": 303, "n_analysts": 38}
  }
}
```

## 4.4 Onglet 3 — COMMENT RENTRER ?

**Question** : Stratégie d'entrée concrète sur cette position.

**Architecture** : trois sections — État technique, Niveaux clés, Plan d'entrée DCA.

### 4.4.1 État technique

Calculs purement observables, **sans signal directionnel** :

| Métrique                  | Calcul                                       | Source     |
|---------------------------|----------------------------------------------|------------|
| Prix vs MA50              | `(price - ma50) / ma50`                      | Alpaca/calcul |
| Prix vs MA200             | `(price - ma200) / ma200`                    | Alpaca/calcul |
| MA50 vs MA200             | `(ma50 - ma200) / ma200` + pente MA50 50j    | Alpaca/calcul |
| RSI 14j (Wilder)          | RSI canonique avec EMA Wilder                | calcul     |
| ATR 14j (Wilder)          | ATR canonique avec EMA Wilder                | calcul     |
| Position dans range 52w   | `(price - low_52w) / (high_52w - low_52w)`   | YFinance   |
| Drawdown depuis ATH       | `(price - ath_252j) / ath_252j`              | calcul     |
| Beta 5 ans                | `beta`                                       | YFinance   |

### 4.4.2 Scénarios d'état technique

5 scénarios (ajout du cas "consolidation" et "piège" par rapport à la v1) :

```
SCÉNARIO A — TENDANCE HAUSSIÈRE FORTE
  Conditions : prix > MA200 ET MA50 > MA200 ET pente MA50 ascendante (>0)
  Logique d'entrée : attendre pull-back vers MA50 ou Fib 38.2 %
  Objectif : ne pas chasser le prix, optimiser le point d'entrée

SCÉNARIO B — CORRECTION DANS TENDANCE HAUSSIÈRE
  Conditions : MA200 < prix < MA50 ET MA50 > MA200
  Logique d'entrée : opportunité d'entrée si fondamentaux OK
  Objectif : capturer une correction saine

SCÉNARIO C — SOUS MA200, BAISSE
  Conditions : prix < MA200 ET MA50 < MA200 ET pente MA50 descendante
  Logique d'entrée : prudence maximale, entrée seulement si :
    - RSI < 35 (survente confirmée)
    - Score qualité INVESTISSABLE > 65 (très bonne entreprise)
  Objectif : éviter d'attraper un couteau qui tombe

SCÉNARIO D — CONSOLIDATION (NOUVEAU)
  Conditions : prix entre MA50 et MA200 (zone) ET pente MA50 plate (±2 %)
  Logique d'entrée : attendre confirmation directionnelle (cassure
    haut MA50 ou bas MA200 sur 3 séances consécutives)
  Objectif : ne pas s'engager dans le bruit

SCÉNARIO E — GOLDEN CROSS EN FORMATION OU PIÈGE (NOUVEAU)
  Conditions : prix > MA200 mais MA50 < MA200, ou inverse
  Sous-cas E1 (golden cross en formation) : prix > MA200 ET MA50 < MA200
    ET pente MA50 ascendante → entrée prudente possible (signe de
    retournement haussier)
  Sous-cas E2 (piège dans baisse) : prix > MA50 ET MA50 < MA200
    ET pente MA50 descendante → ne pas entrer, c'est probablement
    un rebond technique dans une tendance baissière
```

### 4.4.3 Niveaux clés (calculs purs, pas de signal)

```python
def compute_key_levels(df_ohlcv: pd.DataFrame, current_price: float) -> dict:
    """
    Calcule les niveaux techniques observables.
    Aucune interprétation directionnelle.
    """
    # Fibonacci sur 52w (canonique : high/low, pas close)
    last_252 = df_ohlcv.tail(252)
    swing_high = last_252["high"].max()
    swing_low = last_252["low"].min()
    diff = swing_high - swing_low

    fib = {
        "0":     swing_low,
        "23.6":  swing_low + 0.236 * diff,
        "38.2":  swing_low + 0.382 * diff,
        "50":    swing_low + 0.500 * diff,
        "61.8":  swing_low + 0.618 * diff,
        "78.6":  swing_low + 0.786 * diff,
        "100":   swing_high,
    }

    # Volume profile par tranche de prix (validation Fibonacci)
    # On découpe le range en 20 buckets et on calcule le volume cumulé
    n_buckets = 20
    bucket_size = diff / n_buckets
    volume_by_bucket = {}
    for _, row in last_252.iterrows():
        bucket_idx = int((row["close"] - swing_low) / bucket_size)
        bucket_idx = max(0, min(n_buckets - 1, bucket_idx))
        volume_by_bucket[bucket_idx] = volume_by_bucket.get(bucket_idx, 0) + row["volume"]

    # Médiane du volume par bucket pour seuil de "validation"
    volumes = list(volume_by_bucket.values())
    volume_threshold = median(volumes) * 0.5  # 50 % de la médiane

    # Pour chaque niveau Fib, on regarde si le bucket correspondant a du volume
    fib_validated = {}
    for level_name, level_price in fib.items():
        bucket_idx = int((level_price - swing_low) / bucket_size)
        bucket_idx = max(0, min(n_buckets - 1, bucket_idx))
        bucket_volume = volume_by_bucket.get(bucket_idx, 0)
        fib_validated[level_name] = {
            "price": round(level_price, 2),
            "volume_validated": bucket_volume > volume_threshold,
        }

    # Stop loss structurel : MA200 - 2 × ATR (cassure structurelle)
    ma200 = df_ohlcv["close"].tail(200).mean()
    atr14 = compute_atr_wilder(df_ohlcv, period=14)
    structural_stop = ma200 - 2 * atr14

    return {
        "fib_levels": fib_validated,
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "ma50": ma50,
        "ma200": ma200,
        "atr14": atr14,
        "structural_stop": round(structural_stop, 2),
    }
```

**Note importante** : pour les small caps avec volume très faible, le volume profile ne donnera pas d'info exploitable. On affiche tous les niveaux avec mention "non validé statistiquement" (`volume_validated: false`).

### 4.4.4 Plan d'entrée DCA

Le plan d'entrée dépend de la **conviction** (voir partie 7) et du **scénario technique**.

```python
def compute_entry_plan(
    conviction_tier: str,
    scenario: str,
    current_price: float,
    fib_levels: dict,
    monthly_dca_budget: float,
    min_tranche_amount: float = 25.0,  # paramétrable via env
) -> dict:
    """
    Construit le plan d'entrée concret en €/$.
    """
    # Allocation budgétaire selon palier de conviction
    allocation_pct = {
        "VERY_HIGH": 0.45,
        "HIGH":      0.30,
        "MEDIUM":    0.20,
        "LOW":       0.0,
    }[conviction_tier]

    if conviction_tier == "LOW":
        return {
            "action": "SKIP",
            "reason": "Conviction insuffisante, redéployer le budget",
        }

    monthly_amount = monthly_dca_budget * allocation_pct

    # Découpage en tranches selon scénario technique
    if scenario in ("TENDANCE_HAUSSIÈRE_FORTE", "GOLDEN_CROSS_FORMATION"):
        tranches = [
            {"pct": 0.5, "trigger_price": fib_levels["38.2"]["price"]},
            {"pct": 0.3, "trigger_price": fib_levels["50"]["price"]},
            {"pct": 0.2, "trigger_price": fib_levels["61.8"]["price"]},
        ]
    elif scenario == "CORRECTION_DANS_HAUSSE":
        tranches = [
            {"pct": 1.0, "trigger_price": current_price},
        ]
    elif scenario == "SOUS_MA200_BAISSE":
        tranches = [
            {"pct": 0.5, "trigger_price": fib_levels["50"]["price"]},
            {"pct": 0.5, "trigger_price": fib_levels["61.8"]["price"]},
        ]
    elif scenario == "CONSOLIDATION":
        tranches = []
    elif scenario == "PIÈGE":
        return {"action": "SKIP", "reason": "Piège technique (rebond dans baisse)"}
    else:
        tranches = [{"pct": 1.0, "trigger_price": current_price}]

    # Calcul des montants par tranche
    tranches_with_amounts = [
        {**t, "amount": round(monthly_amount * t["pct"], 2)}
        for t in tranches
    ]

    # Consolidation des tranches sous le seuil minimum
    consolidated = consolidate_tranches(tranches_with_amounts, min_tranche_amount)

    if not consolidated:
        return {
            "action": "SKIP",
            "reason": (
                f"Montant total ({monthly_amount:.0f}€) trop faible "
                f"pour atteindre le seuil minimum ({min_tranche_amount}€). "
                f"Redéployer sur autre position."
            ),
        }

    return {
        "action": "DCA_ÉCHELONNÉ" if len(consolidated) > 1 else "DCA_DIRECT",
        "monthly_total": round(monthly_amount, 2),
        "tranches": consolidated,
        "scenario": scenario,
        "conviction_tier": conviction_tier,
        "min_tranche_threshold": min_tranche_amount,
    }


def consolidate_tranches(
    tranches: list[dict],
    min_tranche_amount: float = 25.0,
) -> list[dict]:
    """
    Consolide les tranches en dessous du seuil minimum.
    Évite la friction excessive (frais + spread) sur petits montants.

    Stratégie :
    - Si toutes les tranches sont sous le seuil mais total ≥ seuil :
      consolider en une tranche unique au prix de la plus haute (entrée immédiate)
    - Si certaines sont valides : merge les invalides dans la dernière valide
    - Si total < seuil : skip total (return [])

    Justification de 25€ par défaut :
    - Trade Republic : 1€ de frais → 4% friction à 25€, acceptable long terme
    - Cohérent avec budget DCA 150-250€/mois (5-8 tranches max)
    """
    if not tranches:
        return []

    valid = [t for t in tranches if t["amount"] >= min_tranche_amount]
    invalid = [t for t in tranches if t["amount"] < min_tranche_amount]

    if not valid:
        # Toutes les tranches sont trop petites, on consolide en une seule
        total = sum(t["amount"] for t in tranches)
        if total >= min_tranche_amount:
            return [{
                "pct": 1.0,
                "trigger_price": tranches[0]["trigger_price"],  # prix de la 1ère tranche
                "amount": round(total, 2),
                "consolidated": True,
                "consolidation_reason": "Toutes les tranches sous seuil, fusion en une",
            }]
        return []  # Skip total, redéploiement

    if invalid:
        # Merge les invalides dans la dernière valide
        extra = sum(t["amount"] for t in invalid)
        valid[-1]["amount"] = round(valid[-1]["amount"] + extra, 2)
        valid[-1]["consolidated"] = True
        valid[-1]["consolidation_reason"] = (
            f"{len(invalid)} tranche(s) sous seuil ({min_tranche_amount}€) "
            f"fusionnée(s) dans cette tranche"
        )

    return valid
```

### 4.4.5 Sortie API onglet 3

```json
{
  "scenario": "TENDANCE_HAUSSIÈRE_FORTE",
  "technical_state": {
    "price_vs_ma50": +0.084,
    "price_vs_ma200": +0.108,
    "rsi_14": 58.2,
    "atr_14": 4.85,
    "position_in_52w_range": 0.94,
    "drawdown_from_ath": -0.015,
    "beta_5y": 1.07
  },
  "key_levels": {
    "fib_levels": {
      "38.2": {"price": 248.5, "volume_validated": true},
      "50":   {"price": 241.0, "volume_validated": true},
      "61.8": {"price": 233.5, "volume_validated": false}
    },
    "ma50": 262.13,
    "ma200": 256.26,
    "structural_stop": 246.55,
    "swing_high": 288.62,
    "swing_low": 193.46
  },
  "entry_plan": {
    "action": "DCA_ÉCHELONNÉ",
    "monthly_total": 60.00,
    "tranches": [
      {"pct": 0.5, "trigger_price": 248.5, "amount": 30.00},
      {"pct": 0.3, "trigger_price": 241.0, "amount": 18.00},
      {"pct": 0.2, "trigger_price": 233.5, "amount": 12.00}
    ],
    "conviction_tier": "MEDIUM"
  },
  "stop_loss_logic": "Cassure de 246.55$ (MA200 - 2×ATR) confirmée sur 3 séances + invalidation thèse fondamentale"
}
```

## 4.5 Bandeau de synthèse

### 4.5.1 Architecture technique

Le bandeau est un **composant frontend** qui consomme un **endpoint backend dédié** qui agrège les 3 services.

```
GET /api/v1/synthesis/{symbol}
   → appelle en parallèle (asyncio.gather) :
     - investissable_service.analyze(symbol)
     - valuation_service.analyze(symbol)
     - entry_service.analyze(symbol)
   → applique les règles de synthèse déterministes
   → retourne le verdict consolidé
```

### 4.5.2 Les 6 règles de synthèse complètes

Ces règles sont **déterministes** et **testables unitairement**. Aucune ambiguïté tolérée.

```python
def compute_synthesis(
    investissable: dict,
    valuation: dict,
    entry: dict,
) -> dict:
    """
    Applique les 6 règles de synthèse pour produire la décision finale.

    IMPORTANT — Distinction des verdicts à NE PAS confondre :
    - InvestissableVerdict.verdict ∈ {"OUI", "NON", "INCERTAIN"}
    - ValuationVerdict.verdict ∈ {"OUI", "OUI_NEUTRE", "NON", "INDÉTERMINÉ"}

    OUI_NEUTRE existe UNIQUEMENT sur le ValuationVerdict, pas sur InvestissableVerdict.
    Il sert d'affichage informatif dans l'onglet 2 pour la zone 0.95-1.05.

    compute_synthesis se base sur :
    - investissable["verdict"] : court-circuit si "NON" ou "INCERTAIN"
    - valuation["ratio_price_to_fair_value"] : les seuils numériques tranchent
      pour toutes les autres règles

    Ne JAMAIS écrire de code défensif du genre :
        if investissable["verdict"] != "OUI": return INCERTAIN
    qui exclurait OUI_NEUTRE par accident — mais OUI_NEUTRE n'existe pas sur
    InvestissableVerdict de toute façon. Le risque c'est de confondre les deux schémas.
    """
    # ─────────────────────────────────────────────────────────
    # RÈGLE 1 — INVESTISSABLE NON → REJET
    # ─────────────────────────────────────────────────────────
    if investissable["verdict"] == "NON":
        return {
            "decision": "REJET",
            "reason": investissable.get("reason", "Critères INVESTISSABLE non remplis"),
            "blocked_at": "investissable",
            "show_other_tabs": False,
            "color": "RED",
        }

    if investissable["verdict"] == "INCERTAIN":
        return {
            "decision": "INCERTAIN",
            "reason": "Données insuffisantes pour conclure",
            "show_other_tabs": True,  # affichage informatif uniquement
            "color": "GREY",
        }

    # ─────────────────────────────────────────────────────────
    # À ce stade, INVESTISSABLE = OUI
    # On évalue selon la valorisation
    # ─────────────────────────────────────────────────────────
    val_ratio = valuation.get("ratio_price_to_fair_value")
    val_disp = valuation.get("dispersion", 0)

    if val_ratio is None:
        return {
            "decision": "ANALYSE_INCOMPLÈTE",
            "reason": "Fair value non calculable",
            "color": "GREY",
        }

    # ─────────────────────────────────────────────────────────
    # RÈGLE 6 — Surcote massive → REJET
    # Avant les autres règles pour court-circuit propre
    # Note v2.1 : >= 2.0 (inclusif à gauche) pour cohérence avec
    # les autres frontières qui utilisent < (inclusif à droite).
    # Tout seuil de 2.0 exact tombe en règle 6.
    # ─────────────────────────────────────────────────────────
    if val_ratio >= 2.0:
        return {
            "decision": "REJET_SURCOTE_MASSIVE",
            "reason": f"Prix > 2× fair value médian ({valuation['fair_value_median']}$)",
            "color": "RED",
            "rationale": "Risque de bulle, refus d'entrée même en watchlist",
            "synthesis_text": (
                "Excellente entreprise mais à un prix qui n'a aucun lien "
                "avec sa valeur intrinsèque. Watchlist serait psychologiquement "
                "dangereuse (tu finirais par craquer). REJET assumé."
            ),
        }

    # ─────────────────────────────────────────────────────────
    # HARMONISATION v2.1 : les seuils du bandeau (nouvelles entrées)
    # sont alignés sur la matrice DCA (positions existantes).
    # Conséquence : aucune zone n'admet d'entrée sur action surévaluée.
    #
    # Zones unifiées :
    #   < 0.95           ENTRÉE/ACTIF PLEIN (size 1.0)
    #   0.95 - 1.05      ENTRÉE/ACTIF NEUTRE (size 0.7)
    #   1.05 - 1.5       WATCHLIST/SUSPENDU (size 0)
    #   1.5 - 2.0        WATCHLIST_PRUDENT/SUSPENDU_PRUDENT (size 0)
    #   > 2.0            REJET / VENTE_PARTIELLE
    # ─────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────
    # RÈGLE 2 — Sous-évaluée (< 0.95) → ENTRÉE PLEINE
    # ─────────────────────────────────────────────────────────
    if val_ratio < 0.95:
        # Sous-cas avec dispersion forte
        if val_disp > 0.30:
            return {
                "decision": "ENTRÉE_PRUDENTE",
                "reason": "Sous-évaluée mais méthodes en désaccord (dispersion >30%)",
                "rationale": "Fair value incertain, taille de position réduite",
                "size_adjustment": 0.6,
                "fair_value": valuation["fair_value_median"],
                "color": "GREEN",
                "show_other_tabs": True,
                "next_step": "Voir onglet COMMENT RENTRER pour le plan détaillé",
            }
        return {
            "decision": "ENTRÉE_PLEINE",
            "reason": "Investissable et sous fair value",
            "fair_value": valuation["fair_value_median"],
            "size_adjustment": 1.0,
            "color": "GREEN",
            "show_other_tabs": True,
            "next_step": "Voir onglet COMMENT RENTRER pour le plan",
        }

    # ─────────────────────────────────────────────────────────
    # RÈGLE 3 — Autour fair value (0.95 - 1.05) → ENTRÉE NEUTRE
    # Zone de tolérance étroite autour du fair value
    # ─────────────────────────────────────────────────────────
    if val_ratio < 1.05:
        return {
            "decision": "ENTRÉE_NEUTRE",
            "reason": f"Autour du fair value (ratio {val_ratio:.2f})",
            "rationale": (
                "Pas de bargain mais pas de surcote significative. "
                "Entrée à taille réduite (70% de la conviction)."
            ),
            "size_adjustment": 0.7,
            "fair_value": valuation["fair_value_median"],
            "color": "GREEN",
            "show_other_tabs": True,
        }

    # ─────────────────────────────────────────────────────────
    # RÈGLE 4 — Surévaluée modérée (1.05 - 1.5) → WATCHLIST
    # Pas d'entrée, juste niveau cible affiché
    # ─────────────────────────────────────────────────────────
    if val_ratio < 1.5:
        target_buy_price = valuation["fair_value_median"]
        # Réactivation à fair value × 0.95 pour marge de sécurité
        reactivation_price = target_buy_price * 0.95
        return {
            "decision": "WATCHLIST",
            "reason": f"Surévaluée ({val_ratio:.2f}× fair value)",
            "rationale": "Excellente entreprise mais prix au-dessus du juste",
            "fair_value": target_buy_price,
            "reactivation_price": round(reactivation_price, 2),
            "size_adjustment": 0,
            "color": "ORANGE",
            "show_other_tabs": True,
            "next_step": "Position en watchlist, alerte à suivre",
        }

    # ─────────────────────────────────────────────────────────
    # RÈGLE 5 — Surcote forte (1.5 - 2.0) → WATCHLIST PRUDENT
    # ─────────────────────────────────────────────────────────
    return {
        "decision": "WATCHLIST_PRUDENT",
        "reason": f"Forte surévaluation ({val_ratio:.2f}× fair value)",
        "rationale": (
            "Surcote significative. Risque que le retour à la moyenne "
            "soit douloureux. Watchlist mais sans engagement à entrer."
        ),
        "fair_value": valuation["fair_value_median"],
        "reactivation_price": round(valuation["fair_value_median"] * 0.95, 2),
        "size_adjustment": 0,
        "color": "ORANGE",
        "show_other_tabs": True,
    }
```

### 4.5.3 Tableau récapitulatif des 6 règles (harmonisé v2.1)

| # | INVESTISSABLE | VALORISATION (ratio) | Dispersion | Décision (nouvelles entrées) | Statut DCA (existantes) | Couleur | Size adj.    |
|---|---------------|----------------------|------------|-------------------------------|--------------------------|---------|---------------|
| 1 | NON           | -                    | -          | REJET                         | n/a (pas en portefeuille)| RED     | -             |
| 2 | OUI           | < 0.95               | < 0.30     | ENTRÉE PLEINE                 | ACTIF (DCA 100%)         | GREEN   | 1.0           |
| 2'| OUI           | < 0.95               | ≥ 0.30     | ENTRÉE PRUDENTE               | ACTIF (DCA 100%)         | GREEN   | 0.6           |
| 3 | OUI           | 0.95 - 1.05          | -          | ENTRÉE NEUTRE                 | ACTIF NEUTRE (DCA 70%)   | GREEN   | 0.7           |
| 4 | OUI           | 1.05 - 1.5           | -          | WATCHLIST                     | SUSPENDU (DCA 0%)        | ORANGE  | 0             |
| 5 | OUI           | 1.5 - 2.0            | -          | WATCHLIST PRUDENT             | SUSPENDU PRUDENT (DCA 0%)| ORANGE  | 0             |
| 6 | OUI           | > 2.0                | -          | REJET SURCOTE MASSIVE         | VENTE PARTIELLE 33%      | RED     | -             |

**Cohérence garantie** : pour un même ratio, le verdict des nouvelles entrées et celui des positions existantes ne peuvent jamais être contradictoires. La matrice DCA partie 6.3 et le bandeau partie 4.5 utilisent les mêmes seuils.

**Le `size_adjustment`** est appliqué à l'allocation issue du palier de conviction (partie 7). Exemple : conviction HIGH (30 % du DCA mensuel) × size_adjustment 0.7 = 21 % effectifs sur cette ligne ce mois-ci.

### 4.5.4 Affichage du bandeau (UX cible)

```
┌────────────────────────────────────────────────────────────────┐
│ AAPL — Apple Inc.                                               │
│ ┌───────────┐ ┌────────────┐ ┌─────────────────────────────┐ │
│ │INVESTISS. │ │ VALORISÉE  │ │ DÉCISION                    │ │
│ │    ✓      │ │     ⚠      │ │  WATCHLIST                  │ │
│ │ Q. 78/100 │ │ Ratio 1.16 │ │  Réactivation DCA: 232.75$  │ │
│ │           │ │ Disp. 0.08 │ │                             │ │
│ └───────────┘ └────────────┘ └─────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

---

# PARTIE 5 — Logique AAOIFI personnelle

## 5.1 Seuils personnels stricts

Les seuils standards AAOIFI sont à 30 % pour debt/MC, 30 % pour cash/MC, 5 % pour revenus haram. Tu vises l'**AAOIFI strict personnel** :

```python
# app/services/shariah/thresholds.py

class ShariahCustomThresholds:
    """
    Seuils personnels stricts.
    Override des seuils méthodologiques publiés par les institutions.

    Justification :
    - debt/MC ≤ 30% (au lieu de 33% classique) : tolérance zéro sur Riba structurel
    - impure_revenue ≤ 3% (au lieu de 5%) : approche Khatakhatay & Nizar 2007
      qui montre que 5% est généreux ; 3% est plus aligné avec l'esprit du
      principe (revenus négligeables, pas tolérables-mais-significatifs)
    - cash/MC ≤ 30% : seuil canonique
    - interest_income ≤ 3% : aligné sur le seuil revenus haram
    """
    DEBT_TO_MARKETCAP_MAX = 0.30
    DEBT_TO_ASSETS_MAX = 0.30  # méthodo alternative
    CASH_TO_MARKETCAP_MAX = 0.30
    IMPURE_REVENUE_MAX = 0.03
    INTEREST_INCOME_MAX = 0.03
    RECEIVABLES_TO_ASSETS_MAX = 0.45  # seuil canonique AAOIFI
```

**Note** : seuils paramétrables via fichier de config (`config/shariah_thresholds.yaml`) pour ajustement futur sans refactoring de code.

## 5.2 Algorithme de screening

```python
async def screen_with_personal_thresholds(symbol: str) -> dict:
    """
    Récupère les ratios bruts depuis Halal Terminal et applique
    les seuils personnels au lieu de ceux des 5 méthodologies.
    """
    try:
        response = await halal_terminal_client.screen(symbol)
    except HalalTerminalError as e:
        return {
            "verdict": "ERROR",
            "reason": f"Halal Terminal indisponible: {e}",
            "fallback": "Re-vérifier manuellement avant toute entrée",
        }

    if response is None:
        return {
            "verdict": "NOT_COVERED",
            "reason": f"{symbol} non couvert par Halal Terminal",
            "fallback": "Vérification Musaffa manuelle requise",
        }

    ratios = response["ratios"]
    t = ShariahCustomThresholds

    custom_checks = {
        "debt_to_marketcap": {
            "value": ratios["debt_to_marketcap"],
            "threshold": t.DEBT_TO_MARKETCAP_MAX,
            "pass": ratios["debt_to_marketcap"] <= t.DEBT_TO_MARKETCAP_MAX,
        },
        "cash_to_marketcap": {
            "value": ratios["cash_to_marketcap"],
            "threshold": t.CASH_TO_MARKETCAP_MAX,
            "pass": ratios["cash_to_marketcap"] <= t.CASH_TO_MARKETCAP_MAX,
        },
        "impure_revenue_ratio": {
            "value": ratios["impure_revenue_ratio"],
            "threshold": t.IMPURE_REVENUE_MAX,
            "pass": ratios["impure_revenue_ratio"] <= t.IMPURE_REVENUE_MAX,
        },
        "interest_income_ratio": {
            "value": ratios.get("interest_income_ratio", 0),
            "threshold": t.INTEREST_INCOME_MAX,
            "pass": ratios.get("interest_income_ratio", 0) <= t.INTEREST_INCOME_MAX,
        },
    }

    # Verdict global = AND de tous les checks calculables
    failed_checks = [k for k, v in custom_checks.items() if not v["pass"]]
    overall_pass = len(failed_checks) == 0

    return {
        "verdict": "PASS" if overall_pass else "FAIL",
        "failed_checks": failed_checks,
        "checks": custom_checks,
        "halal_terminal_methodology_verdicts": response["methodologies"],
        "raw_ratios": ratios,
        "as_of_date": response["as_of_date"],
        "source": "Halal Terminal API + custom thresholds",
    }
```

## 5.3 Matrice de re-screening AAOIFI

Une position peut devenir non conforme après l'entrée. La fréquence et la sévérité du re-screening doivent être proportionnelles au risque.

### 5.3.1 Triggers de re-screening

```
TRIGGER 1 — Event-driven (priorité haute)
  Source : SEC EDGAR submissions endpoint
  Détection : nouveau filing 10-Q, 10-K, ou 8-K matériel
  Polling : quotidien (cron job 1× par jour)
  Action : re-screen automatique de la position

TRIGGER 2 — Mensuel calé sur DCA (filet de sécurité)
  Cadence : 1× par mois, le jour du DCA
  Cible : toutes les positions ouvertes
  Action : re-screen complet, comparaison avec dernier screen

TRIGGER 3 — Manuel sur demande
  UI : bouton "Re-screen" sur chaque ligne du portfolio
  Action : re-screen immédiat
  Use case : doute sur news récente
```

### 5.3.2 Matrice de décision si non-conformité détectée

```python
def determine_action_on_non_compliance(
    previous_screen: dict,
    current_screen: dict,
    failed_check: str,
) -> dict:
    """
    Détermine l'action à prendre si une position devient non conforme.
    Gradient en 4 niveaux selon la sévérité.
    """
    if failed_check in current_screen.get("failed_checks", []):
        threshold = current_screen["checks"][failed_check]["threshold"]
        actual = current_screen["checks"][failed_check]["value"]
        previous_actual = previous_screen["checks"][failed_check]["value"]

        # Calcul du dépassement relatif
        relative_excess = (actual - threshold) / threshold

        # Détection changement de business model (heuristique)
        is_business_change = _detect_business_change(previous_screen, current_screen)

        if is_business_change:
            return {
                "level": "BUSINESS_CHANGE",
                "action": "VENTE_IMMÉDIATE_TOTALE",
                "reason": "Changement d'activité haram détecté",
                "urgency": "IMMEDIATE",
                "no_gradient": True,
            }

        if relative_excess < 0.10:  # < 10% de dépassement
            return {
                "level": "MARGINAL",
                "action": "DCA_SUSPENDU",
                "reason": f"Dépassement marginal de {relative_excess*100:.1f}%",
                "rationale": (
                    "Marge d'erreur des données + variations comptables temporaires. "
                    "Position maintenue, re-check au prochain trimestre."
                ),
                "exit_required": False,
                "next_check": "next_quarter",
                "exit_if_persistent": True,  # sortie si confirmé sur 2 trimestres
            }

        if relative_excess < 0.30:  # 10-30% de dépassement
            return {
                "level": "SIGNIFICATIF",
                "action": "SORTIE_PROGRESSIVE_3_MOIS",
                "reason": f"Dépassement significatif de {relative_excess*100:.1f}%",
                "rationale": "Sortie graduelle pour limiter timing market",
                "exit_required": True,
                "exit_schedule": [
                    {"month": 1, "pct_to_sell": 0.33},
                    {"month": 2, "pct_to_sell": 0.33},
                    {"month": 3, "pct_to_sell": 0.34},
                ],
                "dca_suspended": True,
                "redirect_cash_to": "watchlist",
            }

        # > 30% de dépassement
        return {
            "level": "MASSIF",
            "action": "VENTE_IMMÉDIATE_TOTALE",
            "reason": f"Dépassement massif de {relative_excess*100:.1f}%",
            "rationale": "Non-conformité claire, sortie sans gradient",
            "exit_required": True,
            "urgency": "IMMEDIATE",
        }

    # Cas spécial : revenus haram > 2× seuil
    if failed_check == "impure_revenue_ratio":
        actual = current_screen["checks"][failed_check]["value"]
        threshold = current_screen["checks"][failed_check]["threshold"]
        if actual > 2 * threshold:
            return {
                "level": "MASSIF",
                "action": "VENTE_IMMÉDIATE_TOTALE",
                "reason": "Revenus haram > 2× seuil personnel (3% → 6%+)",
                "urgency": "IMMEDIATE",
            }

    return {"level": "OK", "action": "NONE"}


def _detect_business_change(prev: dict, curr: dict) -> bool:
    """
    Détecte un changement de business model haram.
    Heuristique basique en V1 : variation > 50 % du ratio impure_revenue
    en un seul cycle de reporting.
    """
    prev_impure = prev["checks"]["impure_revenue_ratio"]["value"]
    curr_impure = curr["checks"]["impure_revenue_ratio"]["value"]
    if prev_impure < 0.01 and curr_impure > 0.05:
        return True
    if curr_impure > 2 * prev_impure and curr_impure > 0.05:
        return True
    return False
```

### 5.3.3 Tableau récapitulatif

| Niveau         | Dépassement relatif | Action                    | Urgence       | Exit gradient |
|----------------|---------------------|---------------------------|----------------|----------------|
| Marginal       | < 10 %              | DCA suspendu              | Pas urgent     | Non, watch     |
| Significatif   | 10-30 %             | Sortie progressive 3 mois | Modéré         | Oui, 1/3/mois |
| Massif         | > 30 %              | Vente immédiate           | Immédiate      | Non, full      |
| Business change| n/a                 | Vente immédiate            | Immédiate      | Non, full      |

---

# PARTIE 6 — Logique fair value et DCA

## 6.1 Méthodes de calcul fair value (V1)

Quatre méthodes, **sans DCF en V1** (décision figée).

| # | Méthode                          | Calcul                                          | Conditions                  |
|---|----------------------------------|-------------------------------------------------|------------------------------|
| 1 | Multiples vs historique 5 ans    | price × (median_multiple_5y / current_multiple) | ≥ 5 ans d'historique         |
| 2 | Multiples vs secteur             | price × (sector_median / current_multiple)      | ≥ 10 peers identifiés        |
| 3 | Graham Number                    | √(22.5 × EPS × BVPS)                            | EPS > 0 et BVPS > 0          |
| 4 | Target médian analystes          | analyst_target_median (12 mois)                 | ≥ 5 analystes                |

Détails de calcul en partie 4.3.

## 6.2 Agrégation médiane

```python
fair_value_median = median([fv1, fv2, fv3, fv4])  # parmi les disponibles
fair_value_mean = mean([fv1, fv2, fv3, fv4])
dispersion = stdev / mean  # coefficient de variation
ratio_price_to_fair_value = current_price / fair_value_median
```

**Choix de la médiane vs moyenne** : médiane plus robuste aux outliers (target analystes biaisé haussier, Graham souvent conservateur). On expose les deux dans la sortie API mais on utilise la médiane comme valeur centrale.

## 6.3 Matrice de décision DCA face au fair value

Pour chaque position du portefeuille, le statut DCA est calculé **mécaniquement** depuis le ratio prix/fair value.

**Cohérence v2.1** : les seuils utilisés ici sont **identiques** à ceux du bandeau de synthèse (partie 4.5.3). Pour un même ratio, le verdict d'une nouvelle entrée et celui d'une position existante ne peuvent pas être contradictoires.

```python
def compute_dca_status(
    current_price: float,
    fair_value_median: float,
    dispersion: float,
    partial_sell_pct_at_2x: float = 0.33,  # paramétrable via env
) -> dict:
    """
    Détermine le statut DCA d'une position existante.
    Calculé à chaque rafraîchissement, pas de mémoire d'état précédent.
    Seuils harmonisés avec le bandeau de synthèse (4.5.3).
    """
    ratio = current_price / fair_value_median

    # Borne inférieure de réactivation : fair_value × 0.95
    reactivation_price = fair_value_median * 0.95

    if ratio < 0.95:
        return {
            "status": "ACTIF",
            "dca_pct": 1.0,
            "reason": f"Sous fair value (ratio {ratio:.2f})",
            "color": "GREEN",
        }

    if ratio < 1.05:
        return {
            "status": "ACTIF_NEUTRE",
            "dca_pct": 0.7,  # taille réduite en zone neutre
            "reason": f"Autour du fair value (ratio {ratio:.2f})",
            "color": "GREEN",
        }

    if ratio < 1.5:
        return {
            "status": "SUSPENDU",
            "dca_pct": 0.0,
            "reason": f"Au-dessus du fair value (ratio {ratio:.2f})",
            "reactivation_price": reactivation_price,
            "rationale": "Cash redéployé sur autres positions ou watchlist",
            "color": "ORANGE",
        }

    if ratio < 2.0:
        return {
            "status": "SUSPENDU_PRUDENT",
            "dca_pct": 0.0,
            "reason": f"Forte surévaluation (ratio {ratio:.2f})",
            "reactivation_price": reactivation_price,
            "review_thesis": True,
            "color": "ORANGE",
        }

    # Ratio >= 2.0 : surcote massive
    # Note v2.1 : >= 2.0 (inclusif) pour cohérence avec compute_synthesis
    return {
        "status": "VENTE_PARTIELLE_RECOMMANDÉE",
        "dca_pct": 0.0,
        "reason": f"Surcote massive (ratio {ratio:.2f})",
        "recommendation": (
            f"Vendre {int(partial_sell_pct_at_2x * 100)}% de la position pour bloquer le gain"
        ),
        "partial_sell_pct": partial_sell_pct_at_2x,
        "rationale": (
            "Le prix est tellement décorrélé du fair value que l'asymétrie "
            "risque/rendement est défavorable. Bloquer une partie du gain."
        ),
        "color": "RED",
    }
```

### 6.3.1 Tableau récapitulatif

| Ratio price/fair value | Statut                          | DCA pct | Action                            |
|------------------------|---------------------------------|---------|------------------------------------|
| < 0.95                 | ACTIF                           | 100 %   | DCA normal                         |
| 0.95 - 1.05            | ACTIF NEUTRE                    | 70 %    | DCA réduit                         |
| 1.05 - 1.5             | SUSPENDU                        | 0 %     | DCA arrêté, cash redéployé         |
| 1.5 - 2.0              | SUSPENDU PRUDENT                | 0 %     | DCA arrêté + revoir thèse          |
| > 2.0                  | VENTE PARTIELLE RECOMMANDÉE     | 0 %     | Vente 33 % (paramétrable)          |

### 6.3.2 Justification et paramétrabilité du 33% de vente partielle

**Le 33% est une heuristique, pas un théorème.** Justification :

- Pas tout vendre (100%) car la thèse fondamentale n'est pas cassée — juste le prix est décorrélé. La vente totale reviendrait à timer le marché alors qu'on cherche à garder l'exposition au business.
- Pas garder 100% car le risque asymétrique est défavorable au-delà de 2× fair value (downside potentiel grand, upside limité).
- 33% ≈ approximation d'un quartile, équilibre entre "bloquer du gain" et "rester exposé".
- Cohérent avec la règle de Greenblatt qui suggère de réduire (sans préciser le %) quand prix > 2× fair value.

**Cas où l'utilisateur peut dévier** (à documenter dans l'UI comme tooltip explicatif) :

| Situation                                              | Recommandation        |
|--------------------------------------------------------|-----------------------|
| Ratio > 3× fair value                                  | Vendre 50%            |
| Position à très haute conviction long terme + thèse intacte | Vendre 0% (conserver) |
| Besoin de cash pour autre opportunité                  | Vendre 50% ou plus    |
| Position fiscalement avantageuse (PEA, plus-value LT)  | Vendre 33% standard   |

**Paramétrabilité** : valeur configurable via `DCA_PARTIAL_SELL_PCT_AT_2X=0.33` dans `.env`. L'utilisateur peut ajuster sans modifier le code.

## 6.4 Logique de rebasing automatique

Le fair value n'est pas figé. Il est **recalculé à chaque rafraîchissement** depuis les données actuelles. Conséquence : une position en SUSPENDU peut redevenir ACTIVE si les fondamentaux se sont améliorés.

```python
def update_position_status(position: dict) -> dict:
    """
    Recalcule le statut DCA d'une position.
    Appelé à chaque ouverture du dashboard et 1× par jour en background.
    """
    symbol = position["symbol"]
    current_price = get_current_price(symbol)

    # Recalcul complet du fair value (4 méthodes)
    valuation = compute_valuation(symbol)

    if not valuation["available"]:
        return {
            "symbol": symbol,
            "status": "FAIR_VALUE_UNAVAILABLE",
            "previous_status": position.get("dca_status"),
            "warning": "Fair value non calculable, statut DCA gelé",
        }

    new_fv = valuation["fair_value_median"]
    old_fv = position.get("last_fair_value")

    # Calcul nouveau statut
    new_status = compute_dca_status(
        current_price=current_price,
        fair_value_median=new_fv,
        dispersion=valuation["dispersion"],
    )

    # Détection de changement
    status_changed = (
        position.get("dca_status") != new_status["status"]
    )

    if old_fv and abs(new_fv - old_fv) / old_fv > 0.10:
        rebasing_event = {
            "type": "FAIR_VALUE_REBASED",
            "old_value": old_fv,
            "new_value": new_fv,
            "change_pct": (new_fv - old_fv) / old_fv,
            "trigger": "fundamentals_evolution",
        }
    else:
        rebasing_event = None

    return {
        "symbol": symbol,
        **new_status,
        "fair_value": new_fv,
        "current_price": current_price,
        "ratio": current_price / new_fv,
        "status_changed": status_changed,
        "rebasing_event": rebasing_event,
    }
```

### 6.4.1 Exemple concret de rebasing

```
T0 (mai 2026)
  AIXTRON : prix 50€, fair value calculé = 50€, ratio 1.0
  Statut : ACTIF_NEUTRE (DCA 70%)

T1 (juillet 2026)
  AIXTRON : publie 2 trimestres en hausse forte
  Recalcul fair value : 65€ (méthodes mises à jour)
  Prix : 55€
  Ratio : 55/65 = 0.85
  Statut : ACTIF (DCA 100%) → réactivé automatiquement
  Rebasing event détecté et loggé

T2 (octobre 2026)
  AIXTRON : prix 80€, fair value 65€ (stable)
  Ratio : 1.23
  Statut : SUSPENDU (DCA 0%)
  Réactivation à 61.75€ (65 × 0.95)
```

L'utilisateur n'a rien à faire, le statut bascule automatiquement.

## 6.5 UI cible portfolio

```
┌──────────────────────────────────────────────────────────────────┐
│ Portfolio (3 positions, 252$ + 59$ cash)                         │
├──────────────────────────────────────────────────────────────────┤
│ AIXTRON  │ 38.50€ │ FV 65€  │ Ratio 0.59 │ ACTIF      │ DCA 100%│
│ RIO      │ 78.20$ │ FV 75$  │ Ratio 1.04 │ ACTIF NEUTRE│ DCA 70%│
│ EOG      │ 130.4$ │ FV 145$ │ Ratio 0.90 │ ACTIF      │ DCA 100%│
├──────────────────────────────────────────────────────────────────┤
│ Cash : 59$ disponible pour redéploiement                         │
│ Suggestion : redéployer sur AIXTRON (ratio 0.59, sous-évalué)    │
└──────────────────────────────────────────────────────────────────┘
```

**Note** : exemple à 3 positions, alignées avec le portefeuille réel actuel. À terme cible 6-10 positions (cf. partie 1.1).

---

# PARTIE 7 — Paliers de conviction et allocation

## 7.1 Score de conviction unifié

**Usage unique** : déterminer le palier d'allocation pour les **nouvelles entrées**.

**Pas d'autre usage** :
- Pas pour les positions existantes (utiliser matrice DCA partie 6)
- Pas pour la priorisation watchlist (utiliser ratio prix/fair value)
- Pas pour les décisions de vente

## 7.2 Construction du score de conviction

```python
def compute_conviction_score(
    investissable: dict,
    valuation: dict,
    entry: dict,
) -> dict:
    """
    Score de conviction 0-100, calculé pour les nouvelles entrées uniquement.
    Combine la qualité fondamentale (50%), la valorisation (30%),
    et l'état technique (20%).
    """
    if investissable["verdict"] != "OUI":
        return {
            "score": 0,
            "tier": "NONE",
            "reason": "INVESTISSABLE non OUI, pas de conviction calculable",
        }

    # Composante 1 — Qualité fondamentale (50%)
    quality_score = investissable["quality_score"]
    # déjà 0-100

    # Composante 2 — Valorisation (30%)
    val_ratio = valuation.get("ratio_price_to_fair_value")
    if val_ratio is None:
        valuation_score = 50  # neutre si non calculable
    elif val_ratio < 0.85:
        valuation_score = 100  # sous-évaluée
    elif val_ratio < 0.95:
        valuation_score = 85   # légère décote
    elif val_ratio < 1.05:
        valuation_score = 60   # juste prix
    elif val_ratio < 1.5:
        valuation_score = 20   # surcote modérée
    else:
        valuation_score = 0    # surcote forte

    # Composante 3 — État technique (20%)
    scenario = entry.get("scenario", "UNKNOWN")
    technical_score = {
        "TENDANCE_HAUSSIÈRE_FORTE":    70,  # bon mais pull-back préférable
        "CORRECTION_DANS_HAUSSE":       95,  # idéal pour entrée
        "GOLDEN_CROSS_FORMATION":       80,
        "CONSOLIDATION":                40,  # incertain
        "SOUS_MA200_BAISSE":            30,  # difficile
        "PIÈGE":                         0,  # à éviter
    }.get(scenario, 50)

    # Combinaison
    conviction = (
        0.50 * quality_score +
        0.30 * valuation_score +
        0.20 * technical_score
    )

    # Détermination du palier
    if conviction >= 80:
        tier = "VERY_HIGH"
        allocation_pct = 0.45
    elif conviction >= 65:
        tier = "HIGH"
        allocation_pct = 0.30
    elif conviction >= 50:
        tier = "MEDIUM"
        allocation_pct = 0.20
    else:
        tier = "LOW"
        allocation_pct = 0.0

    return {
        "score": round(conviction, 1),
        "tier": tier,
        "allocation_pct": allocation_pct,
        "components": {
            "quality":    quality_score,
            "valuation":  valuation_score,
            "technical":  technical_score,
        },
        "weights": {"quality": 0.50, "valuation": 0.30, "technical": 0.20},
    }
```

## 7.3 Paliers d'allocation

| Tier      | Score      | % du DCA mensuel | Justification                              |
|-----------|------------|------------------|---------------------------------------------|
| VERY_HIGH | ≥ 80       | 40-50 % (centre 45 %) | Conviction maximale, allocation forte     |
| HIGH      | 65-80      | 25-35 % (centre 30 %) | Bonne conviction                          |
| MEDIUM    | 50-65      | 15-25 % (centre 20 %) | Conviction modérée                        |
| LOW       | < 50       | 0 % (skip)            | Conviction insuffisante, redéploiement   |

**Important** : les % indiqués sont les centres des fourchettes. La fourchette permet à l'utilisateur d'ajuster manuellement (ex: VERY_HIGH = 45 % par défaut, mais l'utilisateur peut choisir 50 % s'il veut maximiser ou 40 % s'il veut rester prudent). Le terminal **propose** le centre, l'utilisateur **valide** avant chaque DCA.

## 7.4 Cas particuliers

```
CAS 1 — Conviction HIGH mais déjà 30 % du portefeuille sur ce ticker
  → Plafonner allocation à 5 % pour éviter sur-concentration
  → Avertissement : "Tu as déjà 30 % sur AAPL, allocation limitée à 5 %"
  → Logique : portfolio analytics (concentration HHI)

CAS 2 — Conviction VERY_HIGH mais size_adjustment 0.6 (synthèse ENTRÉE_PRUDENTE)
  → Allocation effective = 45 % × 0.6 = 27 %
  → Tooltip : "Conviction haute mais valorisation incertaine"

CAS 3 — Conviction LOW + watchlist active sur autre ticker à fair value
  → Suggestion : redéployer le DCA mensuel sur ticker en watchlist
  → Notification : "Skip {ticker_low}, considère {ticker_watchlist}"
```

## 7.5 Recalcul de la conviction lors d'une réactivation

**Règle métier** : quand une position passe de SUSPENDU à ACTIF (le prix retombe sous fair value après une période au-dessus), la conviction doit être **recalculée à l'instant T** avec les données actuelles. Pas réutiliser celle figée au moment de l'achat initial.

**Justification** : entre l'achat initial et la réactivation, les fondamentaux peuvent avoir évolué (positivement ou négativement). Continuer à allouer 45% sur une position dont la qualité a baissé serait une erreur. Le palier de conviction doit refléter la réalité de l'instant.

```python
def compute_dca_amount_on_reactivation(
    symbol: str,
    monthly_dca_budget: float,
) -> dict:
    """
    Calcule le montant DCA à allouer lors d'une réactivation
    de position après suspension.
    Recalcule la conviction à l'instant T avec les données fraîches.
    """
    # Recalcul complet du score de conviction avec les données actuelles
    investissable = analyze_investissable(symbol)
    valuation = analyze_valuation(symbol)
    entry = analyze_entry(symbol)

    conviction = compute_conviction_score(investissable, valuation, entry)
    synthesis = compute_synthesis(investissable, valuation, entry)

    # Allocation = palier conviction × size_adjustment de la synthèse
    base_allocation_pct = conviction["allocation_pct"]
    size_adjustment = synthesis.get("size_adjustment", 1.0)

    # Cas spécial : conviction tombée à LOW
    if conviction["tier"] == "LOW":
        return {
            "action": "DO_NOT_REACTIVATE",
            "reason": "Conviction insuffisante au moment de la réactivation",
            "recommendation": "Maintenir la position mais ne pas renforcer",
            "previous_dca_pct_to_log": "to_log_for_history",
            "new_dca_pct": 0.0,
        }

    effective_pct = base_allocation_pct * size_adjustment
    monthly_amount = monthly_dca_budget * effective_pct

    return {
        "action": "REACTIVATE",
        "monthly_amount": round(monthly_amount, 2),
        "conviction_tier": conviction["tier"],
        "size_adjustment": size_adjustment,
        "effective_pct": effective_pct,
        "rationale": (
            f"Réactivation à {effective_pct*100:.0f}% du DCA "
            f"(conviction {conviction['tier']} × adjustment {size_adjustment})"
        ),
    }
```

**Logique métier importante** : si la conviction est tombée à LOW au moment de la réactivation :
- La position est **maintenue** (pas de vente automatique)
- Le DCA reste **suspendu** (pas de renforcement)
- L'utilisateur est notifié : "Position {symbol} réactivable mais conviction baissée. Décide manuellement de garder, renforcer ou sortir."
- L'historique des paliers de conviction est loggé pour analyse a posteriori

---

# PARTIE 8 — Portfolio analytics

## 8.1 Partie 1 — Calculs purs (étape 3 de la roadmap)

Indépendants du SCORE refondu. Peuvent être implémentés en parallèle de SEC EDGAR.

### 8.1.1 Matrice de corrélation des returns

```python
def compute_correlation_matrix(
    positions: list[str],
    period_days: int = 252,
) -> pd.DataFrame:
    """
    Calcule la matrice de corrélation des returns journaliers
    sur 252 jours (1 an de trading).
    """
    returns = {}
    for symbol in positions:
        prices = get_ohlcv(symbol, days=period_days + 1)
        returns[symbol] = prices["close"].pct_change().dropna()

    df = pd.DataFrame(returns)
    correlation_matrix = df.corr(method="pearson")
    return correlation_matrix
```

**Interprétation** :
- ρ > 0.7 : très corrélé (diversification illusoire)
- 0.4 < ρ < 0.7 : modérément corrélé
- 0 < ρ < 0.4 : faiblement corrélé (bonne diversification)
- ρ < 0 : décorrélé (excellent)

**Affichage UI** : grille 10×10 max, code couleur du rouge (corrélé) au vert (décorrélé). Pour le portfolio actuel (RIO, EOG, AIXTRON), grille 3×3 simple.

### 8.1.2 Concentration sectorielle (HHI)

```python
def compute_sector_concentration(positions: list[dict]) -> dict:
    """
    Herfindahl-Hirschman Index sur les secteurs.
    HHI = somme des carrés des parts sectorielles.
    """
    sector_weights = {}
    total_value = sum(p["value_usd"] for p in positions)

    for p in positions:
        sector = p["sector"]
        weight = p["value_usd"] / total_value
        sector_weights[sector] = sector_weights.get(sector, 0) + weight

    hhi = sum(w**2 for w in sector_weights.values())

    # Normalisation : HHI varie de 1/n (uniforme) à 1 (mono-concentré)
    n_sectors = len(sector_weights)
    hhi_min = 1 / n_sectors if n_sectors > 0 else 0

    if hhi < 0.15:
        diversity = "EXCELLENT"
    elif hhi < 0.25:
        diversity = "BON"
    elif hhi < 0.40:
        diversity = "MODÉRÉ"
    else:
        diversity = "CONCENTRÉ"

    return {
        "hhi": round(hhi, 4),
        "n_sectors": n_sectors,
        "weights": {k: round(v, 3) for k, v in sector_weights.items()},
        "diversity_label": diversity,
        "max_sector_weight": max(sector_weights.values()),
        "max_sector": max(sector_weights, key=sector_weights.get),
    }
```

**Seuils HHI** (norme retail simplifiée) :
- < 0.15 : excellent (diversification large)
- 0.15-0.25 : bon
- 0.25-0.40 : modéré
- ≥ 0.40 : concentré (alerte)

### 8.1.3 Exposition devise consolidée

```python
def compute_currency_exposure(positions: list[dict]) -> dict:
    """
    Consolide l'exposition par devise.
    Une action en USD a 100 % d'exposition USD.
    """
    currency_weights = {}
    total_value = sum(p["value_usd"] for p in positions)

    for p in positions:
        currency = p["listing_currency"]  # USD, EUR, GBP, etc.
        weight = p["value_usd"] / total_value
        currency_weights[currency] = currency_weights.get(currency, 0) + weight

    home_currency = "EUR"
    foreign_exposure = sum(
        w for c, w in currency_weights.items() if c != home_currency
    )

    return {
        "by_currency": {k: round(v, 3) for k, v in currency_weights.items()},
        "home_currency": home_currency,
        "foreign_exposure_total": round(foreign_exposure, 3),
        "alert_if_foreign_above_70pct": foreign_exposure > 0.70,
    }
```

**Note** : pour un investisseur EUR DCA, exposition USD/GBP > 70 % = alerte. Pas un blocage, juste une info pour qu'il sache qu'il subit le risque change EUR/USD.

### 8.1.4 Beta agrégé du portefeuille

```python
def compute_portfolio_beta(positions: list[dict]) -> dict:
    """
    Beta du portefeuille = somme pondérée des betas individuels.
    """
    total_value = sum(p["value_usd"] for p in positions)
    weighted_beta = sum(
        (p["value_usd"] / total_value) * p["beta"]
        for p in positions
        if p.get("beta") is not None
    )

    if weighted_beta < 0.8:
        profile = "DÉFENSIF"
    elif weighted_beta < 1.2:
        profile = "ÉQUILIBRÉ"
    else:
        profile = "AGRESSIF"

    return {
        "portfolio_beta": round(weighted_beta, 2),
        "profile": profile,
        "interpretation": (
            f"Pour 1 % de mouvement S&P, ton portefeuille bouge ≈ {weighted_beta:.2f} %"
        ),
    }
```

### 8.1.5 Sortie API portfolio analytics partie 1

```json
{
  "portfolio_summary": {
    "n_positions": 3,
    "total_value_usd": 252.00,
    "cash_usd": 59.00
  },
  "correlation_matrix": {
    "RIO":     {"RIO": 1.0,  "EOG": 0.72, "AIXTRON": 0.31},
    "EOG":     {"RIO": 0.72, "EOG": 1.0,  "AIXTRON": 0.28},
    "AIXTRON": {"RIO": 0.31, "EOG": 0.28, "AIXTRON": 1.0}
  },
  "correlation_alerts": [
    "RIO et EOG fortement corrélés (ρ=0.72) - exposition commodities concentrée"
  ],
  "sector_concentration": {
    "hhi": 0.42,
    "diversity_label": "CONCENTRÉ",
    "weights": {
      "Basic Materials": 0.40,
      "Energy": 0.30,
      "Technology": 0.30
    },
    "alert": "HHI 0.42 > 0.40 : portefeuille concentré, élargir progressivement"
  },
  "currency_exposure": {
    "by_currency": {"USD": 0.55, "EUR": 0.30, "GBP": 0.15},
    "foreign_exposure_total": 0.70
  },
  "portfolio_beta": {
    "portfolio_beta": 1.15,
    "profile": "ÉQUILIBRÉ"
  }
}
```

## 8.2 Partie 2 — Simulation d'ajout (étape 5 de la roadmap)

Dépend du SCORE refondu (besoin de scorer le candidat avant simulation).

### 8.2.1 Logique de simulation

```python
def simulate_position_add(
    current_portfolio: list[dict],
    candidate_symbol: str,
    proposed_amount_usd: float,
) -> dict:
    """
    Simule l'impact d'ajouter une nouvelle position au portefeuille.
    """
    # Score le candidat
    candidate_analysis = analyze_ticker(candidate_symbol)
    if not candidate_analysis["investissable"]["verdict"] == "OUI":
        return {
            "feasible": False,
            "reason": "Candidat non INVESTISSABLE",
            "details": candidate_analysis,
        }

    # Construit le portfolio simulé
    simulated_portfolio = current_portfolio + [{
        "symbol": candidate_symbol,
        "value_usd": proposed_amount_usd,
        "sector": candidate_analysis["sector"],
        "listing_currency": candidate_analysis["currency"],
        "beta": candidate_analysis["beta"],
    }]

    # Recalcule les métriques
    new_correlation = compute_correlation_matrix([p["symbol"] for p in simulated_portfolio])
    new_sector = compute_sector_concentration(simulated_portfolio)
    new_currency = compute_currency_exposure(simulated_portfolio)
    new_beta = compute_portfolio_beta(simulated_portfolio)

    # Compare aux métriques actuelles
    current_sector = compute_sector_concentration(current_portfolio)
    current_currency = compute_currency_exposure(current_portfolio)
    current_beta = compute_portfolio_beta(current_portfolio)

    # Calcule corrélation moyenne du candidat avec le portefeuille existant
    candidate_correlations = {
        p["symbol"]: new_correlation.loc[candidate_symbol, p["symbol"]]
        for p in current_portfolio
    }
    avg_correlation = mean(candidate_correlations.values())

    # Détermine le verdict
    impacts = []
    if new_sector["hhi"] > current_sector["hhi"] + 0.05:
        impacts.append({"type": "SECTOR_CONCENTRATION_INCREASE", "severity": "WARNING"})
    if avg_correlation > 0.7:
        impacts.append({"type": "HIGH_CORRELATION_WITH_EXISTING", "severity": "WARNING"})
    if new_currency["foreign_exposure_total"] > 0.80:
        impacts.append({"type": "EXCESSIVE_FOREIGN_EXPOSURE", "severity": "WARNING"})

    return {
        "feasible": True,
        "candidate": {
            "symbol": candidate_symbol,
            "conviction_tier": candidate_analysis["conviction"]["tier"],
            "investissable_verdict": candidate_analysis["investissable"]["verdict"],
            "valuation_verdict": candidate_analysis["valuation"]["verdict"],
        },
        "current_metrics": {
            "hhi": current_sector["hhi"],
            "beta": current_beta["portfolio_beta"],
            "foreign_exposure": current_currency["foreign_exposure_total"],
        },
        "simulated_metrics": {
            "hhi": new_sector["hhi"],
            "beta": new_beta["portfolio_beta"],
            "foreign_exposure": new_currency["foreign_exposure_total"],
            "avg_correlation_with_existing": avg_correlation,
        },
        "impacts": impacts,
        "recommendation": "PROCEED" if not impacts else "CAUTION",
    }
```

### 8.2.2 UI cible simulation

```
┌──────────────────────────────────────────────────────────┐
│ Simuler l'ajout d'une position                           │
├──────────────────────────────────────────────────────────┤
│ Candidat : [MSFT_______]  Montant : [40$_____]            │
│                                                            │
│ AVANT (3 positions)            APRÈS (4 positions)         │
│ HHI sectoriel : 0.42           → 0.32 ✓ amélioration       │
│ Beta porte.   : 1.15           → 1.13 ≈ stable             │
│ Expo étranger : 70 %           → 72 % ⚠ légère hausse     │
│ Corrélation moyenne avec exist.: -                          │
│   • RIO  ρ=0.18                                            │
│   • EOG  ρ=0.15                                            │
│   • AIXTRON ρ=0.42                                         │
│ → moyenne 0.25 ✓ bonne diversification                    │
│                                                            │
│ Verdict : PROCEED — bonne diversification                 │
└──────────────────────────────────────────────────────────┘
```

**Note exemple** : MSFT utilisé comme candidat illustratif. La logique est valable pour tout candidat scoré INVESTISSABLE.

---

# PARTIE 9 — Roadmap d'exécution

## 9.1 Vue d'ensemble en 7 étapes

```
ÉTAPE 1 — Halal Terminal en gate AAOIFI
  ↓
ÉTAPE 2 — SEC EDGAR intégration              ┐
  ↓                                          │  parallèles
ÉTAPE 3 — Portfolio Analytics partie 1       ┘
  ↓
ÉTAPE 4 — Refonte SCORE en gates + qualité
  ↓
ÉTAPE 5 — Suppression XGBoost + Portfolio Analytics partie 2
  ↓
ÉTAPE 6 — Couche de synthèse 3-onglets + bandeau verdict
  ↓
ÉTAPE 7 — Raffinements (au fil de l'eau)
```

## 9.2 Détail par étape

### Étape 1 — Halal Terminal en gate AAOIFI

**Effort** : 1 session, ~6h
**Dépendances** : aucune
**Livrables** :
1. Inscription Halal Terminal API + récupération clé
2. Création `app/integration/halal_terminal_client.py`
3. Création `app/services/shariah_service.py` avec couche `ShariahCustomThresholds`
4. Tests sur AAPL, MSFT, RIO, AIXTRON
5. Intégration dans le pipeline d'analyse

**Validation** :
- Verdict cohérent vs Musaffa pour 5 tickers de référence
- Ratios bruts exposés correctement
- Seuils personnels appliqués (debt/MC ≤ 30%, etc.)

### Étape 2 — SEC EDGAR

**Effort** : 1-2 sessions, ~8h
**Dépendances** : aucune
**Livrables** :
1. Création `app/integration/sec_edgar_client.py`
2. Implémentation lookup CIK
3. Implémentation `get_company_facts(cik)`
4. Implémentation `get_concept(cik, taxonomy, tag)`
5. Implémentation `get_recent_filings(cik, form_type)`
6. Cache Redis (TTL 24h)
7. Tests sur 5 tickers US

**Validation** :
- Validation croisée Revenue, Net Income, Debt vs YFinance pour 5 tickers
- Écart < 5 % sur métriques critiques

### Étape 3 — Portfolio Analytics partie 1

**Effort** : 1 session, ~6h
**Dépendances** : aucune (peut tourner en parallèle de l'étape 2)
**Livrables** :
1. Création `app/services/portfolio_analytics_service.py`
2. Implémentation matrice de corrélation (Pearson 252j)
3. Implémentation HHI sectoriel
4. Implémentation exposition devise
5. Implémentation beta agrégé
6. Endpoint `/api/v1/portfolio/analytics`
7. UI : grille de corrélation + cards des métriques

**Validation** : sur le portfolio actuel (RIO, EOG, AIXTRON), affichage des 4 métriques avec interprétation.

### Étape 4 — Refonte SCORE en gates + qualité

**Effort** : 2-3 sessions, ~15h
**Dépendances** : étapes 1 et 2
**Livrables** :
1. Refactor `app/services/investissable_service.py` selon partie 4.2
2. Implémentation des 3 gates (AAOIFI, Altman, fraud)
3. Implémentation du score qualité (5 composantes)
4. Refactor `app/services/valuation_service.py` selon partie 4.3
5. Implémentation des 4 méthodes fair value
6. Endpoints dédiés `/api/v1/investissable/{symbol}` et `/api/v1/valuation/{symbol}`
7. Tests sur 10 tickers (large/mid/small caps + intl)

**Validation** :
- Verdicts cohérents avec analyses manuelles sur 10 tickers
- Pas de score sur composantes manquantes (rebalancing OK)
- Traçabilité complète (chaque verdict explicable)

### Étape 5 — Suppression XGBoost + Portfolio Analytics partie 2

**Effort** : 1 session, ~6h
**Dépendances** : étape 4
**Livrables** :
1. Suppression `app/ml/*` (XGBoost, predictor, trainer, features, targets)
2. Suppression `app/api/v1/endpoints/prediction.py`
3. Suppression onglet PRÉVISIONS frontend
4. Retrait dépendance `xgboost` de `requirements.txt`
5. Implémentation `simulate_position_add()` (partie 8.2)
6. Endpoint `/api/v1/portfolio/simulate`
7. UI simulation d'ajout

**Validation** :
- Application démarre sans XGBoost
- Simulation produit verdict cohérent sur 3 cas de test

### Étape 6 — Couche de synthèse 3-onglets + bandeau verdict

**Effort** : 1-2 sessions, ~10h
**Dépendances** : étapes 4 et 5
**Livrables** :
1. Création `app/services/synthesis_service.py` avec les 6 règles
2. Endpoint `/api/v1/synthesis/{symbol}`
3. Composant frontend `<SynthesisBanner />` toujours visible
4. Tests unitaires des 6 règles avec cas limites
5. Refactor frontend pour restructurer les 3 onglets

**Validation** :
- Les 6 règles produisent les verdicts attendus sur 6 cas de test
- Le bandeau s'affiche correctement avec les 3 sections (couleurs)
- Cohérence entre bandeau et contenu des onglets

### Étape 7 — Raffinements

**Effort** : étalé, ~10h cumulé
**Dépendances** : étape 6
**Livrables incrémentaux** :
1. Scénarios A/B/C/D/E avec pente MA50 (partie 4.4.2)
2. Score conviction unifié documenté (partie 7)
3. Fibonacci avec volume profile (partie 4.4.3)
4. Régime macro en info contextuelle (sortie de la pondération)
5. Note de transparence sur biais de survie (si backtest plus tard)
6. Logging structuré des divergences inter-sources

**Validation** : itérative, au fil de l'usage réel.

## 9.3 Estimation cumulée

| Étape       | Effort estimé | Cumul  |
|-------------|---------------|--------|
| Étape 1     | 6h            | 6h     |
| Étape 2     | 8h            | 14h    |
| Étape 3     | 6h (parallèle)| 14h    |
| Étape 4     | 15h           | 29h    |
| Étape 5     | 6h            | 35h    |
| Étape 6     | 10h           | 45h    |
| Étape 7     | 10h (étalé)   | 55h    |

**Total estimé : ~55h de développement** sur 4-6 semaines de travail à temps partiel.

---

# PARTIE 10 — Annexes techniques

## 10.1 Variables d'environnement

```bash
# .env (à configurer dans Railway)

# ── Sources existantes ──
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
FMP_API_KEY=...
FRED_API_KEY=...

# ── Sources à ajouter ──
HALAL_TERMINAL_API_KEY=...     # ÉTAPE 1
FINNHUB_API_KEY=...             # ÉTAPE 2 ou 5
SEC_EDGAR_USER_AGENT="FinTerminal Personal Project ton_email@example.com"  # ÉTAPE 2

# ── Auth API minimale (anti-bot) ──
# Génération : python -c "import uuid; print(uuid.uuid4())"
# Cf. master-prompt.md §9.6
FINTERMINAL_API_KEY=550e8400-e29b-41d4-a716-446655440000

# ── Cache et infra ──
REDIS_URL=...
DATABASE_URL=...                # PostgreSQL Railway

# ── App ──
APP_ENV=production
LOG_LEVEL=INFO
CORS_ORIGINS=https://ton-frontend.railway.app
RATE_LIMIT_PER_MINUTE=30

# ── Configuration métier (paramétrable) ──
# Seuils Shariah personnels
SHARIAH_DEBT_TO_MARKETCAP_MAX=0.30
SHARIAH_IMPURE_REVENUE_MAX=0.03
SHARIAH_INTEREST_INCOME_MAX=0.03
SHARIAH_CASH_TO_MARKETCAP_MAX=0.30

# Budget et portefeuille
DCA_MONTHLY_BUDGET_EUR=200
DCA_MIN_TRANCHE_EUR=25                  # seuil min d'achat par tranche
DCA_PARTIAL_SELL_PCT_AT_2X=0.33         # vente partielle si ratio >= 2x FV
PORTFOLIO_TARGET_POSITIONS_MIN=6
PORTFOLIO_TARGET_POSITIONS_MAX=10

# Stop loss
STOP_LOSS_ATR_MULTIPLIER=2.0            # K dans MA200 - K×ATR

# Concentration
CONCENTRATION_HHI_ALERT_THRESHOLD=0.40
SECTOR_MAX_WEIGHT_ALERT=0.35
FOREIGN_EXPOSURE_ALERT=0.70
```

**Frontend env** (Railway frontend, fichier `.env` séparé) :

```bash
# Mêmes valeurs que backend pour le mur anti-bot
VITE_FINTERMINAL_API_KEY=550e8400-e29b-41d4-a716-446655440000
VITE_API_URL=https://finterminal-back.up.railway.app
```

## 10.2 Patterns de gestion d'erreur

```python
# Pattern standard pour les services externes

async def fetch_with_fallback(symbol: str, primary_fn, fallback_fns: list) -> dict:
    """
    Pattern de fallback en cascade.
    """
    try:
        result = await primary_fn(symbol)
        if result is not None:
            return {"data": result, "source": primary_fn.__name__}
    except Exception as e:
        logger.warning(f"Primary source failed for {symbol}: {e}")

    for fallback in fallback_fns:
        try:
            result = await fallback(symbol)
            if result is not None:
                return {
                    "data": result,
                    "source": fallback.__name__,
                    "primary_failed": True,
                }
        except Exception as e:
            logger.warning(f"Fallback {fallback.__name__} failed: {e}")

    return {
        "data": None,
        "source": None,
        "all_failed": True,
        "warning": "Aucune source disponible",
    }
```

## 10.3 Logging structuré pour validation croisée

```python
def log_cross_validation(
    symbol: str,
    metric: str,
    sources: dict,
    chosen_source: str,
    deviation: float,
):
    """
    Log structuré pour analyser les divergences inter-sources a posteriori.
    """
    logger.info(
        "cross_validation",
        extra={
            "symbol": symbol,
            "metric": metric,
            "sources": sources,
            "chosen_source": chosen_source,
            "deviation_pct": deviation * 100,
            "warning": deviation > 0.05,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
```

## 10.4 Liste des warnings utilisateur

Warnings à afficher dans le frontend selon situation :

| Code warning              | Message utilisateur                                       | Couleur |
|---------------------------|-----------------------------------------------------------|---------|
| DATA_INCOMPLETE_SMALL_CAP | Données incomplètes — verdict moins confiant              | Orange  |
| ALTMAN_GREY_ZONE          | Zone grise Altman — surveillance renforcée                | Orange  |
| DISPERSION_HIGH           | Méthodes de fair value en désaccord — confiance réduite   | Orange  |
| AAOIFI_NOT_COVERED        | Halal Terminal ne couvre pas ce ticker                    | Rouge   |
| SEC_EDGAR_UNAVAILABLE     | Source officielle US indisponible (action internationale) | Gris    |
| API_DOWN                  | Source X temporairement indisponible                      | Orange  |
| HIGH_CORRELATION_PORTFOLIO| Cette position serait fortement corrélée à existant       | Orange  |
| EXCESSIVE_CONCENTRATION   | Cette position augmenterait la concentration sectorielle  | Orange  |
| FAIR_VALUE_NOT_COMPUTABLE | Fair value non calculable (ex: pas d'EPS positif)         | Gris    |

## 10.5 Tolérances de validation croisée

| Métrique           | Tolérance | Action si dépassement                      |
|--------------------|-----------|---------------------------------------------|
| Revenue TTM        | 5 %       | Préférer SEC EDGAR, log warning             |
| Net Income TTM     | 5 %       | Idem                                        |
| Total Debt         | 5 %       | Idem                                        |
| Total Cash         | 5 %       | Idem                                        |
| EPS TTM            | 3 %       | Tolérance plus stricte (impact valorisation)|
| Free Cash Flow     | 5 %       | Idem Revenue                                |
| Interest Income    | 10 %      | Tolérance plus large (donnée volatile)      |

## 10.6 Convention de nommage des services

```
app/services/
├── shariah_service.py              # Gate AAOIFI personnel
├── investissable_service.py        # Onglet 1 - gates + qualité
├── valuation_service.py            # Onglet 2 - 4 méthodes fair value
├── entry_service.py                # Onglet 3 - plan d'entrée
├── synthesis_service.py            # Bandeau de synthèse - 6 règles
├── portfolio_analytics_service.py  # Portfolio metrics
└── data_orchestrator_service.py    # Orchestration data multi-sources
```

## 10.7 Zones grises résiduelles

Points à trancher lors de la construction (non bloquants pour le démarrage).

| # | Zone grise                                       | Quand trancher              |
|---|--------------------------------------------------|------------------------------|
| 1 | Construction des peer groups par secteur         | Étape 4                      |
| 2 | Médianes sectorielles (FCF/NI, investment intensity) | Étape 4 (calculées depuis peers) |
| 3 | Heuristique `_detect_business_change` AAOIFI     | Étape 1, raffinement post-V1 |
| 4 | Seuils HHI sectoriels précis (0.40 alerte)       | Étape 3, calibration usage   |
| 5 | Fenêtre temporelle volume profile (90j ? 252j ?) | Étape 7                      |
| 6 | Comportement DCA si VRAIMENT toutes en SUSPENDU  | À voir si ça arrive en prod  |
| 7 | Backtest de la stratégie complète sur 10 ans     | Phase ultérieure (V2)         |

**Note v2.1** : la zone grise initiale "Stop loss structurel exact" est tranchée. K=2 par défaut, paramétrable via `STOP_LOSS_ATR_MULTIPLIER`.

## 10.8 Tableau récapitulatif des décisions clés

| Décision                              | Statut    | Section |
|---------------------------------------|-----------|---------|
| Découpage en 3 onglets profonds       | Validé    | 4       |
| Bandeau de synthèse permanent         | Validé    | 4.5     |
| Suppression XGBoost et PRÉVISIONS J+1 | Validé    | 2.1     |
| Suppression Kelly Criterion           | Validé    | 2.2     |
| Suppression TP ATR-based              | Validé    | 2.3     |
| SL structurel MA200 - K×ATR (K=2)     | Validé v2.1 | 2.3, 4.4.3 |
| Refonte SCORE en gates + qualité      | Validé    | 4.2     |
| Suppression D2 dividende du score     | Validé v2.1 | 4.2.2 |
| Formule D3 = (CapEx + R&D) / Revenue  | Validé v2.1 | 4.2.2 |
| Détecteur fraude normalisé sectoriel  | Validé v2.1 | 4.2.1 |
| Halal Terminal en gate AAOIFI         | Validé    | 5       |
| Seuils personnels stricts AAOIFI      | Validé    | 5.1     |
| 4 méthodes fair value sans DCF        | Validé    | 6.1     |
| Matrice DCA face au fair value        | Validé    | 6.3     |
| Harmonisation seuils synthèse/DCA     | Validé v2.1 | 4.5, 6.3 |
| Logique de rebasing automatique       | Validé    | 6.4     |
| Vente partielle 33% paramétrable      | Validé v2.1 | 6.3.2 |
| Paliers conviction 45/30/20/skip      | Validé    | 7.3     |
| Score conviction = usage unique       | Validé    | 7.1     |
| Recalcul conviction sur réactivation  | Validé v2.1 | 7.5   |
| Seuil min tranche DCA 25€             | Validé v2.1 | 4.4.4 |
| Cible 6-10 positions portfolio        | Validé    | 1.1     |
| Portfolio analytics en deux parties   | Validé    | 8       |
| SEC EDGAR canonique pour US           | Validé    | 3.2.2   |
| Régime macro = info contextuelle      | Validé    | 3.2.6   |
| Check Israël hors terminal            | Validé    | 1.3 P5  |

---

## Notes de fin

Ce document est la **spec de référence** pour la construction de FinTerminal v2.0.
Il doit être mis à jour lors de chaque itération significative.
Toute déviation entre ce document et le code mérite une discussion avant implémentation.

**Prochaine étape** : construction du master prompt technique pour Claude Code, en s'appuyant sur cette spec comme source unique de vérité.

**Fin du document.**
