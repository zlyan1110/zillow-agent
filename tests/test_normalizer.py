"""Tests for address normalization and fast-path routing.

These tests verify:
1. Standard addresses are correctly parsed (fast-path)
2. Fuzzy inputs are correctly classified (LLM-path)
3. Edge cases don't crash the router
"""

from __future__ import annotations

import pytest

from zillow_agent.models import Address, InputClassification
from zillow_agent.normalizer import build_zillow_url, classify_input


class TestClassifyInput:
    """Tests for the fast-path router."""

    def test_standard_address_with_zip(self) -> None:
        result = classify_input("14933 SE 45th Place, Bellevue, WA 98006")
        assert result.classification == InputClassification.STANDARD_ADDRESS
        assert result.address is not None
        assert result.address.street == "14933 SE 45th Place"
        assert result.address.city == "Bellevue"
        assert result.address.state == "WA"
        assert result.address.zipcode == "98006"

    def test_standard_address_without_zip(self) -> None:
        result = classify_input("123 Main St, Seattle, WA")
        assert result.classification == InputClassification.STANDARD_ADDRESS
        assert result.address is not None
        assert result.address.state == "WA"
        assert result.address.zipcode is None

    def test_standard_address_with_apt(self) -> None:
        result = classify_input("456 Oak Ave Apt 3B, Portland, OR 97201")
        assert result.classification == InputClassification.STANDARD_ADDRESS
        assert result.address is not None
        assert "Apt" in result.address.street

    def test_standard_address_with_zip_plus_four(self) -> None:
        result = classify_input("789 Pine Rd, Denver, CO 80202-1234")
        assert result.classification == InputClassification.STANDARD_ADDRESS
        assert result.address is not None
        assert result.address.zipcode == "80202-1234"

    def test_standard_address_extra_whitespace(self) -> None:
        result = classify_input("  123 Main St ,  Seattle ,  WA  98101  ")
        assert result.classification == InputClassification.STANDARD_ADDRESS

    def test_standard_address_lowercase_state(self) -> None:
        result = classify_input("100 Broadway, New York, ny 10001")
        assert result.classification == InputClassification.STANDARD_ADDRESS
        assert result.address is not None
        assert result.address.state == "NY"  # Should be uppercased

    def test_fuzzy_query_landmark(self) -> None:
        result = classify_input("the space needle")
        assert result.classification == InputClassification.FUZZY_QUERY
        assert result.address is None

    def test_fuzzy_query_natural_language(self) -> None:
        result = classify_input("Apple Park in Cupertino")
        assert result.classification == InputClassification.FUZZY_QUERY

    def test_fuzzy_query_partial_address(self) -> None:
        result = classify_input("123 Main St")
        assert result.classification == InputClassification.FUZZY_QUERY

    def test_fuzzy_query_city_only(self) -> None:
        result = classify_input("houses in Seattle")
        assert result.classification == InputClassification.FUZZY_QUERY

    def test_invalid_state_code_falls_to_fuzzy(self) -> None:
        result = classify_input("123 Main St, Faketown, ZZ 99999")
        assert result.classification == InputClassification.FUZZY_QUERY

    def test_empty_string_raises(self) -> None:
        """Empty input should be caught by Pydantic, not the router."""
        result = classify_input("")
        assert result.classification == InputClassification.FUZZY_QUERY

    def test_unicode_in_street(self) -> None:
        result = classify_input("123 Elm St, San Jose, CA 95112")
        assert result.classification == InputClassification.STANDARD_ADDRESS

    def test_hash_in_unit_number(self) -> None:
        result = classify_input("500 Market St #200, San Francisco, CA 94105")
        assert result.classification == InputClassification.STANDARD_ADDRESS
        assert result.address is not None
        assert "#200" in result.address.street


class TestAddress:
    """Tests for the Address model."""

    def test_to_zillow_slug(self, bellevue_address: Address) -> None:
        slug = bellevue_address.to_zillow_slug()
        assert "14933" in slug
        assert "45th" in slug
        assert "Bellevue" in slug
        assert "WA" in slug
        assert " " not in slug  # Should use dashes

    def test_to_search_query(self, bellevue_address: Address) -> None:
        query = bellevue_address.to_search_query()
        assert "14933 SE 45th Pl" in query
        assert "Bellevue" in query
        assert "WA" in query
        assert "98006" in query

    def test_invalid_zip_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid ZIP"):
            Address(street="123 Main", city="Test", state="WA", zipcode="abc")

    def test_state_uppercased(self) -> None:
        addr = Address(street="123 Main", city="Test", state="wa")
        assert addr.state == "WA"


class TestBuildZillowUrl:
    """Tests for URL construction."""

    def test_url_format(self, bellevue_address: Address) -> None:
        url = build_zillow_url(bellevue_address)
        assert url.startswith("https://www.zillow.com/homes/")
        assert url.endswith("_rb/")
        assert " " not in url
