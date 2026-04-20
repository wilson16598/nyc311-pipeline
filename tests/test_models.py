"""
Tests for Pydantic models — schema validation logic.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError

from src.models.request import RawRequest, CleanRequest


# ---------------------------------------------------------------------------
# RawRequest tests
# ---------------------------------------------------------------------------

class TestRawRequest:
    def test_valid_minimal_record(self):
        r = RawRequest(unique_key="12345678")
        assert r.unique_key == "12345678"
        assert r.agency is None

    def test_whitespace_stripped_from_key(self):
        r = RawRequest(unique_key="  99999  ")
        assert r.unique_key == "99999"

    def test_empty_unique_key_raises(self):
        with pytest.raises(ValidationError):
            RawRequest(unique_key="")

    def test_whitespace_only_key_raises(self):
        with pytest.raises(ValidationError):
            RawRequest(unique_key="   ")

    def test_all_nullable_fields_accept_none(self):
        r = RawRequest(
            unique_key="abc",
            created_date=None,
            closed_date=None,
            agency=None,
            borough=None,
            latitude=None,
        )
        assert r.borough is None


# ---------------------------------------------------------------------------
# CleanRequest tests
# ---------------------------------------------------------------------------

class TestCleanRequest:
    def _base(self, **overrides):  # type: ignore[no-untyped-def]
        defaults = dict(
            unique_key="10000001",
            created_date=datetime(2023, 6, 15, 10, 0, 0),
            closed_date=datetime(2023, 6, 15, 14, 0, 0),
            agency="DEP",
            agency_name="Dept of Environmental Protection",
            complaint_type="Noise - Residential",
            descriptor="Loud Music/Party",
            borough="BROOKLYN",
            status="Closed",
        )
        defaults.update(overrides)
        return CleanRequest(**defaults)

    def test_valid_record_creates_successfully(self):
        r = self._base()
        assert r.unique_key == "10000001"

    def test_resolution_hours_derived_correctly(self):
        r = self._base(
            created_date=datetime(2023, 1, 1, 0, 0, 0),
            closed_date=datetime(2023, 1, 1, 6, 0, 0),
        )
        assert r.resolution_hours == 6.0

    def test_is_resolved_true_when_closed_date_present(self):
        r = self._base()
        assert r.is_resolved is True

    def test_is_resolved_false_when_no_closed_date(self):
        r = self._base(closed_date=None)
        assert r.is_resolved is False

    def test_noise_complaint_categorized_correctly(self):
        r = self._base(complaint_type="Noise - Residential")
        assert r.complaint_category == "NOISE"

    def test_pothole_categorized_as_infrastructure(self):
        r = self._base(complaint_type="Street Pothole")
        assert r.complaint_category == "INFRASTRUCTURE"

    def test_invalid_borough_normalized_to_unspecified(self):
        r = self._base(borough="ATLANTIS")
        assert r.borough == "UNSPECIFIED"

    def test_valid_boroughs_accepted(self):
        for borough in ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]:
            r = self._base(borough=borough)
            assert r.borough == borough

    def test_year_month_derived_from_created_date(self):
        r = self._base(created_date=datetime(2022, 11, 5))
        assert r.year_month == "2022-11"

    def test_valid_zip_accepted(self):
        r = self._base(incident_zip="10001")
        assert r.incident_zip == "10001"

    def test_invalid_zip_rejected(self):
        r = self._base(incident_zip="ABCDE")
        assert r.incident_zip is None

    def test_out_of_bounds_latitude_rejected(self):
        r = self._base(latitude=51.5)  # London
        assert r.latitude is None

    def test_valid_nyc_latitude_accepted(self):
        r = self._base(latitude=40.7128)
        assert r.latitude == 40.7128


# ---------------------------------------------------------------------------
# Cleaning logic tests
# ---------------------------------------------------------------------------

class TestCleaningLogic:
    def test_resolution_hours_none_when_no_closed_date(self):
        r = CleanRequest(
            unique_key="x",
            created_date=datetime(2023, 1, 1),
            closed_date=None,
            agency="A",
            agency_name="Agency A",
            complaint_type="Noise",
            descriptor="Loud",
            status="Open",
        )
        assert r.resolution_hours is None
        assert r.is_resolved is False

    def test_missing_key_raises_validation_error(self):
        with pytest.raises((ValidationError, TypeError)):
            CleanRequest(
                created_date=datetime(2023, 1, 1),
                agency="A",
                agency_name="B",
                complaint_type="C",
                descriptor="D",
                status="Open",
            )
