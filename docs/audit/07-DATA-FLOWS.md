# 07 — Séquences détaillées (use-cases)

## 1. User clique "Analyser AAPL" (cold cache)

```
USER                  FRONTEND                                    BACKEND
 │ tape "AAPL" + Analyser                                              │
 │ ───────────────────►                                                 │
 │                     setState(symbol="AAPL")                            │
 │                     setActiveTab("GÉNÉRALE")                            │
 │                     navigate(/analyse/AAPL)                            │
 │                                                                       │
 │                     ① SynthesisBanner mount                            │
 │                       useEffect([symbol]) → fetchSynthesis("AAPL")    │
 │                     GET /api/v1/synthesis/AAPL ───────────────────►   │
 │                                                                       │
 │                                                       compute_synthesis│
 │                                                       async session × 3│
 │                                                       asyncio.gather :  │
 │                                                                       │
 │                                                       ╔═══════════════╗│
 │                                                       ║ Halal branch  ║│
 │                                                       ║   ┌─────────┐ ║│
 │                                                       ║   │ DB       │ ║│
 │                                                       ║   │ screen_  │ ║│
 │                                                       ║   │ history  │ ║│
 │                                                       ║   │ cache miss│ ║│
 │                                                       ║   └─────────┘ ║│
 │                                                       ║   ┌─────────┐ ║│
 │                                                       ║   │ Halal API│ ║│
 │                                                       ║   │ GET screen│ ║│
 │                                                       ║   └─────────┘ ║│
 │                                                       ║   PASS persist║│
 │                                                       ╚═══════════════╝│
 │                                                       ╔═══════════════╗│
 │                                                       ║ Inv branch    ║│
 │                                                       ║  shariah cache║│
 │                                                       ║  HIT          ║│
 │                                                       ║  ┌──────────┐ ║│
 │                                                       ║  │ SEC EDGAR│ ║│
 │                                                       ║  │ company_ │ ║│
 │                                                       ║  │ facts    │ ║│
 │                                                       ║  │ cache miss│║│
 │                                                       ║  └──────────┘ ║│
 │                                                       ║  ┌──────────┐ ║│
 │                                                       ║  │ SEC EDGAR│ ║│
 │                                                       ║  │ submiss. │ ║│
 │                                                       ║  └──────────┘ ║│
 │                                                       ║  altman.compute║│
 │                                                       ║  fraud.compute ║│
 │                                                       ║  piotroski    ║│
 │                                                       ║  growth       ║│
 │                                                       ║  capital      ║│
 │                                                       ║  → OUI        ║│
 │                                                       ╚═══════════════╝│
 │                                                       ╔═══════════════╗│
 │                                                       ║ Val branch    ║│
 │                                                       ║ SEC facts HIT ║│
 │                                                       ║ ┌───────────┐ ║│
 │                                                       ║ │ YFinance  │ ║│
 │                                                       ║ │ get_info  │ ║│
 │                                                       ║ │ cache miss│ ║│
 │                                                       ║ └───────────┘ ║│
 │                                                       ║ ┌───────────┐ ║│
 │                                                       ║ │ YFinance  │ ║│
 │                                                       ║ │ get_hist  │ ║│
 │                                                       ║ │ cache miss│ ║│
 │                                                       ║ └───────────┘ ║│
 │                                                       ║ 4 méthodes :  ║│
 │                                                       ║  M1 P/E hist ✓║│
 │                                                       ║  M2 sector ✗ ║│
 │                                                       ║  M3 Graham ✓ ║│
 │                                                       ║  M4 target ✓ ║│
 │                                                       ║  → NON SUR   ║│
 │                                                       ╚═══════════════╝│
 │                                                                       │
 │                                                       _aggregate(...) │
 │                                                       overall_verdict :│
 │                                                       REQUIRES_REVIEW │
 │                                                                       │
 │                     ◄───────────────────── SynthesisReport JSON (200)   │
 │                     setState({ status: 'success', report })            │
 │                                                                       │
 │                     ② GeneraleTab mount (activeTab default)            │
 │                       useEffect([prefillSymbol]) → fetchFinancials    │
 │                     GET /api/v1/financials/AAPL ──────────────────►   │
 │                                                                       │
 │                                                       financials_cache│
 │                                                       HIT (déjà fetched│
 │                                                       par Inv branch)  │
 │                                                                       │
 │                     ◄────────────────── FinancialsSnapshot JSON (200)  │
 │                                                                       │
 │ Banner rendu :                                                          │
 │  [HALAL ✓] [INVESTISSABLE ✓] [VALORISÉE ✗ SURÉVALUÉE]                  │
 │  SYNTHÈSE ▸ ⚠ À VÉRIFIER MANUELLEMENT                                  │
 │ GÉNÉRALE rendu : Apple Inc. + 9 concepts XBRL                          │
```

**Total appels externes au premier chargement** :
- 1 Halal Terminal API
- 2 SEC EDGAR (company_facts + submissions)
- 2 YFinance (info + history)
= 5 round-trips externes, ~3-5s sur cold cache, < 200 ms sur cache hit ultérieur.

## 2. User clique sur l'onglet INVESTISSABLE

```
USER                  FRONTEND                                    BACKEND
 │ click INVESTISSABLE                                                  │
 │ ──────►            setActiveTab("INVESTISSABLE")                       │
 │                    InvestissablePanel mount                            │
 │                      useEffect([prefillSymbol]) → fetchInvestissable  │
 │                    GET /api/v1/investissable/AAPL ──────────────────► │
 │                                                                       │
 │                                                  compute_investissable│
 │                                                  • shariah cache HIT  │
 │                                                  • SEC facts cache HIT│
 │                                                  • SEC submissions    │
 │                                                    in-memory cache HIT│
 │                                                  • 3 gates + 5 quality│
 │                                                  • Toutes les         │
 │                                                    calculation_detail │
 │                                                    sérialisées        │
 │                                                                       │
 │                    ◄──────────────── InvestissableReport JSON (200)   │
 │                                                                       │
 │ Panel rendu :                                                          │
 │   Verdict OUI — QUALITÉ EXCELLENTE — score 74.1/100                    │
 │   3 cartes Gates (AAOIFI ✓, Altman ✓ avec accordéon, Fraud ✓ avec     │
 │     3 sous-accordéons : fcf_ni, receivables, restatements)            │
 │   5 cartes Quality (Piotroski 78.0 + accordéon, Growth 82.0 + acc.,   │
 │     Smart Money N/A, Capital 70.0 + 2 sous-accordéons D1+D3, ES N/A)  │
 │                                                                       │
 │ click "▶ Calcul détaillé — ALTMAN Z'' (FAILLITE)"                      │
 │   → accordéon expand                                                   │
 │   → affiche formula + variables + intermediates + steps + thresholds  │
 │     + interpretation directement depuis le payload                     │
 │   → aucun appel API                                                    │
```

## 3. User ajoute une transaction Portfolio

```
USER                  FRONTEND                                    BACKEND
 │ click PORTFOLIO    setActiveNav("portfolio")                          │
 │ ──────────►        PortfolioTab mount                                  │
 │                    useEffect → refresh()                               │
 │                                                                       │
 │                    ╔═══ Promise.all ═══╗                              │
 │                    ║ fetchSummary       ║ GET /api/v1/portfolio/summary│
 │                    ║ fetchPositions     ║ GET /api/v1/portfolio/positions
 │                    ║ fetchTransactions  ║ GET /api/v1/portfolio/transactions
 │                    ╚═══════════════════╝ (en parallèle)               │
 │                                                                       │
 │                    State = success { summary, positions, transactions }│
 │                    Render KPIs + table positions + table transactions  │
 │                                                                       │
 │ tape "AAPL" / 10 / 237.42 / 2025-05-14                                 │
 │ click + Ajouter                                                        │
 │ ──────────►        validate inputs                                     │
 │                    POST /api/v1/portfolio/transactions ─────────────► │
 │                       body : { symbol, currency=USD, date, qty=10,    │
 │                                price=237.42, fee=0 }                  │
 │                                                                       │
 │                                                  create_transaction    │
 │                                                  • _find_or_create_pos │
 │                                                  • oversell guard      │
 │                                                  • INSERT transaction   │
 │                                                                       │
 │                    ◄──────────────── TransactionRead JSON (201)        │
 │                                                                       │
 │                    setForm(EMPTY_FORM)                                  │
 │                    refresh() ← re-trigger les 3 fetchers en parallèle │
 │                                                                       │
 │                    Render updated UI (positions row added + KPIs updated)│
```

## 4. YFinance rate-limited en cours de session

```
SCENARIO : Yahoo blocks Railway IP for 1 minute.
USER                  FRONTEND                                    BACKEND
 │ tape "MSFT"                                                          │
 │ ──────────►        SynthesisBanner refetch                           │
 │                    GET /api/v1/synthesis/MSFT ───────────────────►   │
 │                                                                      │
 │                                                  Halal branch ✓     │
 │                                                  Inv branch  ✓     │
 │                                                  Val branch  ─       │
 │                                                    YF get_info → None│
 │                                                    YF get_history →  │
 │                                                      None             │
 │                                                    current_price=None │
 │                                                    M1 : history=None  │
 │                                                      → available=false│
 │                                                    M3 Graham ✓ (SEC) │
 │                                                    M4 : info=None     │
 │                                                      → available=false│
 │                                                    n_methods = 1      │
 │                                                    → INDÉTERMINÉ      │
 │                                                                      │
 │                    ◄────────── SynthesisReport (status 200)          │
 │                       valuation.verdict = "INDÉTERMINÉ"               │
 │                       warnings += "YFinance: current price            │
 │                                    indisponible..."                   │
 │                                                                      │
 │ Banner :                                                              │
 │   [HALAL ✓ PASS] [INVESTISSABLE ✓] [VALORISÉE ⚠ INDÉTERMINÉ]          │
 │   SYNTHÈSE ▸ ⚠ À VÉRIFIER MANUELLEMENT                                │
 │   • YFinance: current price indisponible — verdict global              │
 │     INDÉTERMINÉ même si méthodes calculables.                          │
 │                                                                      │
 │ click VALORISÉE                                                       │
 │ ──────────►        GET /api/v1/valuation/MSFT ───────────────────►   │
 │                    ◄──────────────── ValuationReport (status 200)     │
 │                       3 méthodes : M1 ✗ M2 ✗ M3 ✓ M4 ✗                │
 │                                                                      │
 │ Panel rendu :                                                          │
 │   Verdict INDÉTERMINÉ — confidence N/A — 1/4 méthodes                  │
 │   Strip KPIs : Fair Value médian $XX.XX — Prix actuel — — — — — — — — │
 │   Méthode 1 : indisponible (raison "YFinance n'a pas retourné...")    │
 │   Méthode 2 : indisponible (raison "V2 FMP")                          │
 │   Méthode 3 : ✓ disponible $XX.XX + accordéon Graham                  │
 │   Méthode 4 : indisponible (raison "YFinance .info indisponible")     │
```

Le frontend reçoit **status 200** dans tous les cas — l'UX reste fluide, l'utilisateur voit immédiatement la raison.

## 5. Halal Terminal FAIL → cascade BLOCKED

```
SCENARIO : User tape un ticker non halal (ex. casino, alcool).
USER                  FRONTEND                                    BACKEND
 │ tape "WYNN" (casino)                                                  │
 │ ──────────►        GET /api/v1/synthesis/WYNN ───────────────────►   │
 │                                                                      │
 │                                                  Halal branch:       │
 │                                                  • Halal Terminal     │
 │                                                    aggregate = false  │
 │                                                  → verdict FAIL       │
 │                                                  → CACHED 7 days       │
 │                                                                      │
 │                                                  Inv + Val branches : │
 │                                                  également exécutées  │
 │                                                  en parallèle (pas    │
 │                                                  court-circuit niveau │
 │                                                  synthesis_service)  │
 │                                                                      │
 │                                                  _aggregate :         │
 │                                                  halal.verdict==FAIL  │
 │                                                  → overall_verdict =   │
 │                                                    "BLOCKED"           │
 │                                                  → label = "NON HALAL─│
 │                                                    bloqué au filtrage │
 │                                                    éthique"           │
 │                                                                      │
 │                    ◄────────────────── SynthesisReport (200)          │
 │                                                                      │
 │ Banner :                                                              │
 │   [HALAL ✗ FAIL] [INVESTISSABLE ✓] [VALORISÉE varying]                │
 │   SYNTHÈSE ▸ ✗ NON HALAL — bloqué au filtrage éthique                  │
 │                                                                      │
 │ Le user voit immédiatement le STOP éthique en haut de page.            │
```

**Garantie cascade halal** : même si investissable + valuation retournent des verdicts positifs, le `overall_verdict` reste `BLOCKED`. Aucun override frontend possible (cf. test `test_halal_fail_short_circuits_to_blocked` étape 6).
