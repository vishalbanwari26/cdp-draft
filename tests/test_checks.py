"""Offline tests: validation and arithmetic, no model and no PDF needed."""

from cdp_draft.checks import run_checks
from cdp_draft.facts import Fact, _check, _first_number, to_million_t


def fact(scope, value, segment="group", category=None, page=1, quote="q", year=2025):
    return Fact(scope, category, segment, "gross", year, value, "million t CO2eq", value, page, quote)


def by_title(checks):
    return {c.title: c for c in checks}


def test_units():
    assert to_million_t("million t CO2eq") == 1.0
    assert to_million_t("Mio tCO2eq") == 1.0
    assert to_million_t("t CO2eq") == 1e-6
    assert to_million_t("kg CO2eq/t") is None
    assert to_million_t("t CO2eq/million €") is None
    assert to_million_t("%") is None


def test_first_number_tolerates_units_in_value():
    assert _first_number("4.1 million") == 4.1
    assert _first_number("58,408") == 58408.0


def test_sums_pass_within_rounding():
    facts = [
        fact("1", 61.3), fact("2_market", 4.4), fact("3", 24.5), fact("total_market", 90.3),
        fact("2_market", 4.1, "cement"), fact("2_market", 0.25, "aggregates"), fact("2_market", 0.08, "other"),
    ]
    checks = by_title(run_checks(facts, 2025))
    assert checks["Scope 1 + 2 + 3 = total (market-based)"].ok
    assert checks["Scope 2 market-based: business lines add up"].ok


def test_sum_fails_beyond_rounding():
    facts = [fact("1", 61.3), fact("2_market", 4.4), fact("3", 24.5), fact("total_market", 95.0)]
    assert not by_title(run_checks(facts, 2025))["Scope 1 + 2 + 3 = total (market-based)"].ok


def test_scope3_categories_and_combined_figure():
    facts = [fact("3", 24.5)] + [fact("3", v, category=(c,)) for c, v in [(1, 8.6), (3, 3.7), (4, 2.9), (9, 2.4), (15, 6.9)]]
    facts.append(fact("3", 5.3, category=(4, 9)))
    checks = by_title(run_checks(facts, 2025))
    assert checks["Scope 3: categories add up"].ok
    assert checks["Scope 3: combined figure for categories 4 + 9"].ok
    assert not [c for c in checks.values() if c.kind == "conflict"]


def test_scope2_without_method_conflicts_with_both_methods():
    facts = [
        fact("2", 0.50, "aggregates", page=117),
        fact("2_location", 0.25, "aggregates", page=118),
        fact("2_market", 0.25, "aggregates", page=118),
    ]
    conflicts = [c for c in run_checks(facts, 2025) if c.kind == "conflict"]
    assert len(conflicts) == 1 and "aggregates" in conflicts[0].title


def test_scope2_without_method_matching_one_method_is_fine():
    facts = [fact("2", 0.25, "aggregates"), fact("2_market", 0.25, "aggregates"), fact("2_location", 0.3, "aggregates")]
    assert not [c for c in run_checks(facts, 2025) if c.kind == "conflict"]


class FakePage:
    number = 118

    def __init__(self, text):
        self.text = text

    def find(self, quote):
        return [object()] if quote in self.text else None


def test_validator_rejects_mislabelled_total_and_future_years():
    page = FakePage("Total location-based Scope 2 GHG emissions – – 4.8 million t CO2eq")
    raw = {"scope": "total_location", "value": 4.8, "year": 2025, "unit": "million t CO2eq",
           "quote": "Total location-based Scope 2 GHG emissions – – 4.8 million t CO2eq"}
    f, err = _check(raw, page, 2025)
    assert f is None and "names only Scope 2" in err
    f, err = _check({**raw, "scope": "2_location"}, page, 2025)
    assert f is not None and f.mt == 4.8
    f, err = _check({**raw, "scope": "2_location", "year": 2030}, page, 2025)
    assert f is None and "target" in err


def test_validator_rejects_a_guessed_scope2_method_and_unnamed_scope():
    text = "For the aggregates business line, absolute Scope 2 emissions remained at 0.50 million tonnes of CO2eq"
    page = FakePage(text + " which corresponds to 22.2 million tonnes of CO2eq")
    base = {"value": 0.5, "year": 2025, "unit": "million t CO2eq", "quote": text}
    f, err = _check({**base, "scope": "2_location"}, page, 2025)
    assert f is None and "method" in err
    assert _check({**base, "scope": "2"}, page, 2025)[0] is not None
    f, err = _check({"scope": "1", "value": 22.2, "year": 2025, "unit": "million tonnes of CO2eq",
                     "quote": "22.2 million tonnes of CO2eq"}, page, 2025)
    assert f is None and "which scope" in err


def test_validator_rejects_quote_not_on_page_and_value_not_in_quote():
    page = FakePage("Gross Scope 1 emissions 61.3 million t CO2eq")
    base = {"scope": "1", "year": 2025, "unit": "million t CO2eq"}
    assert _check({**base, "value": 61.3, "quote": "Gross Scope 1 emissions 62.0 million t CO2eq"}, page)[0] is None
    assert _check({**base, "value": 60.0, "quote": "Gross Scope 1 emissions 61.3 million t CO2eq"}, page)[0] is None
    assert _check({**base, "value": 61.3, "quote": "Gross Scope 1 emissions 61.3 million t CO2eq"}, page)[0] is not None
