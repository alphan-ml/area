from area.match import canonical_product, find_matches, load_products

PRODUCTS = load_products()


def _row(**overrides):
    """A minimal Open Payments-shaped row with all 5 product slots empty
    unless overridden. Real rows have ~90 columns; these tests only need
    the ones match.py reads."""
    row = {}
    for n in range(1, 6):
        row[f"Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{n}"] = ""
        row[f"Product_Category_or_Therapeutic_Area_{n}"] = ""
    row.update(overrides)
    return row


def test_brand_name_match_in_name_field():
    row = _row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Ozempic")
    matches = find_matches(row, PRODUCTS)
    assert len(matches) == 1
    assert matches[0].matched_term == "ozempic"
    assert matches[0].matched_field == "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1"
    assert matches[0].slot == 1


def test_generic_name_match_is_case_insensitive():
    row = _row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_3="SEMAGLUTIDE Injection")
    matches = find_matches(row, PRODUCTS)
    assert len(matches) == 1
    assert matches[0].matched_term == "semaglutide"
    assert matches[0].slot == 3


def test_matches_via_category_field_too():
    row = _row(Product_Category_or_Therapeutic_Area_2="GLP-1 (tirzepatide)")
    matches = find_matches(row, PRODUCTS)
    assert len(matches) == 1
    assert matches[0].matched_term == "tirzepatide"
    assert matches[0].matched_field == "Product_Category_or_Therapeutic_Area_2"


def test_non_glp1_row_has_no_matches():
    row = _row(
        Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Humira",
        Product_Category_or_Therapeutic_Area_1="Rheumatology",
    )
    assert find_matches(row, PRODUCTS) == []


def test_empty_row_has_no_matches():
    assert find_matches(_row(), PRODUCTS) == []


def test_all_five_brand_names_match():
    for brand in PRODUCTS["brand_names"]:
        row = _row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1=brand)
        matches = find_matches(row, PRODUCTS)
        assert len(matches) == 1, f"{brand} did not match"
        assert matches[0].matched_term == brand.lower()


def test_matches_in_multiple_slots_canonical_picks_lowest():
    row = _row(
        Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_2="Ozempic",
        Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_4="Wegovy",
    )
    matches = find_matches(row, PRODUCTS)
    assert len(matches) == 2
    product, generic, matched_field = canonical_product(row, matches, PRODUCTS)
    assert product == "Ozempic"
    assert matched_field == "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_2"
    assert generic == "semaglutide"


def test_canonical_product_resolves_brand_to_generic_for_every_brand():
    expected = {
        "Ozempic": "semaglutide",
        "Wegovy": "semaglutide",
        "Rybelsus": "semaglutide",
        "Mounjaro": "tirzepatide",
        "Zepbound": "tirzepatide",
    }
    for brand, generic in expected.items():
        row = _row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1=brand)
        matches = find_matches(row, PRODUCTS)
        _, resolved_generic, _ = canonical_product(row, matches, PRODUCTS)
        assert resolved_generic == generic


def test_canonical_product_direct_generic_match_keeps_generic_name():
    row = _row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Tirzepatide")
    matches = find_matches(row, PRODUCTS)
    product, generic, _ = canonical_product(row, matches, PRODUCTS)
    assert product == "Tirzepatide"
    assert generic == "tirzepatide"


def test_canonical_product_raises_on_no_matches():
    import pytest

    with pytest.raises(ValueError):
        canonical_product(_row(), [], PRODUCTS)


def test_substring_match_does_not_false_positive_on_unrelated_drug():
    # "wegovy" and "zepbound" are distinctive enough that this is mostly a
    # sanity check the match rule doesn't do anything clever/fuzzy beyond
    # plain substring containment.
    row = _row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Humira (adalimumab)")
    assert find_matches(row, PRODUCTS) == []
