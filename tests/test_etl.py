"""
Tests for ETL transform logic.
"""
import pytest
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "etl"))

from transform import safe_agg, cbg_to_county, county_to_document


class TestCbgToCounty:
    def test_extracts_5_chars(self):
        assert cbg_to_county("170310101001") == "17031"

    def test_returns_none_for_short(self):
        assert cbg_to_county("1703") is None

    def test_state_and_county_correct(self):
        # Alabama (01) + Autauga County (001)
        result = cbg_to_county("010010201001")
        assert result == "01001"
        assert result[:2] == "01"   # state fips
        assert result[2:] == "001"  # county fips


class TestSafeAgg:
    def test_sum_normal(self):
        s = pd.Series([100.0, 200.0, 300.0])
        assert safe_agg(s, "sum") == 600.0

    def test_ignores_missing_sentinel(self):
        s = pd.Series([100.0, -666666666.0, 300.0])
        assert safe_agg(s, "sum") == 400.0

    def test_returns_none_for_all_missing(self):
        s = pd.Series([-666666666.0, -666666666.0])
        assert safe_agg(s) is None

    def test_mean_calculation(self):
        s = pd.Series([60000.0, 80000.0, 70000.0])
        result = safe_agg(s, "mean")
        assert abs(result - 70000.0) < 0.01

    def test_empty_series(self):
        assert safe_agg(pd.Series(dtype=float)) is None


class TestCountyToDocument:
    """Tests that document format is correct for KB ingestion."""

    def _make_row(self, **overrides) -> pd.Series:
        defaults = {
            "county_fips": "17031",
            "state_fips": "17",
            "county_name": "Cook County",
            "state_abbr": "IL",
            "total_population": 5275541.0,
            "median_age": 37.2,
            "median_household_income": 68427.0,
            "poverty_rate": 14.2,
            "homeownership_rate": 47.8,
            "unemployment_rate": 5.9,
            "pct_bachelors_or_higher": 42.1,
            "pct_white_alone": 55.4,
        }
        return pd.Series({**defaults, **overrides})

    def test_document_contains_geography(self):
        doc = county_to_document(self._make_row())
        assert "Cook County" in doc
        assert "IL" in doc
        assert "17031" in doc

    def test_document_contains_population(self):
        doc = county_to_document(self._make_row())
        assert "5,275,541" in doc

    def test_document_contains_income(self):
        doc = county_to_document(self._make_row())
        assert "$68,427" in doc

    def test_document_contains_data_year(self):
        doc = county_to_document(self._make_row())
        assert "2020" in doc

    def test_document_handles_missing_fields(self):
        """Document should not crash when optional fields are None."""
        row = self._make_row(
            median_household_income=None,
            poverty_rate=None,
            homeownership_rate=None,
        )
        doc = county_to_document(row)
        assert "Cook County" in doc  # Still produces a document

    def test_document_not_empty(self):
        doc = county_to_document(self._make_row())
        assert len(doc) > 100
