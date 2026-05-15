# 06 — Composants frontend (partie Analyse)

## 1. Arborescence des fichiers Analyse

```
src/
├── App.tsx                         (composant racine + routing + tabs dispatch)
├── pages/
│   ├── Home.tsx                    (landing avant analyse)
│   └── AnalysisDashboard.tsx       (page Analyse — header + chart + banner + tabs)
├── components/
│   ├── Sidebar.tsx                 (nav latérale : ANALYSE + PORTFOLIO)
│   ├── SearchBar.tsx               (input + bouton Analyser)
│   ├── TradingChart.tsx            (placeholder V2 — ~52px)
│   ├── PortfolioTab.tsx            (page Portfolio, USD only)
│   └── Analyse/
│       ├── SynthesisBanner.tsx     (bandeau cascade 3 couches en haut)
│       ├── GeneraleTab.tsx         (onglet GÉNÉRALE — financials SEC)
│       ├── ShariahPanel.tsx        (onglet AAOIFI)
│       ├── InvestissablePanel.tsx  (onglet INVESTISSABLE + SCORE alias)
│       ├── ValuationPanel.tsx      (onglet VALORISÉE)
│       ├── CalendarTab.tsx         (onglet CALENDRIER — Étape 8C)
│       ├── MgmtTab.tsx             (onglet MGMT — Étape 8C)
│       ├── HoldersTab.tsx          (onglet HOLDERS — Étape 8C)
│       ├── ScoreTab.tsx            (alias mince → InvestissablePanel)
│       ├── PlanTab.tsx             (onglet PLAN — synthesis vue détaillée)
│       ├── ComingSoonTab.tsx       (placeholder V2 — RELS, RISQUE)
│       ├── ExpandableCalc.tsx      (accordéon transparence — Étape 8B)
│       └── RelsTab.tsx             (legacy V2)
└── api/
    ├── client.ts                   (apiFetch + BASE_URL + auth header)
    ├── shariah.ts                  (fetchShariahScreen)
    ├── financials.ts               (fetchFinancials)
    ├── investissable.ts            (fetchInvestissable)
    ├── valuation.ts                (fetchValuation)
    ├── synthesis.ts                (fetchSynthesis)
    ├── portfolio.ts                (fetch + create + delete)
    └── yfinanceTabs.ts             (fetchCalendar + fetchManagement + fetchHolders)
```

## 2. SynthesisBanner.tsx

**Source** : `/api/v1/synthesis/{symbol}` (étape 6)
**Position** : juste au-dessus de la barre d'onglets, dans `AnalysisDashboard`

```
┌───────────────────────────────────────────────────────────────┐
│ ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│ │ HALAL ✓     │  │ INVESTISSABLE ✓  │  │ VALORISÉE ✗      │   │
│ │ PASS        │  │ QUALITÉ EXC.    │  │ SURÉVALUÉE        │   │
│ └─────────────┘  └──────────────────┘  └──────────────────┘   │
│ SYNTHÈSE ▸  ⚠ À VÉRIFIER MANUELLEMENT                          │
└───────────────────────────────────────────────────────────────┘
```

États : idle (caché) / loading (skeletons) / success (3 badges + label) / error (alert inline).

Tone mapping :
- `PASS` / `OUI` / `OUI_NEUTRE` → vert
- `FAIL` / `NON` → rouge
- `ERROR` / `INCERTAIN` / `INDÉTERMINÉ` / `NOT_COVERED` / unavailable → ambre
- Autres → gris

Badges **inertes** en V1 (pas de navigation au clic — Étape 6 Q2).

## 3. InvestissablePanel.tsx (étape 7.1 + transparence étape 8B)

**Source** : `/api/v1/investissable/{symbol}`

**Sections** :
1. Bandeau verdict (OUI/NON/INCERTAIN + label + quality_score)
2. Section "3 gates bloquants (§4.2.2)" — 3 cartes (AAOIFI, Altman, Fraud)
3. Section "5 composantes qualité (§4.2.3)" — 5 cartes (Piotroski, Growth, Smart Money, Capital, Earnings Stability)
4. Data completeness footer
5. Warnings éventuels

**Accordéons exposés** (Étape 8B) :
- GateCard Altman → `gate.details.calculation_detail`
- GateCard Fraud → 3 sous-accordéons via `gate.details.signals[*].calculation_detail` (Signal fcf_ni, receivables, restatements)
- QualityCard Piotroski → `component.details.calculation_detail`
- QualityCard Growth → idem
- QualityCard Capital Allocation → 2 sous-accordéons `details.d1.calculation_detail` + `details.d3.calculation_detail`

→ **8 accordéons distincts** auditables par ticker.

## 4. ValuationPanel.tsx (étape 7.1 + transparence étape 8B)

**Source** : `/api/v1/valuation/{symbol}`

**Sections** :
1. Bandeau verdict (OUI/OUI_NEUTRE/NON/INDÉTERMINÉ + label + confidence + n_methods/4)
2. Strip KPIs : Fair Value médian, Prix actuel, Ratio, Dispersion
3. Section "4 méthodes" — 4 cartes (vs_historical_5y, vs_sector, graham_number, analyst_target)
4. Raison/warnings éventuels

**Accordéons exposés** (Étape 8B) : 3 méthodes LIVE (M1, M3, M4) → `method.details.calculation_detail` chacune. M2 sans payload (scaffold V2) → l'accordéon auto-hide.

## 5. GeneraleTab.tsx (étape 7.2 — refactor)

**Source** : `/api/v1/financials/{ticker}`

**Sections** :
1. Bandeau verdict (AVAILABLE/NOT_COVERED/ERROR + entity_name + CIK)
2. Raison si non-AVAILABLE
3. KPIs filing (fiscal_year, period_end, form, filed)
4. 9 concepts XBRL annuels en cartes (Revenus, Net Income, EPS dilué, OCF, Capex, Total Actifs, Equity, LT Debt, Cash & Equiv.)
5. Source + cache footer + accession number

Formatter `fmtBigUSD` : 391 035 000 000 → "$391.04 B".

## 6. ShariahPanel.tsx (étape 1.5)

**Source** : `/api/v1/shariah/{symbol}`

Self-contained (input + bouton Analyser propre). En V1 free-tier mode, les 4 checks détaillés + raw_ratios sont vides → affiche le verdict agrégé + raison.

**Sections** : Verdict banner, reason (si ERROR / NOT_COVERED), 4 checks bloquants (présents en mode complet seulement), 5 methodology verdicts, metadata footer, ratios bruts dans `<details>` collapsable.

## 7. CalendarTab.tsx (étape 8 Phase C)

**Source** : `/api/v1/calendar/{symbol}`

**Sections** :
1. KPI strip : next earnings, ex-dividend, next dividend
2. Tableau "Historique earnings" — Période, EPS estimé, EPS réalisé, **Surprise %** colorée beat (vert) / miss (rouge), Date
3. Tableau "Historique dividendes" — Date, Montant

État dégradé si `report.available === false` : bandeau ambre avec raison.

## 8. MgmtTab.tsx (étape 8 Phase C)

**Source** : `/api/v1/management/{symbol}`

Tableau dirigeants : Nom (bold), Titre, Âge, Rémunération totale (M/B compact), Options exercées (M/B), Né en.

Formatter `fmtUSDBig` : 16 000 000 → "$16.00 M".

## 9. HoldersTab.tsx (étape 8 Phase C)

**Source** : `/api/v1/holders/{symbol}`

**3 sections** :
1. "Répartition globale" — label / value (% institutional / % insider)
2. "Top 10 institutionnels" — Holder, Shares, Value, % Out, Date
3. "Transactions insiders" — Date, Insider, **Transaction colorée** (Sale = rouge, Buy = vert), Shares, Value

Lenient sur la shape de `major_holders` (varie selon versions yfinance).

## 10. PortfolioTab.tsx (étape 7.3 refactor)

**Sources** : `/api/v1/portfolio/{summary,positions,transactions}` + POST + DELETE

**Sections** :
1. KPI strip 8 cellules (total invested, current value, P&L latent, P&L réalisé, P&L total, P&L %, n_positions, n_transactions)
2. Formulaire d'ajout : Symbol / BUY-SELL / Qty / Prix / Date / Fees / bouton Ajouter
3. Tableau positions avec colonnes : Symbol, Qty, Avg cost, Cost basis, Last price (avec tag `live/tx/n.a.`), Current value, Unrealized, Realized
4. Tableau transactions avec colonnes : Date, Symbol, Côté (BUY vert / SELL rouge), Qty, Prix, Frais, bouton DELETE

**Decimal-safe display** : strings du backend → formatter `fmtUSD` `fmtUSDSigned` `fmtPct` via `Intl.NumberFormat` (jamais arithmétique JS).

**USD only V1** : currency hardcoded dans le form. Backend rejette 400 sinon.

## 11. PlanTab.tsx (étape 7.2)

**Source** : `/api/v1/synthesis/{symbol}` (réutilise la même endpoint que SynthesisBanner pour vue détaillée)

**Sections** :
1. Bandeau verdict global (INVESTABLE/NOT_INVESTABLE/REQUIRES_REVIEW/BLOCKED)
2. 3 layer cards (Halal, Investissable, Valorisée) avec verdict + label + erreur si unavailable
3. Warnings + Errors sections (collapsable list)
4. Footer computed_at

Légère : ~250 lignes (vs 1003 du legacy supprimé).

## 12. ScoreTab.tsx (étape 7.2 — alias DRY)

```tsx
export default function ScoreTab({ ticker }: { ticker?: string }) {
  return ticker ? <InvestissablePanel prefillSymbol={ticker} /> : <InvestissablePanel />
}
```

Évite la duplication : SCORE et INVESTISSABLE pointent vers la même endpoint backend.

## 13. ComingSoonTab.tsx (étape 7.2)

Placeholder partagé pour les 2 onglets V2 restants (RELS, RISQUE).

```tsx
<ComingSoonTab feature="Risque" reason="..." eta="V2" />
```

Affiche : icône, "Bientôt disponible" (uppercase), feature, raison technique, "ETA : V2".

## 14. ExpandableCalc.tsx (étape 8 Phase B)

Composant partagé pour rendre `CalculationDetail` (formula + variables + intermediates + steps + result + thresholds + interpretation).

**Comportement** :
- Collapsed par défaut (bouton avec `▶ Calcul détaillé — <title>`)
- Si `result` présent, affichage du chip `= result` sur la ligne du toggle
- Au clic : déploie un panel avec 7 sections labellées
- Hide automatique si `detail` est null/undefined/empty

**Accessibilité** :
- `<button>` avec `aria-expanded` + `aria-controls`
- Sections labellisées avec uppercase headers
- Read-only strict : aucune arithmétique côté frontend (préserve la fidélité Decimal)

## 15. TradingChart.tsx (étape 7.3 placeholder)

Strip compact ~52px en haut du dashboard. Texte : "📊 Graphique de prix — {SYMBOL} · Bientôt disponible — V2".

L'ancien composant 326 lignes avec `useSWR` sur `/api/v1/ticker/{s}/price` (mort) a été remplacé. Bloquait l'UX en "Chargement…" indéfini sur ~50 % de la hauteur écran.

## 16. AnalysisDashboard.tsx — composition

```tsx
const TABS = [
  'GÉNÉRALE', 'AAOIFI', 'INVESTISSABLE', 'VALORISÉE',
  'RISQUE', 'CALENDRIER', 'MGMT', 'RELS', 'HOLDERS',
  'SCORE', 'PLAN',
]

return (
  <div className="flex flex-col h-full overflow-hidden bg-[#08090a]">
    {/* Header : nom + prix + SearchBar */}
    <Header symbol={...} />

    {/* Chart placeholder Étape 7.3A */}
    <div className="shrink-0 px-7 py-2 border-b">
      <TradingChart key={symbol} symbol={symbol} />
    </div>

    {/* Bandeau synthèse Étape 6 */}
    <SynthesisBanner symbol={symbol} />

    {/* Tabs nav */}
    <div className="shrink-0 flex px-7 border-b">
      {TABS.map(tab => <button onClick={() => setActiveTab(tab)}>{tab}</button>)}
    </div>

    {/* Tab content (renderTabContent in App.tsx) */}
    <div className="flex-1 overflow-y-auto">{children}</div>
  </div>
)
```

## 17. App.tsx — dispatch tabs

```tsx
const renderTabContent = () => {
  if (activeTab === 'GÉNÉRALE')      return <GeneraleTab       prefillSymbol={symbol} />
  if (activeTab === 'AAOIFI')         return <ShariahPanel      prefillSymbol={symbol} />
  if (activeTab === 'INVESTISSABLE') return <InvestissablePanel prefillSymbol={symbol} />
  if (activeTab === 'VALORISÉE')     return <ValuationPanel    prefillSymbol={symbol} />
  if (activeTab === 'RISQUE')         return <ComingSoonTab feature="Risque" reason="..." />
  if (activeTab === 'CALENDRIER')    return <CalendarTab       prefillSymbol={symbol} />
  if (activeTab === 'MGMT')           return <MgmtTab           prefillSymbol={symbol} />
  if (activeTab === 'RELS')           return <ComingSoonTab feature="Relations corporate" reason="..." />
  if (activeTab === 'HOLDERS')        return <HoldersTab        prefillSymbol={symbol} />
  if (activeTab === 'SCORE')          return <ScoreTab          ticker={symbol} />
  if (activeTab === 'PLAN')           return <PlanTab           ticker={symbol} />
  return null
}
```

## 18. Pattern useEffect commun (panels)

Tous les panels self-fetch suivent le même pattern :

```tsx
useEffect(() => {
  const sym = (prefillSymbol ?? '').trim().toUpperCase()
  if (!sym) { setState({ status: 'idle' }); return }
  let cancelled = false
  setState({ status: 'loading' })
  void (async () => {
    try {
      const data = await fetchX(sym)
      if (!cancelled) setState({ status: 'success', data })
    } catch (e) {
      if (cancelled) return
      setState({ status: 'error', message: e instanceof Error ? e.message : String(e) })
    }
  })()
  return () => { cancelled = true }
}, [prefillSymbol])
```

État `FetchState` typé en union discriminée (`idle | loading | success | error`).
