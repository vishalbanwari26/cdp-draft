"""Quote matching on the real report. Skipped when the PDF is not downloaded."""

from pathlib import Path

import pytest

PDF = Path(__file__).resolve().parent.parent / "data" / "HM_ASR25_en.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="report PDF not downloaded")


@pytest.fixture(scope="module")
def report():
    from cdp_draft.report import Report

    return Report(PDF)


def test_quote_with_hyphenation_and_nbsp_is_found(report):
    # "Scope 2" is joined by a non-breaking space in the PDF.
    q = "For the aggregates business line, absolute Scope 2 emissions remained at the previous year’s level at 0.50 million tonnes of CO2eq"
    assert report.page(117).find(q)


def test_table_row_is_found(report):
    assert report.page(120).find("Total greenhouse gas emissions (market-based) (Mio tCO2eq) – – 90.3")


def test_table_row_quoted_with_one_column_is_found(report):
    # The row reads "... 1) – 61.1 59.4 million t CO2eq"; the quote skips 2024.
    assert report.page(117).find("Absolute gross Scope 1 GHG emissions 1) 59.4 million t CO2eq")
    # Skipping numbers never lets a wrong number or word through.
    assert report.page(117).find("Absolute gross Scope 1 GHG emissions 1) 58.0 million t CO2eq") is None
    assert report.page(117).find("Absolute gross Scope 3 GHG emissions 1) 59.4 million t CO2eq") is None


def test_invented_quote_is_not_found(report):
    assert report.page(117).find("Scope 1 emissions fell by half in 2025") is None
    assert report.page(118).find("Gross Scope 1 greenhouse gas emissions (Mio tCO2eq) – – 61.3") is None


def test_search_finds_the_inventory(report):
    assert 120 in [p.number for p in report.search("gross scope 1 greenhouse gas emissions million t CO2eq inventory")]
