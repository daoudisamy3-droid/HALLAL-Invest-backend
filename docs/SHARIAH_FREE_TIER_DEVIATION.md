# Déviation Shariah — Free tier Halal Terminal (étape 1.5)

## Contexte

`finterminal-spec.md` §5.1 et §5.2 prévoient l'application de seuils
Shariah **CUSTOM** (0.30 / 0.30 / 0.03 / 0.03) sur les **ratios bruts**
renvoyés par le provider Halal Terminal. Ces seuils sont plus stricts
que les seuils AAOIFI standards (typiquement 0.33 / 0.33 / 0.05 / 0.05).

## Limitation découverte (mai 2026)

Le **free tier** de Halal Terminal **ne renvoie pas les ratios bruts**.
Il renvoie uniquement un verdict agrégé calculé selon **leurs** seuils
AAOIFI standards.

Exemple AAPL (free tier) :

```json
{
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "is_compliant": true,
  "business_screen_pass": true,
  "business_screen_reason": "Business activity is compliant.",
  "financial_screen_pass": true,
  "shariah_compliance_status": null
}
```

Pas de champ `ratios` ⇒ impossible d'appliquer les seuils §5.1 côté
backend.

Exemple ticker inexistant :

```json
{
  "symbol": "ZZZBIDON",
  "is_compliant": null,
  "error": "ticker_unknown",
  "error_message": "Symbol 'ZZZBIDON' is not in our universe; no Shariah verdict available."
}
```

## Décision (mai 2026)

Pour avancer sans coût mensuel (le plan premium Halal Terminal coûte
~$5-20/mois selon le palier), on consomme **directement** les verdicts
agrégés du provider. On accepte temporairement les seuils AAOIFI
standards de Halal Terminal au lieu des seuils custom stricts.

## Impact métier

Un ticker avec `debt_to_marketcap = 0.32` est `PASS` chez Halal
Terminal (≤ 0.33 AAOIFI) mais serait `FAIL` avec nos seuils custom
(> 0.30). En l'absence d'accès aux ratios bruts on accepte le verdict
agrégé du provider.

L'utilisateur est conscient de cette différence : un PASS du gate
Shariah signifie "AAOIFI-conforme selon Halal Terminal" et non "AAOIFI
custom-strict-personnel-conforme".

## Mapping code → cas observés

| Cas | Réponse upstream | Verdict | `source`                                | Persisté ? |
| --- | --- | --- | --- | --- |
| 1   | `error: "ticker_unknown"`                                                | `NOT_COVERED` | `Halal Terminal API (aggregate verdict)` | **Non**    |
| 2   | `is_compliant + business_screen_pass + financial_screen_pass` tous `true` | `PASS`        | `Halal Terminal API (aggregate verdict)` | Oui (7 j)  |
| 3   | au moins un des trois est `false`                                        | `FAIL`        | `Halal Terminal API (aggregate verdict)` | Oui (7 j)  |
| 4   | les trois sont `null` sans champ `error`                                 | `ERROR`       | `Halal Terminal API (error)`             | **Non**    |
| 5   | HTTP / timeout / network failure                                         | `ERROR`       | `Halal Terminal API (error)`             | **Non**    |

NOT_COVERED **n'est pas mis en cache** (la couverture peut s'étendre dans
le temps ; geler la réponse 7 jours empêcherait de redécouvrir un
ticker dès qu'il est ajouté à l'univers Halal Terminal). ERROR n'est
pas non plus mis en cache (transient).

## Conséquence sur le schéma `ShariahReport`

Le contrat reste **compatible** avec la spec §5.2 et avec le frontend
existant — les champs liés aux ratios sont simplement vides :

| Champ                                   | Free-tier (V1)            | Premium (revert futur)      |
| --- | --- | --- |
| `verdict`                               | `PASS`/`FAIL`/`ERROR`/`NOT_COVERED` (idem) | idem |
| `checks: dict[CheckName, ShariahCheck]` | `{}` (vide)               | 4 entrées avec value/threshold/pass |
| `failed_checks: list[CheckName]`        | `[]`                      | sous-ensemble des 4 noms |
| `halal_terminal_methodology_verdicts`   | `{}`                      | 5 entrées (AAOIFI / DJIM / FTSE / MSCI / S&P) |
| `raw_ratios: ShariahRatios`             | tous `None`               | les 7 ratios renseignés |
| `as_of_date: date \| None`              | `None`                    | date du provider |
| `reason: str \| None`                   | message du provider sur FAIL / NOT_COVERED | typiquement `None` sur PASS |

Le frontend `ShariahPanel` gère déjà gracieusement les structures vides
(sections masquées, `CheckCard` qui rend "N/A"). Aucune modif côté
frontend n'a été nécessaire pour ce refactor.

## Plan de retour aux seuils custom

1. **Option A** — Upgrade Halal Terminal payant (~$5-20/mois) ⇒
   accès aux ratios bruts ⇒ revert à la logique §5.1 / §5.2.
   La logique de cette version reste accessible via `git log` (cf.
   commit qui a introduit le refactor free-tier).

2. **Option B** — Intégration d'une source alternative qui fournit les
   ratios bruts (FMP, Finnhub, Musaffa). Plus complexe : il faut
   coder l'arbitrage entre la source primaire et HT pour le verdict
   méthodologique.

À évaluer en **étape 7** (raffinements de la roadmap §9) ou plus tôt
si un cas métier le justifie (par ex. un ticker borderline qui passe
HT-AAOIFI mais échouerait nos seuils custom).

## Traçabilité dans le code

- `app/services/shariah_service.py::ShariahCustomThresholds` — constantes
  §5.1 **dormantes**, conservées pour rendre le revert trivial.
- `app/services/shariah_service.py::_build_report_from_upstream` —
  logique d'agrégation actuelle (free-tier).
- `app/schemas/shariah.py::Source` — Literal type qui codifie les 3
  valeurs possibles du champ `source`.
- `alembic/versions/003_purge_corrupted_shariah_cache.py` — purge des
  lignes polluées pré-refactor (PASS avec ratios vides).
- `alembic/versions/004_purge_today_error_rows.py` — purge des lignes
  ERROR / shapes pré-refactor du jour du déploiement.

Ce document devra être mis à jour ou supprimé lors de l'application
de l'Option A ou B.
