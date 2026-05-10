# Master Prompt — FinTerminal v2.1

> **Document opérationnel pour Claude Code (Opus, mode planification)**
> Version 1.0 · Mai 2026
> Compagnon de la spec métier `finterminal-spec.md` v2.1
>
> Ce prompt définit comment construire FinTerminal v2.1 sans dériver, sans inventer, sans introduire d'incohérences entre les couches. La spec métier est la source unique de vérité. Ce document explique **comment** la coder.

---

## Table des matières

1. [Référencement de la spec](#1--r%C3%A9f%C3%A9rencement-de-la-spec)
2. [Architecture cible](#2--architecture-cible)
3. [Conventions de code](#3--conventions-de-code)
4. [Contrat API back ↔ front](#4--contrat-api-back--front)
5. [Gestion d'erreurs et fallbacks](#5--gestion-derreurs-et-fallbacks)
6. [Logging structuré](#6--logging-structur%C3%A9)
7. [Tests minimum](#7--tests-minimum)
8. [Cohérence entre couches](#8--coh%C3%A9rence-entre-couches)
9. [Sécurité](#9--s%C3%A9curit%C3%A9)
10. [Frontend spécifique](#10--frontend-sp%C3%A9cifique)
11. [Roadmap d'exécution incrémentale](#11--roadmap-dex%C3%A9cution-incr%C3%A9mentale)
12. [Liste de DO NOT](#12--liste-de-do-not)
13. [Critères de validation par étape](#13--crit%C3%A8res-de-validation-par-%C3%A9tape)
14. [Format de sortie attendu](#14--format-de-sortie-attendu)

---

# 1 · Référencement de la spec

## Règle d'or

**La spec métier `finterminal-spec.md` v2.1 est la source unique de vérité.**

Pour chaque fichier que tu codes, tu dois pouvoir répondre à : *"À quelle section de la spec correspond ce code ?"* Si tu ne peux pas répondre, c'est que tu inventes — arrête, demande la clarification.

## Mapping spec → modules à coder

| Section spec       | Module backend à produire                       | Module frontend à produire           |
|--------------------|--------------------------------------------------|--------------------------------------|
| 5.1, 5.2           | `app/services/shariah_service.py`                | `src/api/shariah.ts`                 |
| 5.3                | `app/services/rescreening_service.py`            | `src/components/portfolio/RescreeningAlert.tsx` |
| 4.2                | `app/services/investissable_service.py`          | `src/components/analysis/InvestissableTab.tsx` |
| 4.3                | `app/services/valuation_service.py`              | `src/components/analysis/ValuationTab.tsx` |
| 4.4                | `app/services/entry_service.py`                  | `src/components/analysis/EntryTab.tsx` |
| 4.5, 8 spec        | `app/services/synthesis_service.py`              | `src/components/analysis/SynthesisBanner.tsx` |
| 6.3, 6.4           | `app/services/dca_status_service.py`             | `src/components/portfolio/DCAStatus.tsx` |
| 7.1, 7.2, 7.5      | `app/services/conviction_service.py`             | (consommé par EntryTab)              |
| 8.1                | `app/services/portfolio_analytics_service.py`    | `src/components/portfolio/Analytics.tsx` |
| 8.2                | `app/services/portfolio_simulation_service.py`   | `src/components/portfolio/Simulation.tsx` |
| 3.2.1              | `app/integration/halal_terminal_client.py`       | -                                    |
| 3.2.2              | `app/integration/sec_edgar_client.py`            | -                                    |
| 3.2.7              | `app/integration/finnhub_client.py`              | -                                    |
| 3.3, 5 (validation)| `app/services/data_orchestrator_service.py`      | -                                    |
| 4.4.4 (consolidate_tranches) | inclus dans `entry_service.py`         | -                                    |

## Règle de traçabilité

Chaque fichier de service doit commencer par un docstring de référencement :

```python
"""
Investissable Service.

Implémente l'onglet 1 selon la spec v2.1 partie 4.2.
- Gates bloquants : section 4.2.1 (AAOIFI, Altman, Fraude)
- Score qualité : section 4.2.2 (5 composantes pondérées 30/25/20/15/10)
- Verdict final : section 4.2.3 (compute_investissable_verdict)

Toute déviation de la spec doit faire l'objet d'une discussion explicite
avant implémentation.
"""
```

## Quand tu hésites

**DO** : ouvrir `finterminal-spec.md`, chercher la section correspondante, citer la phrase exacte qui justifie ton choix.

**DON'T** : trancher au feeling parce que "c'est mieux comme ça". Pas mieux. Pas dans la spec.

**DON'T** : inventer une étape intermédiaire ou un calcul auxiliaire qui n'est pas dans la spec. Si la spec dit "fair value médian des 4 méthodes", tu ne calcules pas une moyenne pondérée parce que ça te paraît plus rigoureux.

Si tu trouves un trou dans la spec, **stoppe le code** et liste les ambiguïtés. L'utilisateur tranche.

---

# 2 · Architecture cible

## 2.1 Structure de dossiers backend

```
backend/
├── app/
│   ├── main.py                          # FastAPI app + startup events
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                    # Settings via Pydantic BaseSettings
│   │   ├── thresholds.py                # CONSTANTES SEUILS — source unique
│   │   ├── cache.py                     # Wrapper Redis avec TTL adaptatif
│   │   ├── database.py                  # SQLAlchemy + Alembic setup
│   │   ├── logging.py                   # JSON logger structuré
│   │   ├── exceptions.py                # Exceptions métier typées
│   │   └── security.py                  # CORS, rate limiting
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py                # Aggrégation des routers
│   │       └── endpoints/
│   │           ├── ticker.py            # GET /ticker/{symbol}
│   │           ├── investissable.py     # GET /investissable/{symbol}
│   │           ├── valuation.py         # GET /valuation/{symbol}
│   │           ├── entry.py             # GET /entry/{symbol}
│   │           ├── synthesis.py         # GET /synthesis/{symbol}
│   │           ├── shariah.py           # GET /shariah/{symbol}
│   │           ├── portfolio.py         # CRUD positions
│   │           └── analytics.py         # GET /portfolio/analytics
│   ├── integration/                     # Clients APIs externes
│   │   ├── __init__.py
│   │   ├── alpaca_client.py
│   │   ├── yfinance_client.py
│   │   ├── fmp_client.py
│   │   ├── fred_client.py
│   │   ├── gleif_client.py
│   │   ├── halal_terminal_client.py     # NOUVEAU étape 1
│   │   ├── sec_edgar_client.py          # NOUVEAU étape 2
│   │   └── finnhub_client.py            # NOUVEAU étape 5
│   ├── services/                        # Logique métier (cœur)
│   │   ├── __init__.py
│   │   ├── shariah_service.py
│   │   ├── investissable_service.py
│   │   ├── valuation_service.py
│   │   ├── entry_service.py
│   │   ├── synthesis_service.py
│   │   ├── conviction_service.py
│   │   ├── dca_status_service.py
│   │   ├── rescreening_service.py
│   │   ├── portfolio_analytics_service.py
│   │   ├── portfolio_simulation_service.py
│   │   └── data_orchestrator_service.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py                   # Pydantic models (API contracts)
│   │   ├── db.py                        # SQLAlchemy models (DB tables)
│   │   └── enums.py                     # Enums partagés (Verdict, Tier...)
│   └── ml/
│       └── scoring/
│           ├── piotroski.py             # Conservé, réutilisé étape 4
│           ├── altman.py                # Conservé, réutilisé étape 4
│           ├── data_fetcher.py          # Conservé, à raffiner
│           └── normalizer.py            # Conservé
├── alembic/                             # Migrations DB
│   ├── versions/
│   ├── env.py
│   └── alembic.ini
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── services/
│   │   │   ├── test_shariah_service.py
│   │   │   ├── test_investissable_service.py
│   │   │   ├── test_valuation_service.py
│   │   │   ├── test_synthesis_service.py
│   │   │   └── ...
│   │   └── integration/
│   └── e2e/
├── requirements.txt
├── .env.example
├── Procfile
└── pyproject.toml
```

## 2.2 Structure de dossiers frontend

```
frontend/
├── src/
│   ├── main.tsx                         # Entry point, SWRConfig, providers
│   ├── App.tsx                          # Router top-level uniquement (~30 lignes)
│   ├── api/
│   │   ├── client.ts                    # apiFetch + ApiError
│   │   ├── ticker.ts                    # fetchTicker
│   │   ├── investissable.ts             # fetchInvestissable
│   │   ├── valuation.ts                 # fetchValuation
│   │   ├── entry.ts                     # fetchEntry
│   │   ├── synthesis.ts                 # fetchSynthesis
│   │   ├── shariah.ts                   # fetchShariah
│   │   ├── portfolio.ts                 # CRUD portfolio
│   │   └── analytics.ts                 # fetchAnalytics
│   ├── types/
│   │   ├── api.ts                       # Types générés depuis Pydantic
│   │   ├── enums.ts                     # Verdict, Tier, ScenarioType...
│   │   └── domain.ts                    # Types métier composés
│   ├── hooks/
│   │   ├── useTickerAnalysis.ts
│   │   ├── usePortfolioAnalytics.ts
│   │   ├── useDCAStatus.ts
│   │   └── useToast.ts
│   ├── components/
│   │   ├── analysis/
│   │   │   ├── SynthesisBanner.tsx      # Toujours visible top
│   │   │   ├── InvestissableTab.tsx
│   │   │   ├── ValuationTab.tsx
│   │   │   ├── EntryTab.tsx
│   │   │   └── shared/
│   │   │       ├── VerdictBadge.tsx
│   │   │       ├── MetricCard.tsx
│   │   │       └── DataTable.tsx
│   │   ├── portfolio/
│   │   │   ├── PortfolioTable.tsx
│   │   │   ├── DCAStatus.tsx
│   │   │   ├── Analytics.tsx
│   │   │   ├── Simulation.tsx
│   │   │   └── RescreeningAlert.tsx
│   │   ├── common/
│   │   │   ├── Layout.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── LoadingScreen.tsx
│   │   │   └── ErrorBoundary.tsx
│   │   └── macro/
│   │       └── MacroDashboard.tsx
│   ├── pages/
│   │   ├── Home.tsx
│   │   ├── AnalysisPage.tsx              # Container 3 onglets + bandeau
│   │   ├── PortfolioPage.tsx
│   │   └── MacroPage.tsx
│   ├── lib/
│   │   ├── thresholds.ts                # Mirror de app/core/thresholds.py
│   │   ├── formatters.ts                # Number, currency, percent
│   │   └── utils.ts
│   └── styles/
│       └── tailwind.css
├── tests/
│   ├── components/
│   ├── hooks/
│   └── lib/
├── scripts/
│   └── generate-types.ts                # Génère types depuis OpenAPI back
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
└── .env.example
```

## 2.3 Conventions de nommage

### Backend

**Services** : `{domain}_service.py`, classes en PascalCase si stateful, fonctions en snake_case si pures.

**Endpoints** : verbes HTTP standards. `GET /api/v1/{resource}/{id}`, `POST /api/v1/{resource}`, `DELETE /api/v1/{resource}/{id}`. Pas de RPC-style.

**Modèles Pydantic** : suffixes explicites — `TickerRequest`, `TickerResponse`, `InvestissableVerdict`, `ValuationOutput`. Toujours `BaseModel` direct, pas d'héritage profond.

**Variables d'environnement** : `SCREAMING_SNAKE_CASE`, préfixe par domaine (`SHARIAH_*`, `DCA_*`, `STOP_LOSS_*`).

### Frontend

**Composants** : PascalCase, un fichier = un composant. `SynthesisBanner.tsx` exporte `SynthesisBanner`.

**Hooks** : camelCase préfixé `use`. `useTickerAnalysis(symbol: string)`.

**Types** : PascalCase. `InvestissableVerdict`, `ValuationOutput`.

**Constantes** : `SCREAMING_SNAKE_CASE`. Importées depuis `lib/thresholds.ts`.

---

# 3 · Conventions de code

## 3.1 Backend (Python)

### Règles fondamentales

**DO** :
- Python 3.11+ obligatoire
- Type hints partout (`def foo(x: int) -> str:`), pas d'exceptions
- `async/await` pour toute I/O (HTTP, DB, Redis)
- Pydantic v2 pour tous les modèles d'API
- Imports absolus depuis `app.` (pas de relative imports)
- Une fonction = une responsabilité
- Docstrings format Google sur les fonctions publiques

**DON'T** :
- Pas de mutation d'état global (pas de variable module-level mutable)
- Pas de variables magiques (tout vient de `app.core.config.Settings`)
- Pas de `print()`, jamais. Toujours `logger.info/warning/error`
- Pas de `try/except: pass` silencieux
- Pas de boucle synchrone bloquante dans une fonction async (`time.sleep` interdit, `asyncio.sleep` à la place)

### Pattern service standard

```python
# app/services/example_service.py

"""
Example Service.

Implémente la fonctionnalité X selon la spec v2.1 partie Y.Z.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.core.exceptions import DataInsufficientError, ExternalAPIError
from app.core.logging import get_logger
from app.integration import some_client
from app.models.schemas import ExampleRequest, ExampleResponse

logger = get_logger(__name__)


async def compute_example(
    request: ExampleRequest,
) -> ExampleResponse:
    """Compute the example output following spec v2.1 §Y.Z.

    Args:
        request: validated input from the API layer.

    Returns:
        Typed response object.

    Raises:
        DataInsufficientError: when required fields are missing.
        ExternalAPIError: when an external dependency fails irrecoverably.
    """
    # 1. Fetch data with fallback strategy (cf. spec §3.4)
    raw = await _fetch_with_fallback(request.symbol)
    if raw is None:
        logger.warning(
            "data_insufficient",
            extra={"symbol": request.symbol, "service": "example"},
        )
        raise DataInsufficientError(
            f"Cannot compute example for {request.symbol}"
        )

    # 2. Apply pure business logic (no I/O)
    result = _apply_business_rules(raw)

    # 3. Return typed response
    return ExampleResponse(**result)


async def _fetch_with_fallback(symbol: str) -> Optional[dict]:
    """Internal helper. Cf. spec §3.4 fallback strategy."""
    # ... fallback chain ...
    pass


def _apply_business_rules(data: dict) -> dict:
    """Pure function, easily testable. Cf. spec §Y.Z."""
    # ... business logic only ...
    pass
```

### Anti-patterns interdits

**DON'T** :
```python
# Pas d'I/O dans une fonction "pure"
def compute_score(symbol):
    data = requests.get(f"...")  # Mauvais : I/O cachée
    return data.score

# Pas de mutation globale
_cache = {}  # Mauvais : état module-level
def get_data(symbol):
    if symbol not in _cache:
        _cache[symbol] = fetch(symbol)
    return _cache[symbol]

# Pas de magic numbers
def is_overvalued(ratio):
    return ratio > 1.05  # Mauvais : seuil hard-codé

# Toujours utiliser :
def is_overvalued(ratio: float, threshold: float = settings.VALUATION_NEUTRAL_MAX) -> bool:
    return ratio > threshold
```

## 3.2 Frontend (TypeScript)

### Règles fondamentales

**DO** :
- TypeScript strict mode (`"strict": true` dans `tsconfig.json`)
- React 18+ avec hooks fonctionnels uniquement
- Pas de class components, jamais
- État local par défaut (`useState`), Context API uniquement quand prop drilling > 3 niveaux
- SWR pour les fetches GET, fonctions dédiées pour les mutations POST/DELETE
- Tailwind utility classes, pas de CSS inline (sauf valeurs dynamiques calculées en JS)
- Composants ≤ 200 lignes (sinon découper)

**DON'T** :
- Pas de `any` (utiliser `unknown` puis narrow)
- Pas de `// @ts-ignore` ni `// @ts-expect-error` (résoudre le typage)
- Pas de fetch direct dans un composant — toujours via un hook
- Pas de logique métier côté front (le front affiche, le back décide — cf. §12)
- Pas de mémoïsation prématurée (`useMemo`/`useCallback` uniquement après mesure de perf)
- Pas de `localStorage`/`sessionStorage` directement, encapsuler dans un hook

### Pattern composant standard

```tsx
// src/components/analysis/InvestissableTab.tsx

import type { FC } from "react";
import { useTickerAnalysis } from "@/hooks/useTickerAnalysis";
import type { InvestissableVerdict } from "@/types/api";
import { VerdictBadge } from "./shared/VerdictBadge";
import { MetricCard } from "./shared/MetricCard";

interface InvestissableTabProps {
  symbol: string;
}

export const InvestissableTab: FC<InvestissableTabProps> = ({ symbol }) => {
  const { data, error, isLoading } = useTickerAnalysis(symbol);

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={error.message} />;
  if (!data) return null;

  const investissable: InvestissableVerdict = data.investissable;

  return (
    <div className="space-y-4">
      <VerdictBadge verdict={investissable.verdict} label={investissable.label} />
      <GatesSection gates={investissable.gates} />
      <QualitySection components={investissable.quality_components} />
    </div>
  );
};
```

### Pattern hook standard

```ts
// src/hooks/useTickerAnalysis.ts

import useSWR from "swr";
import { fetchTickerAnalysis } from "@/api/ticker";
import type { TickerAnalysis } from "@/types/api";

interface UseTickerAnalysisResult {
  data: TickerAnalysis | undefined;
  error: Error | undefined;
  isLoading: boolean;
  refresh: () => void;
}

export function useTickerAnalysis(symbol: string): UseTickerAnalysisResult {
  const { data, error, isLoading, mutate } = useSWR<TickerAnalysis>(
    symbol ? `ticker-analysis-${symbol}` : null,
    () => fetchTickerAnalysis(symbol),
    {
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    }
  );

  return {
    data,
    error: error as Error | undefined,
    isLoading,
    refresh: () => mutate(),
  };
}
```

---

# 4 · Contrat API back ↔ front

## 4.1 Principe de cohérence forte

Le backend est la **source de vérité** pour tous les schémas. Le frontend les consomme via des types générés automatiquement.

**Workflow** :
1. Tu définis le schéma Pydantic dans `app/models/schemas.py`
2. FastAPI expose le schéma en OpenAPI 3.x sur `/openapi.json`
3. Un script `frontend/scripts/generate-types.ts` génère les types TypeScript dans `src/types/api.ts`
4. Les composants frontend importent ces types

**Règle absolue** : aucun type côté front qui décrit un payload backend ne doit être écrit à la main. Tout passe par la génération.

## 4.2 Génération automatique

```bash
# Côté backend, exporter le schéma OpenAPI
cd backend
python -c "from app.main import app; import json; print(json.dumps(app.openapi()))" > /tmp/openapi.json

# Côté frontend, générer les types
cd frontend
npx openapi-typescript /tmp/openapi.json -o src/types/api.ts
```

Ce script doit être lancé à chaque modification d'un schéma backend. À ajouter en pre-commit hook ou en script `package.json`.

```json
{
  "scripts": {
    "types:generate": "openapi-typescript ../backend/openapi.json -o src/types/api.ts",
    "dev": "npm run types:generate && vite",
    "build": "npm run types:generate && tsc && vite build"
  }
}
```

## 4.3 Schémas Pydantic — exemples normatifs

```python
# app/models/schemas.py

from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator

# ──────────────────────────────────────
# Enums (mirror dans src/types/enums.ts)
# ──────────────────────────────────────

VerdictType = Literal["OUI", "NON", "INCERTAIN"]
ColorType = Literal["GREEN", "ORANGE", "RED", "GREY"]
ConvictionTier = Literal["VERY_HIGH", "HIGH", "MEDIUM", "LOW"]
DcaStatus = Literal[
    "ACTIF", "ACTIF_NEUTRE", "SUSPENDU",
    "SUSPENDU_PRUDENT", "VENTE_PARTIELLE_RECOMMANDÉE"
]

# ──────────────────────────────────────
# Schemas
# ──────────────────────────────────────

class TickerRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=15, pattern=r"^[A-Z0-9.\-]+$")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        return v.upper().strip()


class GateCheckResult(BaseModel):
    """Résultat d'un check individuel dans un gate. Cf. spec §4.2.1."""
    name: str
    passed: bool
    value: Optional[float] = None
    threshold: Optional[float] = None
    rationale: Optional[str] = None


class GateAaoifi(BaseModel):
    """Cf. spec §5.2 + §4.2.1."""
    verdict: Literal["PASS", "FAIL", "ERROR", "NOT_COVERED"]
    failed_checks: list[str] = Field(default_factory=list)
    checks: dict[str, GateCheckResult]
    raw_ratios: dict[str, float] = Field(default_factory=dict)
    methodology_verdicts: dict[str, str] = Field(default_factory=dict)
    as_of_date: Optional[str] = None


class GateAltman(BaseModel):
    """Cf. spec §4.2.1 Gate 2."""
    verdict: Literal["PASS", "WARNING", "FAIL", "INSUFFICIENT_DATA"]
    z_score: Optional[float] = None
    z_variant: Literal["Z", "Z_double_prime"] = "Z"
    zone: Optional[Literal["SAFE", "GREY", "DISTRESS"]] = None


class GateFraud(BaseModel):
    """Cf. spec §4.2.1 Gate 3."""
    verdict: Literal["PASS", "FAIL"]
    positive_signals_count: int
    signals: dict[str, dict]


class QualityComponent(BaseModel):
    """Cf. spec §4.2.2."""
    score: Optional[float] = Field(None, ge=0, le=100)
    available: bool
    details: dict = Field(default_factory=dict)


class InvestissableVerdict(BaseModel):
    """Cf. spec §4.2.3."""
    verdict: VerdictType
    label: str
    quality_score: Optional[float] = Field(None, ge=0, le=100)
    gates: dict[str, GateAaoifi | GateAltman | GateFraud]
    quality_components: dict[str, QualityComponent]
    data_completeness: Literal["FULL", "PARTIAL", "INSUFFICIENT"]
    warnings: list[str] = Field(default_factory=list)


class ValuationMethod(BaseModel):
    """Cf. spec §4.3.1 à §4.3.4."""
    method: str
    available: bool
    fair_value: Optional[float] = None
    details: dict = Field(default_factory=dict)


class ValuationVerdict(BaseModel):
    """Cf. spec §4.3.6."""
    verdict: Literal["OUI", "OUI_NEUTRE", "NON", "INDÉTERMINÉ"]
    label: str
    ratio_price_to_fair_value: Optional[float] = None
    dispersion: Optional[float] = None
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    fair_value_median: Optional[float] = None
    methods: dict[str, ValuationMethod]


class SynthesisDecision(BaseModel):
    """Cf. spec §4.5.2 (compute_synthesis)."""
    decision: Literal[
        "REJET", "INCERTAIN", "ANALYSE_INCOMPLÈTE",
        "ENTRÉE_PLEINE", "ENTRÉE_PRUDENTE", "ENTRÉE_NEUTRE",
        "WATCHLIST", "WATCHLIST_PRUDENT", "REJET_SURCOTE_MASSIVE"
    ]
    reason: str
    color: ColorType
    size_adjustment: float = Field(ge=0, le=1)
    fair_value: Optional[float] = None
    reactivation_price: Optional[float] = None
    show_other_tabs: bool = True
    rationale: Optional[str] = None
```

## 4.4 Règle de versioning du contrat

Si tu modifies un schéma Pydantic existant :
- **Ajout d'un champ optionnel** : OK, rétrocompatible
- **Ajout d'un champ requis** : breaking, incrément de version (`/api/v2/...`)
- **Renommage / suppression** : breaking, incrément de version

Pour FinTerminal v2.1, on reste sur `/api/v1/`. Les évolutions ultérieures pourront passer en v2.

---

# 5 · Gestion d'erreurs et fallbacks

## 5.1 Hiérarchie d'exceptions

```python
# app/core/exceptions.py

class FinTerminalException(Exception):
    """Base exception pour toutes les erreurs métier FinTerminal."""

class DataInsufficientError(FinTerminalException):
    """Données insuffisantes pour calculer un verdict."""

class ExternalAPIError(FinTerminalException):
    """Une API externe a échoué de manière irrécupérable."""
    def __init__(self, source: str, message: str):
        self.source = source
        super().__init__(f"[{source}] {message}")

class HalalTerminalError(ExternalAPIError):
    """Erreur spécifique Halal Terminal — bloquante (gate AAOIFI)."""

class ValidationError(FinTerminalException):
    """Validation métier échouée (différent de Pydantic ValidationError)."""

class TickerNotFoundError(FinTerminalException):
    """Ticker invalide ou non couvert."""
```

## 5.2 Pattern de fallback en cascade

Cf. spec §3.4. Implémentation canonique :

```python
# app/services/data_orchestrator_service.py

from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

async def fetch_with_fallback(
    symbol: str,
    primary: Callable[[str], Awaitable[Optional[T]]],
    fallbacks: list[Callable[[str], Awaitable[Optional[T]]]],
    metric_name: str,
) -> dict:
    """Pattern de fallback en cascade selon spec §3.4.

    Returns:
        dict avec data, source, primary_failed, all_failed.
    """
    sources_tried = []
    try:
        result = await primary(symbol)
        sources_tried.append(primary.__name__)
        if result is not None:
            return {
                "data": result,
                "source": primary.__name__,
                "primary_failed": False,
                "sources_tried": sources_tried,
            }
    except Exception as e:
        logger.warning(
            "primary_source_failed",
            extra={
                "symbol": symbol,
                "source": primary.__name__,
                "metric": metric_name,
                "error": str(e),
            },
        )
        sources_tried.append(f"{primary.__name__}:FAILED")

    for fallback in fallbacks:
        try:
            result = await fallback(symbol)
            sources_tried.append(fallback.__name__)
            if result is not None:
                logger.info(
                    "fallback_succeeded",
                    extra={
                        "symbol": symbol,
                        "source": fallback.__name__,
                        "metric": metric_name,
                    },
                )
                return {
                    "data": result,
                    "source": fallback.__name__,
                    "primary_failed": True,
                    "sources_tried": sources_tried,
                }
        except Exception as e:
            logger.warning(
                "fallback_failed",
                extra={
                    "symbol": symbol,
                    "source": fallback.__name__,
                    "error": str(e),
                },
            )
            sources_tried.append(f"{fallback.__name__}:FAILED")

    return {
        "data": None,
        "source": None,
        "primary_failed": True,
        "all_failed": True,
        "sources_tried": sources_tried,
        "warning": f"Aucune source disponible pour {metric_name}",
    }
```

## 5.3 Cas spécial : Halal Terminal

Cf. spec §3.4. Halal Terminal **n'a pas de fallback** — c'est un gate non-substituable.

```python
async def screen_aaoifi(symbol: str) -> GateAaoifi:
    try:
        response = await halal_terminal_client.screen(symbol)
    except ExternalAPIError as e:
        # Pas de fallback — on bloque les nouvelles entrées
        return GateAaoifi(
            verdict="ERROR",
            checks={},
            raw_ratios={},
            failed_checks=[],
            methodology_verdicts={},
        )

    if response is None:
        return GateAaoifi(
            verdict="NOT_COVERED",
            checks={},
            raw_ratios={},
            failed_checks=[],
            methodology_verdicts={},
        )

    # ... logique normale ...
```

## 5.4 Tableau des comportements API down

Cf. spec §3.4. À implémenter strictement :

| API down       | Exception levée               | Comportement attendu                                    |
|----------------|-------------------------------|---------------------------------------------------------|
| Halal Terminal | `HalalTerminalError`          | Verdict `ERROR`, blocage nouvelle entrée                |
| YFinance       | `ExternalAPIError("yfinance")`| Bascule SEC EDGAR + FMP + Finnhub                       |
| Alpaca         | `ExternalAPIError("alpaca")`  | Bascule YFinance OHLCV                                  |
| FMP            | `ExternalAPIError("fmp")`     | Continue avec YF + warning                              |
| SEC EDGAR      | `ExternalAPIError("sec")`     | Continue avec YF + FMP + warning                        |
| Finnhub        | `ExternalAPIError("finnhub")` | Skip news/sentiment sections                            |
| FRED           | `ExternalAPIError("fred")`    | Cache stale acceptable jusqu'à 7 jours                  |
| GLEIF          | `ExternalAPIError("gleif")`   | Skip section RELS                                       |

## 5.5 Côté frontend

```ts
// src/api/client.ts

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
    public detail?: unknown
  ) {
    super(message);
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;

export async function apiFetch<T>(
  path: string,
  opts: { method?: string; body?: unknown; timeout?: number } = {}
): Promise<T> {
  const { method = "GET", body, timeout = DEFAULT_TIMEOUT_MS } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    clearTimeout(timer);

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new ApiError(
        errBody.detail ?? `HTTP ${res.status}`,
        res.status,
        errBody.code,
        errBody.detail
      );
    }

    return res.json() as Promise<T>;
  } catch (err) {
    clearTimeout(timer);
    if (err instanceof ApiError) throw err;
    if ((err as Error).name === "AbortError") {
      throw new ApiError("Timeout — le serveur ne répond pas", 0, "timeout");
    }
    throw err;
  }
}
```

**Règle UI** : aucune API down ne doit faire crasher le frontend. Toutes les erreurs sont catchées et affichées comme toast ou bandeau dégradé.

---

# 6 · Logging structuré

## 6.1 Configuration

Logs JSON pour Railway log explorer. Format standard :

```python
# app/core/logging.py

import logging
import json
from datetime import datetime

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Champs additionnels via extra=
        if hasattr(record, "symbol"):
            payload["symbol"] = record.symbol
        if hasattr(record, "service"):
            payload["service"] = record.service
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = record.duration_ms
        # ... autres champs structurés ...

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

## 6.2 Événements à logger systématiquement

Cf. spec §10.3. Trois familles d'événements :

### Famille 1 — Appels API externes

```python
async def call_external_api(symbol: str, source: str, fn):
    start = time.perf_counter()
    try:
        result = await fn(symbol)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "external_api_call",
            extra={
                "symbol": symbol,
                "source": source,
                "duration_ms": round(duration_ms, 1),
                "status": "success",
            },
        )
        return result
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "external_api_call",
            extra={
                "symbol": symbol,
                "source": source,
                "duration_ms": round(duration_ms, 1),
                "status": "error",
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        raise
```

### Famille 2 — Validations croisées

```python
def log_cross_validation(
    symbol: str,
    metric: str,
    sources: dict,
    chosen_source: str,
    deviation: float,
):
    logger.info(
        "cross_validation",
        extra={
            "event_type": "cross_validation",
            "symbol": symbol,
            "metric": metric,
            "sources": sources,
            "chosen_source": chosen_source,
            "deviation_pct": round(deviation * 100, 2),
            "warning": deviation > 0.05,
        },
    )
```

### Famille 3 — Verdicts métier

```python
def log_business_verdict(
    symbol: str,
    service: str,
    verdict: str,
    inputs_summary: dict,
):
    """Log tout verdict métier avec ses inputs pour audit posterior."""
    logger.info(
        "business_verdict",
        extra={
            "event_type": "business_verdict",
            "symbol": symbol,
            "service": service,
            "verdict": verdict,
            "inputs_summary": inputs_summary,
        },
    )
```

## 6.3 Niveaux de log

| Niveau   | Quand l'utiliser                                    |
|----------|-----------------------------------------------------|
| DEBUG    | Détails internes en dev. Désactivé en production.   |
| INFO     | Événements business normaux (verdict, fetch OK)     |
| WARNING  | Anomalies non bloquantes (fallback, divergence > 5%)|
| ERROR    | Échec d'opération avec impact utilisateur           |
| CRITICAL | App down, intervention humaine requise              |

**Règle** : `LOG_LEVEL` configurable via env var. Production = INFO. Dev = DEBUG.

---

# 7 · Tests minimum

## 7.1 Frameworks (validés)

- **Backend** : pytest + pytest-asyncio + httpx (pour tests d'endpoints)
- **Frontend** : vitest + @testing-library/react + @testing-library/jest-dom

## 7.2 Couverture cible

Pas de seuil de coverage % imposé. Mais **chaque service métier doit avoir des tests** sur 4 catégories :

1. **Cas nominal** : tous les inputs disponibles, vérifier verdict attendu
2. **Cas data manquante** : composantes None, vérifier que le service ne crash pas et retourne `available: False`
3. **Cas API down** : mock l'exception, vérifier le fallback
4. **Cas limites** : valeurs juste avant/après chaque seuil

## 7.3 Pattern de test backend

```python
# tests/unit/services/test_synthesis_service.py

import pytest
from app.services.synthesis_service import compute_synthesis
from app.models.schemas import (
    InvestissableVerdict, ValuationVerdict, SynthesisDecision,
)

# ──────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────

@pytest.fixture
def investissable_oui():
    return InvestissableVerdict(
        verdict="OUI",
        label="OUI - QUALITÉ EXCELLENTE",
        quality_score=78.5,
        gates={...},  # mocked
        quality_components={...},
        data_completeness="FULL",
    )

@pytest.fixture
def investissable_non():
    return InvestissableVerdict(
        verdict="NON",
        label="NON - QUALITÉ INSUFFISANTE",
        quality_score=35.0,
        gates={...},
        quality_components={...},
        data_completeness="FULL",
    )

# ──────────────────────────────────────
# TESTS DES 6 RÈGLES DE SYNTHÈSE
# Cf. spec §4.5.2 et §4.5.3
# ──────────────────────────────────────

class TestSynthesisRules:
    """Tests de chaque règle de synthèse avec inputs aux frontières."""

    def test_rule_1_investissable_non_returns_rejet(self, investissable_non):
        """RÈGLE 1 : INVESTISSABLE NON → REJET, peu importe valuation."""
        valuation = ValuationVerdict(
            verdict="OUI",
            label="SOUS-ÉVALUÉE",
            ratio_price_to_fair_value=0.80,
            dispersion=0.10,
            confidence="HIGH",
            fair_value_median=100.0,
            methods={},
        )
        result = compute_synthesis(investissable_non, valuation, {})
        assert result.decision == "REJET"
        assert result.color == "RED"

    def test_rule_2_entree_pleine_at_ratio_094(self, investissable_oui):
        """RÈGLE 2 : ratio = 0.94 (juste avant 0.95) → ENTRÉE PLEINE."""
        valuation = self._make_valuation(ratio=0.94, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "ENTRÉE_PLEINE"
        assert result.size_adjustment == 1.0

    def test_rule_2_entree_pleine_boundary_094999(self, investissable_oui):
        """RÈGLE 2 : ratio = 0.94999 → ENTRÉE PLEINE (juste sous le seuil)."""
        valuation = self._make_valuation(ratio=0.94999, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "ENTRÉE_PLEINE"

    def test_rule_3_entree_neutre_at_ratio_095(self, investissable_oui):
        """RÈGLE 3 : ratio = 0.95 (frontière exacte) → ENTRÉE NEUTRE."""
        valuation = self._make_valuation(ratio=0.95, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "ENTRÉE_NEUTRE"
        assert result.size_adjustment == 0.7

    def test_rule_4_watchlist_at_ratio_105(self, investissable_oui):
        """RÈGLE 4 : ratio = 1.05 (frontière exacte) → WATCHLIST."""
        valuation = self._make_valuation(ratio=1.05, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "WATCHLIST"
        assert result.size_adjustment == 0

    def test_rule_5_watchlist_prudent_at_ratio_15(self, investissable_oui):
        """RÈGLE 5 : ratio = 1.5 (frontière exacte) → WATCHLIST_PRUDENT."""
        valuation = self._make_valuation(ratio=1.5, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "WATCHLIST_PRUDENT"

    def test_rule_6_rejet_surcote_at_ratio_2(self, investissable_oui):
        """RÈGLE 6 : ratio = 2.0 (frontière exacte, INCLUSIVE) → REJET_SURCOTE_MASSIVE.

        Note : >= 2.0 (inclusif à gauche) cf. spec v2.1 §4.5.2.
        Tout ratio à 2.0 exact tombe en règle 6.
        """
        valuation = self._make_valuation(ratio=2.0, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "REJET_SURCOTE_MASSIVE"

    def test_rule_5_boundary_just_below_2(self, investissable_oui):
        """RÈGLE 5 : ratio = 1.9999 (juste sous 2.0) → WATCHLIST_PRUDENT."""
        valuation = self._make_valuation(ratio=1.9999, dispersion=0.10)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "WATCHLIST_PRUDENT"

    def test_rule_2_prime_dispersion_high(self, investissable_oui):
        """RÈGLE 2' : ratio < 0.95 mais dispersion > 0.30 → ENTRÉE_PRUDENTE."""
        valuation = self._make_valuation(ratio=0.85, dispersion=0.35)
        result = compute_synthesis(investissable_oui, valuation, {})
        assert result.decision == "ENTRÉE_PRUDENTE"
        assert result.size_adjustment == 0.6

    @staticmethod
    def _make_valuation(ratio: float, dispersion: float) -> ValuationVerdict:
        return ValuationVerdict(
            verdict="OUI" if ratio < 0.95 else "NON",
            label="...",
            ratio_price_to_fair_value=ratio,
            dispersion=dispersion,
            confidence="HIGH",
            fair_value_median=100.0,
            methods={},
        )
```

## 7.4 Pattern de test frontend

```tsx
// tests/components/SynthesisBanner.test.tsx

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SynthesisBanner } from "@/components/analysis/SynthesisBanner";
import type { SynthesisDecision } from "@/types/api";

describe("SynthesisBanner", () => {
  it("displays REJET in red when decision is REJET", () => {
    const decision: SynthesisDecision = {
      decision: "REJET",
      reason: "AAOIFI non conforme",
      color: "RED",
      size_adjustment: 0,
      show_other_tabs: false,
    };
    render(<SynthesisBanner decision={decision} />);

    expect(screen.getByText("REJET")).toBeInTheDocument();
    expect(screen.getByTestId("synthesis-banner")).toHaveClass("bg-red-100");
  });

  it("displays reactivation_price when decision is WATCHLIST", () => {
    const decision: SynthesisDecision = {
      decision: "WATCHLIST",
      reason: "Surévaluée",
      color: "ORANGE",
      size_adjustment: 0,
      reactivation_price: 232.75,
      fair_value: 245,
      show_other_tabs: true,
    };
    render(<SynthesisBanner decision={decision} />);

    expect(screen.getByText(/232.75/)).toBeInTheDocument();
  });
});
```

## 7.5 Tests obligatoires par service

| Service                          | Tests minimum requis                                |
|----------------------------------|-----------------------------------------------------|
| `shariah_service`                | PASS, FAIL, ERROR, NOT_COVERED, seuils frontière    |
| `investissable_service`          | 3 gates × (PASS, FAIL, INSUFFICIENT) + score qualité|
| `valuation_service`              | 4 méthodes × (available, unavailable) + agrégation  |
| `synthesis_service`              | 6 règles × frontières exactes (cf. exemple ci-dessus)|
| `conviction_service`             | 4 tiers × scénarios + cas réactivation              |
| `dca_status_service`             | 5 statuts × frontières exactes                      |
| `portfolio_analytics_service`    | Calculs (corrélation, HHI, exposition) sur fixtures |
| `entry_service`                  | 5 scénarios + consolidate_tranches frontières       |

---

# 8 · Cohérence entre couches

## 8.1 Problème

Les seuils, formules, et règles métier doivent être **identiques** entre :
- Le code backend (services Python)
- Les types frontend (TypeScript)
- La spec (`finterminal-spec.md`)

Une dérive même subtile (ex: backend 1.05, frontend 1.06) = bug silencieux qui produit des verdicts incohérents.

## 8.2 Source unique des seuils — `app/core/thresholds.py`

```python
# app/core/thresholds.py
"""
Seuils métier — source unique de vérité.

Tous les seuils utilisés dans les services métier sont définis ici,
typés et documentés. Aucun seuil ne doit être hard-codé ailleurs.

Référence : spec v2.1.
"""
from dataclasses import dataclass
from app.core.config import settings


@dataclass(frozen=True)
class SynthesisThresholds:
    """Cf. spec §4.5.3 — table récapitulative des 6 règles."""
    UNDERVALUED_MAX: float = 0.95          # < 0.95 = sous-évaluée
    NEUTRAL_MAX: float = 1.05              # 0.95-1.05 = juste prix
    OVERVALUED_MAX: float = 1.5            # 1.05-1.5 = surévaluée
    HEAVILY_OVERVALUED_MAX: float = 2.0    # 1.5-2.0 = forte surcote
    DISPERSION_HIGH: float = 0.30          # dispersion > 0.30 = méthodes désaccord


@dataclass(frozen=True)
class DcaThresholds:
    """Cf. spec §6.3.1 — matrice DCA face au fair value.
    DOIT être identique à SynthesisThresholds (cf. spec §4.5 + §6.3 harmonisation v2.1)."""
    ACTIF_MAX: float = 0.95
    NEUTRAL_MAX: float = 1.05
    SUSPENDU_MAX: float = 1.5
    SUSPENDU_PRUDENT_MAX: float = 2.0
    PARTIAL_SELL_PCT: float = settings.DCA_PARTIAL_SELL_PCT_AT_2X  # 0.33 default
    REACTIVATION_MARGIN: float = 0.95      # Réactivation à fair_value × 0.95


@dataclass(frozen=True)
class ShariahThresholds:
    """Cf. spec §5.1 — seuils personnels stricts."""
    DEBT_TO_MARKETCAP_MAX: float = settings.SHARIAH_DEBT_TO_MARKETCAP_MAX  # 0.30
    CASH_TO_MARKETCAP_MAX: float = settings.SHARIAH_CASH_TO_MARKETCAP_MAX  # 0.30
    IMPURE_REVENUE_MAX: float = settings.SHARIAH_IMPURE_REVENUE_MAX        # 0.03
    INTEREST_INCOME_MAX: float = settings.SHARIAH_INTEREST_INCOME_MAX      # 0.03


@dataclass(frozen=True)
class ConvictionThresholds:
    """Cf. spec §7.3."""
    VERY_HIGH_MIN: float = 80
    HIGH_MIN: float = 65
    MEDIUM_MIN: float = 50
    VERY_HIGH_PCT: float = 0.45
    HIGH_PCT: float = 0.30
    MEDIUM_PCT: float = 0.20


@dataclass(frozen=True)
class AltmanThresholds:
    """Cf. spec §4.2.1 Gate 2."""
    Z_SAFE_MIN: float = 2.99
    Z_GREY_MIN: float = 1.81
    Z_DOUBLE_PRIME_SAFE_MIN: float = 2.6
    Z_DOUBLE_PRIME_GREY_MIN: float = 1.1


@dataclass(frozen=True)
class StopLossThresholds:
    """Cf. spec §2.3 + §4.4.3."""
    ATR_MULTIPLIER: float = settings.STOP_LOSS_ATR_MULTIPLIER  # 2.0 default


@dataclass(frozen=True)
class DcaTrancheThresholds:
    """Cf. spec §4.4.4."""
    MIN_AMOUNT_EUR: float = settings.DCA_MIN_TRANCHE_EUR  # 25 default


# Singletons exportés
SYNTHESIS = SynthesisThresholds()
DCA = DcaThresholds()
SHARIAH = ShariahThresholds()
CONVICTION = ConvictionThresholds()
ALTMAN = AltmanThresholds()
STOP_LOSS = StopLossThresholds()
DCA_TRANCHE = DcaTrancheThresholds()


# ─────────────────────────────────────────
# ASSERTION DE COHÉRENCE
# ─────────────────────────────────────────
# Garantit que SynthesisThresholds == DcaThresholds (cf. harmonisation v2.1)
assert SYNTHESIS.UNDERVALUED_MAX == DCA.ACTIF_MAX
assert SYNTHESIS.NEUTRAL_MAX == DCA.NEUTRAL_MAX
assert SYNTHESIS.OVERVALUED_MAX == DCA.SUSPENDU_MAX
assert SYNTHESIS.HEAVILY_OVERVALUED_MAX == DCA.SUSPENDU_PRUDENT_MAX
```

## 8.3 Mirror frontend — `src/lib/thresholds.ts`

```ts
// src/lib/thresholds.ts
// MIRROR de app/core/thresholds.py
// À mettre à jour conjointement.

export const SYNTHESIS_THRESHOLDS = {
  UNDERVALUED_MAX: 0.95,
  NEUTRAL_MAX: 1.05,
  OVERVALUED_MAX: 1.5,
  HEAVILY_OVERVALUED_MAX: 2.0,
  DISPERSION_HIGH: 0.30,
} as const;

export const DCA_THRESHOLDS = {
  ACTIF_MAX: 0.95,
  NEUTRAL_MAX: 1.05,
  SUSPENDU_MAX: 1.5,
  SUSPENDU_PRUDENT_MAX: 2.0,
  REACTIVATION_MARGIN: 0.95,
} as const;

// Assertion de cohérence côté front
if (SYNTHESIS_THRESHOLDS.UNDERVALUED_MAX !== DCA_THRESHOLDS.ACTIF_MAX) {
  throw new Error("Threshold mismatch: SYNTHESIS vs DCA");
}
```

## 8.4 Test de cohérence inter-couches

```python
# tests/unit/test_thresholds_consistency.py

from app.core.thresholds import SYNTHESIS, DCA

def test_synthesis_dca_thresholds_aligned():
    """Cf. spec v2.1 §4.5 + §6.3 — harmonisation v2.1."""
    assert SYNTHESIS.UNDERVALUED_MAX == DCA.ACTIF_MAX
    assert SYNTHESIS.NEUTRAL_MAX == DCA.NEUTRAL_MAX
    assert SYNTHESIS.OVERVALUED_MAX == DCA.SUSPENDU_MAX
    assert SYNTHESIS.HEAVILY_OVERVALUED_MAX == DCA.SUSPENDU_PRUDENT_MAX
```

## 8.5 Règle de normalisation OUI_NEUTRE → OUI

Cf. remarque utilisateur sur la spec v2.1.

`compute_valuation_verdict` (spec §4.3.6) peut retourner `OUI_NEUTRE` pour la zone 0.95-1.05. Mais `compute_synthesis` (spec §4.5.2) traite cette zone via la règle 3 ("ENTRÉE NEUTRE", color GREEN).

### Règles de coexistence

- `valuation_verdict.verdict` peut être `OUI`, `OUI_NEUTRE`, `NON`, ou `INDÉTERMINÉ`
- `investissable_verdict.verdict` peut être `OUI`, `NON`, ou `INCERTAIN` (pas de OUI_NEUTRE)
- Dans `compute_synthesis`, on traite tous les cas via les seuils de ratio numériques, pas via les labels verdict
- Le frontend affiche le label `verdict` du valuation tel quel (`OUI` ou `OUI_NEUTRE` reste visible dans l'onglet 2)
- Le frontend affiche le `decision` du synthesis tel quel dans le bandeau

### IMPORTANT — Piège à éviter

```python
# ⚠️ DO NOT — code défensif accidentel qui exclut un cas légitime
if investissable.verdict not in ["OUI", "OUI_NEUTRE"]:
    return SynthesisDecision(decision="INCERTAIN", ...)
# Ce code est buggé : OUI_NEUTRE n'existe pas sur InvestissableVerdict.
# Il existe UNIQUEMENT sur ValuationVerdict.

# ✅ DO — utiliser le verdict du bon schéma
if investissable.verdict == "NON":
    return SynthesisDecision(decision="REJET", ...)
if investissable.verdict == "INCERTAIN":
    return SynthesisDecision(decision="INCERTAIN", ...)
# À ce stade investissable.verdict == "OUI", on évalue le ratio
```

**Distinction à graver** :

| Schéma                  | Valeurs possibles de `verdict`                    |
|-------------------------|---------------------------------------------------|
| `InvestissableVerdict`  | `OUI`, `NON`, `INCERTAIN`                         |
| `ValuationVerdict`      | `OUI`, `OUI_NEUTRE`, `NON`, `INDÉTERMINÉ`         |

**Pas de mapping forcé OUI_NEUTRE → OUI**. Les deux labels coexistent à des niveaux différents (onglet 2 vs bandeau).

Le compilateur TypeScript et Pydantic devraient catcher tout mauvais usage si les types `Literal` sont bien définis (cf. §4.3). C'est précisément pourquoi le typage strict est obligatoire.

## 8.6 Règle de centralisation

**DO** : tout seuil utilisé dans le code vient de `app.core.thresholds`. Front utilise `lib/thresholds.ts`.

**DON'T** : pas de `if ratio < 0.95` directement dans un service. Toujours `if ratio < SYNTHESIS.UNDERVALUED_MAX`.

---

# 9 · Sécurité

## 9.1 Secrets

**DO** :
- Tous les secrets dans `.env` (jamais commit)
- `.env.example` versionné avec les noms (sans valeurs)
- Validation au startup via Pydantic `BaseSettings`
- Le startup app crash si une env var requise manque

**DON'T** :
- Pas de clé API en dur dans le code, jamais
- Pas de secret en URL (query string), toujours en header

```python
# app/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    # APIs (secrets)
    ALPACA_API_KEY: SecretStr
    ALPACA_SECRET_KEY: SecretStr
    FMP_API_KEY: SecretStr
    FRED_API_KEY: SecretStr
    HALAL_TERMINAL_API_KEY: SecretStr
    FINNHUB_API_KEY: SecretStr
    SEC_EDGAR_USER_AGENT: str  # pas un secret, mais requis

    # Database
    DATABASE_URL: SecretStr
    REDIS_URL: SecretStr

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = []
    RATE_LIMIT_PER_MINUTE: int = 30

    # Métier (paramétrables — cf. spec §10.1)
    SHARIAH_DEBT_TO_MARKETCAP_MAX: float = 0.30
    SHARIAH_IMPURE_REVENUE_MAX: float = 0.03
    SHARIAH_INTEREST_INCOME_MAX: float = 0.03
    SHARIAH_CASH_TO_MARKETCAP_MAX: float = 0.30
    DCA_MONTHLY_BUDGET_EUR: float = 200
    DCA_MIN_TRANCHE_EUR: float = 25
    DCA_PARTIAL_SELL_PCT_AT_2X: float = 0.33
    PORTFOLIO_TARGET_POSITIONS_MIN: int = 6
    PORTFOLIO_TARGET_POSITIONS_MAX: int = 10
    STOP_LOSS_ATR_MULTIPLIER: float = 2.0
    CONCENTRATION_HHI_ALERT_THRESHOLD: float = 0.40
    SECTOR_MAX_WEIGHT_ALERT: float = 0.35
    FOREIGN_EXPOSURE_ALERT: float = 0.70


settings = Settings()
```

## 9.2 Validation des inputs

Toutes les entrées API sont validées par Pydantic. Pas de validation manuelle redondante côté service.

```python
# app/api/v1/endpoints/investissable.py

from fastapi import APIRouter
from app.models.schemas import TickerRequest, InvestissableVerdict
from app.services.investissable_service import compute_investissable

router = APIRouter()

@router.get("/investissable/{symbol}", response_model=InvestissableVerdict)
async def get_investissable(symbol: str) -> InvestissableVerdict:
    request = TickerRequest(symbol=symbol)  # validation Pydantic
    return await compute_investissable(request.symbol)
```

## 9.3 CORS

```python
# app/main.py

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # liste explicite, pas de "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
```

**DON'T** : pas de `allow_origins=["*"]`, jamais.

## 9.4 Rate limiting

```python
# app/core/security.py

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Endpoints critiques
@router.get("/investissable/{symbol}")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def get_investissable(...): ...
```

## 9.5 Frontend — règles secrets

**DO** :
- Pas d'API key tierce (Halal Terminal, Finnhub, etc.) dans `import.meta.env.VITE_*` (les `VITE_*` sont publics côté client)
- L'API key Halal Terminal / Finnhub / SEC EDGAR / etc. **reste côté backend** uniquement
- Le frontend appelle uniquement l'API FinTerminal interne (proxyfiée par le backend)

## 9.6 Auth API minimale (anti-bot)

**Problème** : au déploiement Railway, le backend a une URL publique. Sans protection, n'importe quel scan automatique peut consommer ton quota Halal Terminal et faire exploser ta facturation.

**Solution** : middleware FastAPI qui vérifie un header `X-Auth-Token` sur tous les endpoints (sauf `/health` pour les healthchecks Railway). C'est un mur anti-bot, pas une vraie auth multi-utilisateurs.

### Implémentation backend

```python
# app/core/security.py

from fastapi import Header, HTTPException, status
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

async def verify_api_key(x_auth_token: str | None = Header(None)) -> None:
    """Middleware d'auth minimale anti-bot.

    Vérifie le header X-Auth-Token. Refuse 401 si absent ou invalide.
    Logue les tentatives échouées pour détection d'abus.
    """
    if x_auth_token is None or x_auth_token != settings.FINTERMINAL_API_KEY.get_secret_value():
        # Log l'IP source pour détection d'abus (cf. spec §10.1 logs structurés)
        logger.warning(
            "auth_failed",
            extra={
                "event_type": "auth_failed",
                "header_present": x_auth_token is not None,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Auth-Token header",
        )
```

### Application aux routers

```python
# app/api/v1/router.py

from fastapi import APIRouter, Depends
from app.core.security import verify_api_key
from app.api.v1.endpoints import (
    investissable, valuation, entry, synthesis,
    shariah, portfolio, analytics
)

# Tous les endpoints API protégés
api_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(verify_api_key)],
)
api_router.include_router(investissable.router)
api_router.include_router(valuation.router)
# ... etc

# Healthcheck NON protégé (Railway healthchecks)
health_router = APIRouter()

@health_router.get("/health")
async def health() -> dict:
    return {"status": "ok"}
```

### Configuration de la clé

```python
# app/core/config.py — ajout

class Settings(BaseSettings):
    # ... autres settings ...
    FINTERMINAL_API_KEY: SecretStr  # UUID v4 généré une fois
```

```bash
# .env (à configurer dans Railway)
# Génération : python -c "import uuid; print(uuid.uuid4())"
FINTERMINAL_API_KEY=550e8400-e29b-41d4-a716-446655440000
```

### Côté frontend

```ts
// src/api/client.ts — modification du helper apiFetch

export async function apiFetch<T>(
  path: string,
  opts: { method?: string; body?: unknown; timeout?: number } = {}
): Promise<T> {
  const { method = "GET", body, timeout = DEFAULT_TIMEOUT_MS } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const res = await fetch(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        // Header d'auth — clé exposée côté client (mur anti-bot, pas secret crypto)
        "X-Auth-Token": import.meta.env.VITE_FINTERMINAL_API_KEY ?? "",
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    // ... reste inchangé ...
  } catch (err) {
    // ...
  }
}
```

```bash
# frontend/.env (configuré dans Railway, exposé au bundle)
VITE_FINTERMINAL_API_KEY=550e8400-e29b-41d4-a716-446655440000  # même valeur que backend
```

### Limites assumées de cette protection

**Ce que ça protège** :
- Scans automatiques de bots (99% du trafic non sollicité)
- Devinettes d'URL et fuites accidentelles dans des screenshots
- Crawlers indexant ton API publique

**Ce que ça NE protège PAS** :
- Un attaquant qui ouvre les DevTools et lit la valeur dans le bundle JS
- Un utilisateur déterminé qui veut épuiser volontairement ton quota

**Plan de remédiation si abus détecté** (logs `auth_failed` anormalement nombreux ou trafic suspect) :
1. Générer un nouvel UUID v4
2. Mettre à jour `FINTERMINAL_API_KEY` (backend) et `VITE_FINTERMINAL_API_KEY` (frontend) sur Railway
3. Redéployer les deux
4. Temps de remédiation : ~5 minutes

**Pour une vraie auth** (V2 si nécessaire) :
- Backend-for-frontend qui sert de proxy avec auth utilisateur (cookies httpOnly)
- Ou OAuth/JWT si multi-utilisateurs

À ne PAS implémenter en V1 (over-engineering pour un usage personnel).

---

# 10 · Frontend spécifique

## 10.1 Architecture composants

### Hiérarchie pour la page d'analyse

```
AnalysisPage
├── Layout
│   ├── Sidebar
│   └── (main content)
├── SynthesisBanner             ← TOUJOURS visible en haut
└── TabsContainer
    ├── Tab 1 : InvestissableTab
    ├── Tab 2 : ValuationTab
    └── Tab 3 : EntryTab
```

### Hiérarchie pour la page portfolio

```
PortfolioPage
├── Layout
├── PortfolioSummary
├── PortfolioTable               ← Liste positions avec DCAStatus par ligne
├── Analytics                     ← Section corrélations + HHI + expo devise + beta
└── Simulation                    ← Section simulation d'ajout (étape 5)
```

## 10.2 Pattern de gestion d'état asynchrone

```tsx
// src/hooks/useTickerAnalysis.ts

import useSWR from "swr";
import { fetchTickerAnalysis } from "@/api/ticker";

export function useTickerAnalysis(symbol: string) {
  const { data, error, isLoading, mutate } = useSWR(
    symbol ? ["ticker", symbol] : null,
    () => fetchTickerAnalysis(symbol)
  );

  return {
    data,
    error,
    isLoading,
    refresh: () => mutate(),
  };
}

// Composant qui consomme :

export const InvestissableTab: FC<{ symbol: string }> = ({ symbol }) => {
  const { data, error, isLoading } = useTickerAnalysis(symbol);

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={error.message} />;
  if (!data) return null;

  return <InvestissableContent data={data.investissable} />;
};
```

**DO** :
- Toujours gérer les 3 états (loading / error / data)
- Composants séparés : `LoadingState`, `ErrorState`, content réel
- Pas de "loading dot" custom, utiliser un composant `<Skeleton />` réutilisable

**DON'T** :
- Pas de `if (!data) return <div>Loading...</div>` inline répété partout
- Pas de fetch directement dans le composant

## 10.3 SynthesisBanner — composant clé

```tsx
// src/components/analysis/SynthesisBanner.tsx

import type { FC } from "react";
import type { SynthesisDecision } from "@/types/api";
import { cn } from "@/lib/utils";

interface SynthesisBannerProps {
  decision: SynthesisDecision;
}

const COLOR_CLASSES: Record<string, string> = {
  RED: "bg-red-50 border-red-300 text-red-900",
  ORANGE: "bg-orange-50 border-orange-300 text-orange-900",
  GREEN: "bg-green-50 border-green-300 text-green-900",
  GREY: "bg-gray-50 border-gray-300 text-gray-700",
};

export const SynthesisBanner: FC<SynthesisBannerProps> = ({ decision }) => {
  return (
    <div
      data-testid="synthesis-banner"
      className={cn(
        "border-2 rounded-lg p-4 mb-6",
        COLOR_CLASSES[decision.color]
      )}
    >
      <div className="grid grid-cols-3 gap-4">
        <div>
          <h3 className="font-semibold text-sm">INVESTISSABLE</h3>
          <p className="text-2xl">{getInvestissableIcon(decision)}</p>
        </div>
        <div>
          <h3 className="font-semibold text-sm">VALORISATION</h3>
          <p className="text-2xl">{getValuationIcon(decision)}</p>
        </div>
        <div>
          <h3 className="font-semibold text-sm">DÉCISION</h3>
          <p className="text-xl font-bold">{decision.decision}</p>
          {decision.reactivation_price !== undefined && (
            <p className="text-sm">Réactivation : {decision.reactivation_price}€</p>
          )}
        </div>
      </div>
    </div>
  );
};
```

## 10.4 Conventions Tailwind

**DO** :
- Tailwind utility classes pour le statique
- `cn()` pour merge conditionnel (utility de `clsx` + `tailwind-merge`)
- Couleurs sémantiques (`text-red-900`, `bg-green-50`), pas de couleur arbitraire (`text-[#ff0000]`)

**DON'T** :
- Pas de styled-components / emotion / CSS Modules
- Pas de CSS inline sauf valeurs dynamiques calculées en JS

## 10.5 Toast notifications

```tsx
// src/main.tsx
import { Toaster } from "sonner";

// Dans le JSX root :
<Toaster position="bottom-right" theme="dark" richColors />

// Dans les hooks/composants :
import { toast } from "sonner";

const { mutate } = useSWR(...);

const handleAction = async () => {
  try {
    await mutate();
    toast.success("Action réussie");
  } catch (err) {
    toast.error((err as Error).message);
  }
};
```

---

# 11 · Roadmap d'exécution incrémentale

## 11.1 Ordre obligatoire

L'ordre est **bloquant**. Pas de saut d'étape. Pas d'étape commencée avant que la précédente soit livrée et validée.

```
ÉTAPE 0 — Fondations techniques (PRÉREQUIS BLOQUANT)
ÉTAPE 1 — Halal Terminal en gate AAOIFI
ÉTAPE 2 — SEC EDGAR
ÉTAPE 3 — Portfolio Analytics partie 1 (parallèle de 2 si volonté)
ÉTAPE 4 — Refonte SCORE en gates + qualité
ÉTAPE 5 — Suppression XGBoost + Portfolio Analytics partie 2
ÉTAPE 6 — Couche de synthèse + bandeau verdict
ÉTAPE 7 — Raffinements
```

## 11.2 Étape 0 — Fondations techniques

**Objectif** : préparer le terrain technique avant toute feature métier, et **nettoyer le code mort qui sera supprimé plus tard de toute façon** pour ne pas le migrer en TS pour rien.

### 0.1 — Migration TS big-bang ET suppression onglet PRÉVISIONS frontend

- Configurer `tsconfig.json` strict (cf. §3.2)
- Ajouter `vite.config.ts` (depuis `vite.config.js`)
- Renommer tous les `.jsx` en `.tsx`, `.js` en `.ts`
- **Supprimer le code mort frontend** (sera supprimé étape 5 de toute façon, autant ne pas le migrer) :
  - `src/components/prediction/` (tout le dossier de l'onglet PRÉVISIONS)
  - `src/api/prediction.js` ou équivalent (appels à `/api/v1/predict`)
  - Routes frontend `/prediction` ou `/forecast` du router (si présentes)
  - Liens vers l'onglet PRÉVISIONS dans la sidebar / menu de navigation
- Résoudre toutes les erreurs TS sur le code restant (pas de `// @ts-ignore`)
- `npm run build` doit passer sans warning
- Mettre à jour `package.json` : `tsc --noEmit` dans le script `lint`

**Rationale du nettoyage anticipé** : migrer l'onglet PRÉVISIONS en TS pour le supprimer 4 étapes plus tard serait du temps perdu et créerait une dette technique inutile. Le backend XGBoost reste en place pour l'instant (sera supprimé étape 5), mais l'onglet frontend disparaît immédiatement.

### 0.2 — PostgreSQL + Alembic

- Ajouter `psycopg[binary]` et `sqlalchemy[asyncio]` à `requirements.txt`
- Configurer `app/core/database.py` avec connection pool async
- Initialiser Alembic dans `backend/alembic/`
- Créer le schéma initial :
  - `positions` (id, symbol, currency, opened_at)
  - `transactions` (id, position_id, date, qty, price, fee)
  - `screen_history` (id, symbol, screen_date, ratios_json, verdict)
  - `conviction_history` (id, symbol, computed_at, score, tier)
  - `fair_value_history` (id, symbol, computed_at, fair_value, methods_json)
- Première migration `001_initial_schema.py`

### 0.3 — Tests setup

- `pytest.ini` avec markers (`unit`, `integration`, `e2e`)
- `vitest.config.ts` avec setup file
- `conftest.py` avec fixtures DB (rollback transactions)
- CI minimale Railway : run tests à chaque push

### 0.4 — Auth API minimale

- Génération UUID v4 pour `FINTERMINAL_API_KEY`
- Configuration côté backend (`app/core/security.py` + middleware)
- Configuration côté frontend (`apiFetch` avec header `X-Auth-Token`)
- Variables d'env Railway (back + front) avec la même valeur
- Cf. §9.6 pour l'implémentation détaillée

### 0.5 — Génération types Pydantic → TS

- Script `frontend/scripts/generate-types.ts`
- Hook `npm run dev` lance la génération avant Vite
- Test manuel : modifier un schéma backend, vérifier que `src/types/api.ts` est mis à jour

### Critères de validation étape 0

- [ ] `npm run build` passe sans erreur
- [ ] Aucun fichier `prediction.*` ou `predict.*` côté frontend
- [ ] Aucune référence à `/api/v1/predict` ou onglet PRÉVISIONS dans le code frontend
- [ ] `pytest` passe (tests vides OK)
- [ ] `vitest` passe (tests vides OK)
- [ ] `alembic upgrade head` crée le schéma DB
- [ ] La génération de types fonctionne (1 type généré visible dans `src/types/api.ts`)
- [ ] App démarre en local avec `uvicorn` + `vite`
- [ ] `GET /health` retourne 200 sans X-Auth-Token (endpoint public)
- [ ] `GET /api/v1/...` sans X-Auth-Token retourne 401
- [ ] `GET /api/v1/...` avec X-Auth-Token correct retourne 200 (ou 4xx métier, pas 401)

## 11.3 Étapes 1-7

Cf. spec v2.1 §9 pour le détail. Critères de validation par étape en §13 de ce master prompt.

---

# 12 · Liste de DO NOT

## DO NOT — Liste exhaustive

Tu ne dois **jamais** :

1. **Implémenter une API qui n'est pas dans la spec.** Pas d'OpenSanctions. Pas de scraping Musaffa. Pas d'Alpha Vantage. La stack data est figée à 8 sources (cf. spec §3).

2. **Calculer un score composite qui mélange gates et qualités.** Si une dimension est non-substituable (AAOIFI, Altman, fraude), c'est un gate. Pas une composante de score. Cf. spec §1.3 principe 2.

3. **Réintroduire XGBoost ou un module de prédiction directionnelle court terme.** Le module ML est tué (spec §2.1). Pas de "j'ai trouvé un meilleur modèle". Pas de "drawdown 3 mois". Pas de classifier de régime. Rien.

4. **Calculer Kelly avec des inputs par défaut.** Kelly est tué (spec §2.2). Allocation = paliers de conviction (spec §7).

5. **Utiliser un seuil hard-codé qui contredit les variables d'env paramétrables.** Tous les seuils paramétrables passent par `app.core.thresholds`.

6. **Inventer une méthode de fair value qui n'est pas dans les 4 méthodes V1.** Pas de DCF. Pas de SOTP. Pas de "DCF simplifié". Les 4 méthodes (multiples vs historique, multiples vs secteur, Graham, target analystes) sont figées.

7. **Mettre de la logique métier dans le frontend.** Le front affiche, le back décide. Si tu te retrouves à écrire un `if ratio > 1.05` dans un composant React, c'est un bug d'architecture. Le back doit retourner le label déjà calculé.

8. **Faire un commit qui casse les tests existants.** Si tes nouveaux tests cassent les anciens, soit tes anciens tests étaient mauvais (les corriger explicitement avec justification), soit ton code est mauvais (le corriger).

9. **Réintroduire le module RISQUE comme onglet à part entière.** Le risque est intégré dans les 3 onglets (Altman dans INVESTISSABLE, ATR dans COMMENT RENTRER, etc.). Pas d'onglet séparé.

10. **Réintroduire le check Israël dans le terminal.** Cette dimension reste hors du code (cf. spec §1.3 principe 5).

11. **Faire du polling agressif sur Halal Terminal API.** Cache TTL 7 jours. Re-screen uniquement aux triggers définis (spec §5.3.1).

12. **Mettre du localStorage / sessionStorage pour de la donnée critique.** Le portfolio est dans PostgreSQL. Le cache éphémère est dans Redis. localStorage uniquement pour des préférences UI (thème, sidebar collapsed, etc.).

13. **Utiliser `any` en TypeScript.** Toujours `unknown` puis narrow.

14. **Ajouter une nouvelle dépendance sans justification.** Chaque dépendance ajoutée doit être listée dans la note de release avec sa raison d'être.

15. **Ignorer le warning Halal Terminal coverage.** Si l'API retourne `NOT_COVERED`, le frontend doit afficher explicitement "Ticker non couvert" et bloquer toute analyse de gate AAOIFI.

16. **Faire confiance à un verdict pré-calculé d'une méthodologie Halal Terminal.** On utilise les **ratios bruts** et on applique nos seuils personnels. Cf. spec §5.

17. **Vendre une position automatiquement.** Le terminal **propose** des actions, l'utilisateur **valide** chaque action. Pas d'exécution automatique, jamais.

18. **Persister une donnée sensible (mot de passe, token) en clair.** Hashage / chiffrement obligatoires. Pour FinTerminal v2.1 personnel, pas de système d'auth multi-utilisateurs en V1, donc question peu prégnante. Mais pas de raccourci.

19. **Logger une donnée sensible.** Pas de clé API, pas de mot de passe, pas de token de session dans les logs.

20. **Modifier la spec en codant.** Si tu identifies un bug ou une ambiguïté dans la spec, tu **arrêtes** et tu remontes. La spec se modifie via discussion explicite, pas via "j'ai eu une idée en codant".

---

# 13 · Critères de validation par étape

Chaque étape se termine par une validation explicite avant de passer à la suivante. Ces critères sont **binaires** : passe ou ne passe pas.

## Étape 0 — Fondations techniques

- [ ] `cd backend && pip install -r requirements.txt` réussit
- [ ] `cd frontend && npm install` réussit
- [ ] `npm run build` réussit sans erreur ni warning
- [ ] Aucun fichier `prediction.*` ou `predict.*` côté frontend (nettoyage anticipé)
- [ ] Aucune référence à `/api/v1/predict` dans le code frontend
- [ ] Aucun lien vers onglet PRÉVISIONS dans la sidebar / menu de navigation
- [ ] `pytest` réussit (tests vides ou minimaux OK)
- [ ] `vitest run` réussit
- [ ] `alembic upgrade head` crée 5 tables (positions, transactions, screen_history, conviction_history, fair_value_history)
- [ ] `uvicorn app.main:app` démarre, `GET /health` retourne 200 sans X-Auth-Token
- [ ] `GET /api/v1/...` (n'importe quel endpoint API) sans X-Auth-Token retourne 401
- [ ] `GET /api/v1/...` avec X-Auth-Token correct passe (200 ou 4xx métier, pas 401)
- [ ] `npm run dev` démarre Vite, l'app charge sans erreur console
- [ ] La génération de types fonctionne : `npm run types:generate` met à jour `src/types/api.ts`
- [ ] `FINTERMINAL_API_KEY` (UUID v4) configuré sur Railway backend ET `VITE_FINTERMINAL_API_KEY` configuré sur Railway frontend (mêmes valeurs)
- [ ] CI Railway passe (ou setup en attente)

## Étape 1 — Halal Terminal en gate AAOIFI

- [ ] `app/integration/halal_terminal_client.py` existe avec méthode `screen(symbol)`
- [ ] `app/services/shariah_service.py` existe avec `screen_with_personal_thresholds(symbol)`
- [ ] `GET /api/v1/shariah/AAPL` retourne JSON avec `verdict`, `checks`, `raw_ratios`, `methodology_verdicts`
- [ ] `GET /api/v1/shariah/AAPL` retourne PASS avec ratios bruts visibles
- [ ] `GET /api/v1/shariah/RIO` retourne PASS avec ratios bruts visibles
- [ ] `GET /api/v1/shariah/{ticker_non_couvert}` retourne `NOT_COVERED`
- [ ] Cas API down : retourne `ERROR` avec fallback explicite "Re-vérifier manuellement"
- [ ] Les seuils personnels (debt/MC ≤ 30%, revenus haram ≤ 3%) sont appliqués depuis `app/core/thresholds.py`
- [ ] Tests : 4 catégories (cas nominal, NOT_COVERED, ERROR, seuils frontière)
- [ ] Logging : chaque appel logge `event="external_api_call"`, `source="halal_terminal"`, durée ms

## Étape 2 — SEC EDGAR

- [ ] `app/integration/sec_edgar_client.py` existe avec `lookup_cik`, `get_company_facts`, `get_concept`, `get_recent_filings`
- [ ] User-Agent header présent dans toutes les requêtes
- [ ] Cache Redis : ticker→CIK 7j, company facts 24h
- [ ] Validation croisée : `Revenue` AAPL via SEC vs YFinance, écart < 5%
- [ ] Idem pour Net Income, Total Debt, Total Cash, EPS, FCF
- [ ] `InterestIncomeOperating` extrait correctement (distinct de InterestExpense)
- [ ] Cas non US : ticker AIXA.DE retourne `None` proprement, pas de crash
- [ ] Tests : 5 tickers US (AAPL, MSFT, GOOGL, AMZN, META), validation croisée OK
- [ ] Logging : événements `cross_validation` avec deviation_pct

## Étape 3 — Portfolio Analytics partie 1

- [ ] `app/services/portfolio_analytics_service.py` existe avec 4 fonctions :
  - [ ] `compute_correlation_matrix(positions, period_days=252)`
  - [ ] `compute_sector_concentration(positions)`
  - [ ] `compute_currency_exposure(positions)`
  - [ ] `compute_portfolio_beta(positions)`
- [ ] `GET /api/v1/portfolio/analytics` retourne les 4 métriques
- [ ] Sur le portfolio actuel (RIO, EOG, AIXTRON), affiche :
  - [ ] Corrélation RIO/EOG (commodities) > 0.5
  - [ ] HHI sectoriel calculé
  - [ ] Exposition USD + EUR + GBP additionnant 100%
  - [ ] Beta agrégé numérique
- [ ] UI : `Analytics.tsx` rendu, grille 3×3 corrélation visible
- [ ] Tests : fixtures sur 3 positions, valeurs calculées exactes

## Étape 4 — Refonte SCORE en gates + qualité

- [ ] `app/services/investissable_service.py` existe avec architecture gates → qualité
- [ ] 3 gates implémentés : AAOIFI (call shariah_service), Altman, Fraud
- [ ] Détecteur fraude normalisé sectoriellement (pas de seuil absolu sans fallback)
- [ ] Score qualité : 5 composantes (Piotroski, Growth, SmartMoney, CapitalAllocation, EarningsStability)
- [ ] D2 dividende **n'existe pas** dans CapitalAllocation (cf. spec v2.1)
- [ ] D3 utilise (CapEx + R&D) / Revenue (cf. spec v2.1)
- [ ] `app/services/valuation_service.py` existe avec 4 méthodes fair value
- [ ] **Pas de DCF** dans les 4 méthodes
- [ ] Agrégation médiane + dispersion calculés correctement
- [ ] Tests sur 10 tickers (AAPL, MSFT, GOOGL, RIO, EOG, AIXTRON, ASML, RIVN, et 2 small caps)
  - [ ] Niveau de complétude FULL pour large caps US
  - [ ] Niveau PARTIAL pour AIXTRON (international, données partielles attendues)
  - [ ] Niveau INSUFFICIENT pour les small caps testées si données manquantes

## Étape 5 — Suppression XGBoost backend + Portfolio Analytics partie 2

**Note** : le frontend a déjà été nettoyé en étape 0 (suppression onglet PRÉVISIONS). Cette étape concerne uniquement le backend.

- [ ] `app/ml/predictor.py`, `trainer.py`, `features.py`, `targets.py` supprimés
- [ ] `app/api/v1/endpoints/prediction.py` supprimé
- [ ] Endpoint `/api/v1/predict` retire du router (vérifier `app/api/v1/router.py`)
- [ ] `xgboost` retiré de `requirements.txt`
- [ ] `scikit-learn` retiré si plus utilisé ailleurs (sinon conservé)
- [ ] App démarre sans XGBoost (test : `pip uninstall xgboost`, `uvicorn app.main:app` OK)
- [ ] Tests existants ne sont pas cassés
- [ ] Frontend ne contient déjà plus aucune référence à PRÉVISIONS (validé étape 0)
- [ ] `app/services/portfolio_simulation_service.py` existe avec `simulate_position_add`
- [ ] `GET /api/v1/portfolio/simulate?candidate=MSFT&amount=40` retourne :
  - [ ] HHI avant/après
  - [ ] Beta avant/après
  - [ ] Exposition devise avant/après
  - [ ] Corrélations candidat avec existing
  - [ ] Verdict PROCEED/CAUTION
- [ ] Test : ajouter MSFT au portfolio actuel, vérifier impacts cohérents

## Étape 6 — Couche de synthèse + bandeau verdict

- [ ] `app/services/synthesis_service.py` existe avec `compute_synthesis`
- [ ] Les 6 règles sont implémentées exactement comme spec §4.5.2
- [ ] Aucun seuil hard-codé : tout passe par `app.core.thresholds`
- [ ] `GET /api/v1/synthesis/{symbol}` retourne `SynthesisDecision` typé
- [ ] Tests : 6 règles × frontières exactes (0.94999, 0.95, 1.04999, 1.05, 1.49999, 1.5, 1.99999, 2.0)
- [ ] Tests cas dispersion forte (règle 2')
- [ ] Composant `SynthesisBanner.tsx` toujours visible au-dessus des 3 onglets
- [ ] Couleurs : RED pour REJET, ORANGE pour WATCHLIST, GREEN pour ENTRÉE
- [ ] Reactivation_price affiché si applicable

## Étape 7 — Raffinements

- [ ] Scénarios A/B/C/D/E avec pente MA50 (5 scénarios + cas piège)
- [ ] Score conviction unifié documenté + traçabilité spec §7
- [ ] Volume profile pour validation Fibonacci (avec fallback "non validé")
- [ ] Régime macro affiché en info contextuelle dashboard, **pas dans la pondération** des verdicts
- [ ] Logging structuré des divergences inter-sources opérationnel
- [ ] Tests boundary additionnels selon zones grises résiduelles tranchées

---

# 14 · Format de sortie attendu

## 14.1 Pour chaque étape livrée

Tu produis 4 livrables :

### Livrable A — Code complet

Structure exacte conforme à §2. Chaque fichier nouveau ou modifié référence la spec dans son docstring.

### Livrable B — Tests unitaires

Au moins 1 fichier de test par fichier de service. Tests sur 4 catégories : nominal, data manquante, API down, frontières.

### Livrable C — Note de release

Format markdown :

```markdown
# Release Note — Étape X

## Résumé
Une phrase qui dit ce qui est livré.

## Spec mappings
- Section 4.2.1 → `app/services/investissable_service.py:compute_aaoifi_gate`
- Section 4.2.2 → `app/services/investissable_service.py:compute_quality_score`
- Section 4.2.3 → `app/services/investissable_service.py:compute_investissable_verdict`

## Fichiers créés
- `app/services/investissable_service.py`
- `tests/unit/services/test_investissable_service.py`
- `src/components/analysis/InvestissableTab.tsx`
- ...

## Fichiers modifiés
- `app/api/v1/router.py` (ajout endpoint /investissable/{symbol})
- `app/models/schemas.py` (ajout InvestissableVerdict)
- ...

## Fichiers supprimés
(aucun pour cette étape)

## Dépendances ajoutées
- `slowapi==0.1.9` — rate limiting

## Variables d'environnement nouvelles
- `SHARIAH_DEBT_TO_MARKETCAP_MAX=0.30` (cf. spec §10.1)

## Migrations DB
- `alembic/versions/002_add_screen_history.py`

## Tests
- 14 tests passent
- Coverage backend services : 87%
- Coverage frontend components : 65%

## Validation manuelle
Voir section démo ci-dessous.

## Suivi des DO NOT
- ✅ Pas de seuil hard-codé (tout via thresholds.py)
- ✅ Pas de logique métier dans le front
- ✅ Pas d'API hors spec
- ✅ Tests existants ne sont pas cassés
```

### Livrable D — Démo

Format selon le type de livraison :

**Si endpoint backend** : 3-5 commandes curl avec leur sortie attendue.

```bash
# Démo étape 1

$ curl -s http://localhost:8000/api/v1/shariah/AAPL | jq
{
  "verdict": "PASS",
  "failed_checks": [],
  "checks": {
    "debt_to_marketcap": {
      "name": "debt_to_marketcap",
      "passed": true,
      "value": 0.020,
      "threshold": 0.30
    },
    ...
  },
  "raw_ratios": {...},
  "methodology_verdicts": {...},
  "as_of_date": "2026-04-30"
}

$ curl -s http://localhost:8000/api/v1/shariah/{TICKER_NON_COUVERT} | jq
{
  "verdict": "NOT_COVERED",
  ...
}
```

**Si UI** : capture d'écran de la page concernée + courte description.

## 14.2 Pour chaque session

À chaque session de travail :

1. **Résumé en début de session** : "Je vais traiter l'étape X.Y selon la spec §Z. Liste des fichiers à créer/modifier. Estimation 4h."

2. **Résumé en fin de session** : "Étape X.Y livrée. Critères de validation : N/N OK ou X/N OK avec liste des points restants."

## 14.3 En cas de blocage

Si tu rencontres un blocage (ambiguïté spec, dépendance externe down, contradiction entre deux sections de la spec) :

1. **Tu arrêtes le code**.
2. Tu produis un message structuré :

```markdown
## BLOCAGE rencontré

**Étape** : X.Y
**Type** : ambiguïté spec / dépendance down / contradiction / autre

**Description** :
La spec §X dit "...". Mais dans §Y il est dit "...". Quelle est la bonne interprétation ?

**Options possibles** :
1. Option A : ...
2. Option B : ...
3. Option C : ...

**Mon analyse** :
[ton analyse argumentée]

**Recommandation** :
Option B parce que [justification].

**Quelle est ta décision ?**
```

3. **Tu attends la décision avant de reprendre**.

---

# Annexe — Checklist avant tout commit

Avant chaque commit, vérifie :

- [ ] Le code référence la spec dans les docstrings
- [ ] Tous les seuils viennent de `app.core.thresholds` ou `lib/thresholds.ts`
- [ ] Type hints présents partout (Python) / aucun `any` (TS)
- [ ] Tests unitaires écrits ou mis à jour
- [ ] `pytest` passe en local
- [ ] `npm run build` passe en local
- [ ] `npm run lint` passe en local (si configuré)
- [ ] Pas de `console.log`, `print`, `debugger` oubliés
- [ ] Pas de secret en dur
- [ ] Le commit message respecte le format `<type>(<scope>): <description>` (ex: `feat(shariah): add personal thresholds layer`)
- [ ] Les fichiers modifiés sont listés dans la note de release

---

**Fin du master prompt.**

Ce document complète la spec métier `finterminal-spec.md` v2.1.
La spec dit **quoi** faire. Ce master prompt dit **comment** le faire.

Démarre par : "Je commence l'étape 0 — fondations techniques. Voici mon plan détaillé."
