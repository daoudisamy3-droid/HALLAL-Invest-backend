"""Sector classification from SEC SIC code (Step 4).

Spec authority: §4.2.1 fraud-gate Signal 1 fallback list (capital-intensive
sectors exempted from the absolute FCF/NI < 0.5 threshold).

V1 design (Q4 plan validated): no YFinance/sector-data integration, so
we don't have GICS sector strings or sector medians. We classify via
**SIC ranges** taken from the SEC submissions endpoint and only flag
the capital-intensive exemptions the spec mentions. Sector-relative
thresholds (Fraud Signal 1, Capital D3) fall back to the spec's
absolute defaults when sector medians are unavailable.

SIC reference (selected ranges relevant to FinTerminal's exemption list):
  1000–1499 : Metal Mining / Coal Mining → Basic Materials
  1300–1399 : Oil & Gas Extraction → Energy
  1400–1499 : Mining & Quarrying of Nonmetallic Minerals → Basic Materials
  4900–4999 : Electric / Gas / Sanitary services → Utilities
  6500–6599 : Real Estate (operators, developers)
  6700–6799 : Holding & Investment Offices (includes REITs at SIC 6798)

Outside these ranges → not exempt; absolute thresholds apply.

Note on the Real-Estate range: SEC classifies REITs under SIC 6798
(Real Estate Investment Trusts) which sits inside the 6700-6799
"Holding & Investment Offices" band, not in the 6500-6599 "Real Estate"
band. We extend the exemption to cover both bands so REITs are
correctly treated as capital-intensive per spec §4.2.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# (lo, hi) inclusive — SIC code ranges considered capital-intensive per spec §4.2.1.
EXEMPT_SIC_RANGES: tuple[tuple[int, int], ...] = (
    (1000, 1499),  # Mining + Basic Materials + Energy (extracted)
    (4900, 4999),  # Utilities
    (6500, 6599),  # Real Estate (operators, developers)
    (6700, 6799),  # Holding & Investment Offices (REITs at 6798)
)


@dataclass(frozen=True)
class SectorInfo:
    """Lightweight sector classification result."""

    sic_code: int | None
    sic_description: str | None
    is_exempt_from_absolute_fcf_ni: bool
    exempt_reason: str | None


def classify_from_submissions(submissions: dict[str, Any] | None) -> SectorInfo:
    """Build a :class:`SectorInfo` from the SEC submissions payload.

    Tolerant: every field defaults gracefully if the payload is partial
    or missing. Never raises.
    """
    if not isinstance(submissions, dict):
        return SectorInfo(
            sic_code=None,
            sic_description=None,
            is_exempt_from_absolute_fcf_ni=False,
            exempt_reason=None,
        )

    sic_raw = submissions.get("sicCode") or submissions.get("sic")
    sic_code: int | None = None
    if isinstance(sic_raw, (int, str)):
        try:
            sic_code = int(sic_raw)
        except (TypeError, ValueError):
            sic_code = None

    sic_desc = submissions.get("sicDescription")
    sic_description = str(sic_desc) if isinstance(sic_desc, str) else None

    is_exempt = False
    exempt_reason: str | None = None
    if sic_code is not None:
        for lo, hi in EXEMPT_SIC_RANGES:
            if lo <= sic_code <= hi:
                is_exempt = True
                exempt_reason = (
                    f"SIC {sic_code} in capital-intensive exempt range "
                    f"[{lo}, {hi}] (per spec §4.2.1)"
                )
                break

    return SectorInfo(
        sic_code=sic_code,
        sic_description=sic_description,
        is_exempt_from_absolute_fcf_ni=is_exempt,
        exempt_reason=exempt_reason,
    )
