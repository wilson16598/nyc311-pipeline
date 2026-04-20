"""
Tests for aggregation computations.
All run in-memory without a ClickHouse connection using mock DataFrames.
"""

import pandas as pd
import pytest
from unittest.mock import patch

from src.aggregation.aggregator import (
    compute_agency_performance,
    compute_borough_complaints,
    compute_monthly_trend,
)


def make_clean_df() -> pd.DataFrame:
    return pd.DataFrame({
        "unique_key": [str(i) for i in range(10)],
        "agency": ["NYPD", "NYPD", "DEP", "DEP", "DSNY", "NYPD", "DEP", "DSNY", "NYPD", "DEP"],
        "agency_name": [
            "Police Dept", "Police Dept", "Environ Protection",
            "Environ Protection", "Sanitation", "Police Dept",
            "Environ Protection", "Sanitation", "Police Dept", "Environ Protection"
        ],
        "borough": [
            "MANHATTAN", "BROOKLYN", "BRONX", "MANHATTAN",
            "QUEENS", "BROOKLYN", "BRONX", "QUEENS", "MANHATTAN", "BROOKLYN"
        ],
        "complaint_type": [
            "Noise - Residential", "Noise - Street/Sidewalk",
            "Heat/Hot Water", "Street Pothole",
            "Dirty Condition", "Noise - Commercial",
            "Heat/Hot Water", "Dirty Condition",
            "Noise - Residential", "Street Pothole"
        ],
        "complaint_category": [
            "NOISE", "NOISE", "HOUSING", "INFRASTRUCTURE",
            "SANITATION", "NOISE", "HOUSING", "SANITATION", "NOISE", "INFRASTRUCTURE"
        ],
        "year_month": [
            "2023-01", "2023-01", "2023-01", "2023-02",
            "2023-02", "2023-02", "2023-03", "2023-03", "2023-03", "2023-03"
        ],
        "is_resolved": [1, 0, 1, 1, 0, 1, 0, 1, 1, 0],
        "resolution_hours": [4.0, None, 12.0, 6.5, None, 2.0, None, 8.0, 3.5, None],
    })


@pytest.fixture
def mock_load_clean(monkeypatch):  # type: ignore[no-untyped-def]
    """Patch _load_clean to return in-memory data instead of hitting ClickHouse."""
    df = make_clean_df()

    def fake_load(columns):  # type: ignore[no-untyped-def]
        available = [c for c in columns if c in df.columns]
        return df[available].copy()

    monkeypatch.setattr("src.aggregation.aggregator._load_clean", fake_load)


class TestAgencyPerformance:
    def test_returns_dataframe(self, mock_load_clean):
        result = compute_agency_performance()
        assert isinstance(result, pd.DataFrame)

    def test_has_expected_columns(self, mock_load_clean):
        result = compute_agency_performance()
        for col in ["agency", "agency_name", "year_month", "total_requests",
                    "resolved_count", "avg_resolution_hrs", "resolution_rate"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_resolution_rate_between_0_and_1(self, mock_load_clean):
        result = compute_agency_performance()
        assert (result["resolution_rate"] >= 0).all()
        assert (result["resolution_rate"] <= 1).all()

    def test_total_requests_is_positive(self, mock_load_clean):
        result = compute_agency_performance()
        assert (result["total_requests"] > 0).all()

    def test_each_agency_present(self, mock_load_clean):
        result = compute_agency_performance()
        agencies = set(result["agency"])
        assert "NYPD" in agencies
        assert "DEP" in agencies

    def test_resolved_count_leq_total(self, mock_load_clean):
        result = compute_agency_performance()
        assert (result["resolved_count"] <= result["total_requests"]).all()


class TestBoroughComplaints:
    def test_returns_dataframe(self, mock_load_clean):
        result = compute_borough_complaints()
        assert isinstance(result, pd.DataFrame)

    def test_has_expected_columns(self, mock_load_clean):
        result = compute_borough_complaints()
        for col in ["borough", "complaint_category", "request_count", "pct_of_borough"]:
            assert col in result.columns

    def test_pct_between_0_and_1(self, mock_load_clean):
        result = compute_borough_complaints()
        assert (result["pct_of_borough"] >= 0).all()
        assert (result["pct_of_borough"] <= 1.001).all()  # float tolerance

    def test_all_boroughs_present(self, mock_load_clean):
        result = compute_borough_complaints()
        boroughs = set(result["borough"])
        assert "MANHATTAN" in boroughs
        assert "BROOKLYN" in boroughs

    def test_request_count_positive(self, mock_load_clean):
        result = compute_borough_complaints()
        assert (result["request_count"] > 0).all()


class TestMonthlyTrend:
    def test_returns_dataframe(self, mock_load_clean):
        result = compute_monthly_trend()
        assert isinstance(result, pd.DataFrame)

    def test_has_expected_columns(self, mock_load_clean):
        result = compute_monthly_trend()
        for col in ["year_month", "borough", "complaint_category",
                    "request_count", "resolved_count"]:
            assert col in result.columns

    def test_sorted_by_year_month(self, mock_load_clean):
        result = compute_monthly_trend()
        months = result["year_month"].tolist()
        assert months == sorted(months)

    def test_request_count_positive(self, mock_load_clean):
        result = compute_monthly_trend()
        assert (result["request_count"] > 0).all()

    def test_resolved_leq_total(self, mock_load_clean):
        result = compute_monthly_trend()
        assert (result["resolved_count"] <= result["request_count"]).all()

    def test_covers_all_months_in_data(self, mock_load_clean):
        result = compute_monthly_trend()
        months = set(result["year_month"])
        assert "2023-01" in months
        assert "2023-03" in months
