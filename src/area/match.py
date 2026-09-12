"""GLP-1 product match rule (SPEC-area.md section 3.1).

Loads data/products.json and applies the match rule to a raw CMS Open
Payments CSV row (a dict of column name -> string value, exactly as the
csv.DictReader in pull_open_payments.py produces it). Kept separate from
the pull/streaming machinery so the rule itself -- which decides what
counts as "the AREA v0 dataset" -- is independently testable with plain
dicts, no network or CSV parsing involved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PRODUCTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "products.json"

NAME_FIELD_TEMPLATE = "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{n}"
CATEGORY_FIELD_TEMPLATE = "Product_Category_or_Therapeutic_Area_{n}"
NUM_PRODUCT_SLOTS = 5


@dataclass(frozen=True)
class ProductMatch:
    matched_term: str  # the products.json term that matched, e.g. "Ozempic"
    matched_field: str  # e.g. "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_2"
    slot: int  # 1..5


def load_products(path: Path = PRODUCTS_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _terms_lowercase(products: dict[str, Any]) -> list[str]:
    return [t.lower() for t in products["generic_names"] + products["brand_names"]]


def find_matches(row: dict[str, str], products: dict[str, Any]) -> list[ProductMatch]:
    """Every (slot, field) match for this row, in slot order (1..5), each
    field checked at most once. An empty list means this is not a GLP-1
    payment and the row is dropped by the pull script."""
    terms = _terms_lowercase(products)
    matches: list[ProductMatch] = []
    for n in range(1, NUM_PRODUCT_SLOTS + 1):
        for template in (NAME_FIELD_TEMPLATE, CATEGORY_FIELD_TEMPLATE):
            field = template.format(n=n)
            value = (row.get(field) or "").strip()
            if not value:
                continue
            value_lower = value.lower()
            for term in terms:
                if term in value_lower:
                    matches.append(ProductMatch(matched_term=term, matched_field=field, slot=n))
                    break  # one recorded match per field is enough to flag it
    return matches


def canonical_product(
    row: dict[str, str], matches: list[ProductMatch], products: dict[str, Any]
) -> tuple[str, str | None, str]:
    """(product, product_generic, matched_field) from the lowest-slot,
    lowest-field match, per products.json's match_rule ("the lowest-numbered
    matching slot is recorded as canonical"). product_generic is resolved
    via products.json's brand_to_generic map when the matched term is a
    brand name, or the term itself when it already is a generic name.
    Raises if matches is empty -- callers must check that first."""
    if not matches:
        raise ValueError("canonical_product called with no matches")
    first = min(matches, key=lambda m: (m.slot, m.matched_field))
    name_field = NAME_FIELD_TEMPLATE.format(n=first.slot)
    raw_name = (row.get(name_field) or "").strip()
    product = raw_name if raw_name else first.matched_term

    generic_names_lower = {g.lower() for g in products["generic_names"]}
    brand_to_generic_lower = {
        brand.lower(): generic for brand, generic in products["brand_to_generic"].items()
    }
    if first.matched_term in generic_names_lower:
        generic = first.matched_term
    else:
        generic = brand_to_generic_lower.get(first.matched_term)
    return product, generic, first.matched_field
