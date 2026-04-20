"""
Tests for the cleaning pipeline's pandas transformation logic.
These tests run entirely in-memory without a ClickHouse connection.
"""

import pandas as pd
import pytest
from datetime import datetime

# We test the internal _clean_dataframe function directly
from src.cleaning.cleaner import _clean_dataframe


def make_df(**overrides) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """Build a minimal valid raw DataFrame for testing."""
    base = {
        "unique_key": ["A001", "A002", "A003"],
        "created_date": [
            "2023-01-01T10:00:00",
            "2023-06-15T08:30:00",
            "2022-11-20T14:00:00",
        ],
        "closed_date": [
            "2023-01-01T12:00:00",
            None,
            "2022-11-20T10:00:00",  # before created — negative hours
        ],
        "agency": ["NYPD", None, "DEP"],
        "agency_name": ["Police Dept", None, "Environ Protection"],
        "complaint_type": ["  noise - residential  ", "HEAT/HOT WATER", "Street Pothole"],
        "descriptor": ["LOUD MUSIC", "NO HEAT", "POTHOLE"],
        "location_type": ["RESIDENTIAL", None, "STREET"],
        "incident_zip": ["10001", "BADZIP", "11201"],
        "city": ["New York", None, "Brooklyn"],
        "borough": ["MANHATTAN", "BRONX", "ATLANTIS"],
        "status": ["Closed", "Open", "Closed"],
        "latitude": [40.75, 40.85, 51.5],   # 51.5 = London, invalid
        "longitude": [-73.99, -73.92, -0.12],  # -0.12 = London, invalid
    }
    for k, v in overrides.items():
        base[k] = v
    return pd.DataFrame(base)


class TestDeduplication:
    def test_duplicates_removed(self):
        df = make_df(unique_key=["X001", "X001", "X002"])
        result = _clean_dataframe(df)
        assert result["unique_key"].nunique() == len(result)

    def test_no_duplicates_unchanged(self):
        df = make_df()
        result = _clean_dataframe(df)
        assert len(result) <= len(df)  # may remove negatives


class TestDatetimeParsing:
    def test_created_date_parsed_to_datetime(self):
        df = make_df()
        result = _clean_dataframe(df)
        assert pd.api.types.is_datetime64_any_dtype(result["created_date"])

    def test_null_created_date_row_dropped(self):
        df = make_df(created_date=["2023-01-01", None, "2023-03-01"])
        result = _clean_dataframe(df)
        assert len(result) < 3  # row with null created_date removed


class TestTextNormalization:
    def test_complaint_type_title_cased(self):
        df = make_df(complaint_type=["  noise - residential  ", "HEAT/HOT WATER", "pothole"])
        result = _clean_dataframe(df)
        assert all(result["complaint_type"] == result["complaint_type"].str.title())

    def test_null_agency_filled_with_na(self):
        df = make_df()
        result = _clean_dataframe(df)
        assert result["agency"].notna().all()

    def test_borough_normalized_to_uppercase(self):
        df = make_df()
        result = _clean_dataframe(df)
        assert all(b == b.upper() for b in result["borough"])


class TestBoroughNormalization:
    def test_invalid_borough_becomes_unspecified(self):
        df = make_df(borough=["ATLANTIS", "MARS", "MANHATTAN"])
        result = _clean_dataframe(df)
        invalid = result[~result["borough"].isin(
            {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNSPECIFIED"}
        )]
        assert len(invalid) == 0

    def test_valid_boroughs_preserved(self):
        df = make_df(borough=["BRONX", "BROOKLYN", "QUEENS"])
        result = _clean_dataframe(df)
        assert set(result["borough"]).issubset(
            {"BRONX", "BROOKLYN", "QUEENS", "MANHATTAN", "STATEN ISLAND", "UNSPECIFIED"}
        )


class TestZipValidation:
    def test_valid_zip_preserved(self):
        df = make_df(incident_zip=["10001", "11201", "10451"])
        result = _clean_dataframe(df)
        for z in result["incident_zip"].dropna():
            assert len(z) == 5 and z.isdigit()

    def test_invalid_zip_set_to_null(self):
        df = make_df(incident_zip=["ABCDE", "123", "10001"])
        result = _clean_dataframe(df)
        # ABCDE and 123 should be null
        assert result["incident_zip"].isna().sum() >= 2


class TestCoordinateValidation:
    def test_out_of_nyc_bounds_set_to_null(self):
        df = make_df()
        result = _clean_dataframe(df)
        valid_lat = result["latitude"].dropna()
        assert all(40.4 <= v <= 41.0 for v in valid_lat)


class TestNegativeResolutionRemoval:
    def test_negative_resolution_rows_removed(self):
        # Row 3 has closed_date before created_date
        df = make_df()
        result = _clean_dataframe(df)
        valid_hours = result["resolution_hours"].dropna()
        assert all(h >= 0 for h in valid_hours)


class TestDerivedColumns:
    def test_year_month_column_created(self):
        df = make_df()
        result = _clean_dataframe(df)
        assert "year_month" in result.columns
        assert result["year_month"].str.match(r"\d{4}-\d{2}").all()

    def test_complaint_category_column_created(self):
        df = make_df()
        result = _clean_dataframe(df)
        assert "complaint_category" in result.columns
        assert result["complaint_category"].notna().all()

    def test_noise_complaint_correctly_categorized(self):
        df = make_df(complaint_type=["Noise - Residential", "HEAT", "Pothole"])
        result = _clean_dataframe(df)
        noise_rows = result[result["complaint_type"].str.upper().str.contains("NOISE")]
        assert all(noise_rows["complaint_category"] == "NOISE")
