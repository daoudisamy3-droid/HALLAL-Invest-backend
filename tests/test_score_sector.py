"""Tests for app/services/sector.py — SIC range classifier."""

from __future__ import annotations

import pytest

from app.services.sector import EXEMPT_SIC_RANGES, SectorInfo, classify_from_submissions


@pytest.mark.unit
def test_classify_none_payload_returns_default() -> None:
    info = classify_from_submissions(None)
    assert info.sic_code is None
    assert info.sic_description is None
    assert info.is_exempt_from_absolute_fcf_ni is False


@pytest.mark.unit
def test_classify_unknown_sic_not_exempt() -> None:
    info = classify_from_submissions({"sicCode": "7372", "sicDescription": "Software"})
    assert info.sic_code == 7372
    assert info.sic_description == "Software"
    assert info.is_exempt_from_absolute_fcf_ni is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "sic, expected_range",
    [
        (1311, (1000, 1499)),  # Crude Petroleum & Natural Gas — Energy
        (1040, (1000, 1499)),  # Gold Mining — Basic Materials
        (4911, (4900, 4999)),  # Electric Services — Utilities
        (6500, (6500, 6599)),  # Real Estate operators
        (6798, (6700, 6799)),  # REITs (Real Estate Investment Trusts)
    ],
)
def test_classify_exempt_sic_ranges(sic: int, expected_range: tuple[int, int]) -> None:
    info = classify_from_submissions({"sicCode": str(sic)})
    assert info.is_exempt_from_absolute_fcf_ni is True
    assert info.exempt_reason is not None
    assert f"[{expected_range[0]}, {expected_range[1]}]" in info.exempt_reason


@pytest.mark.unit
def test_classify_handles_int_sic_code() -> None:
    info = classify_from_submissions({"sicCode": 1311})  # int instead of str
    assert info.sic_code == 1311
    assert info.is_exempt_from_absolute_fcf_ni is True


@pytest.mark.unit
def test_classify_falls_back_to_sic_alt_key() -> None:
    info = classify_from_submissions({"sic": "1311", "sicCode": None})
    assert info.sic_code == 1311


@pytest.mark.unit
def test_exempt_ranges_cover_specification_sectors() -> None:
    """The EXEMPT list must include mining + energy + utilities + real-estate + REITs."""
    assert (1000, 1499) in EXEMPT_SIC_RANGES   # mining + materials + energy
    assert (4900, 4999) in EXEMPT_SIC_RANGES   # utilities
    assert (6500, 6599) in EXEMPT_SIC_RANGES   # real estate operators
    assert (6700, 6799) in EXEMPT_SIC_RANGES   # REITs (SIC 6798)
