"""Rules a store must pass to be a lead.

Bump RULES_VERSION whenever a rule is added or changed: on its next start (for example right after
an update), the app re-checks the leads still waiting in the list with the new rules.
"""

from __future__ import annotations

import brand_list
from seller_parser import is_media_only, same_name

RULES_VERSION = 5  # 5: brands are also read from the product names on the storefront

MEDIA_ONLY_REASON = "Only sells books, movies, music & video games"
OWN_BRAND_REASON = "Sells its own brand"
NO_BRANDS_REASON = "No well-known brands"


def violation(row: dict, departments: list[str] | None, brands: list[str] | None = None) -> str | None:
    """Why this store is not a lead, or None when it passes every rule.

    `departments` and `brands` are what the store's storefront menus show (None when they couldn't
    be read; a store is never rejected for something the app couldn't see). The brand of the product
    the store was found on comes from the row (`found_brand`).
    """
    if departments and is_media_only(departments):
        return MEDIA_ONLY_REASON
    names = [row.get("seller_name", ""), row.get("business_name", "")]
    for brand in [row.get("found_brand", "")] + list(brands or []):
        if brand and any(same_name(n, brand) for n in names):
            return f"{OWN_BRAND_REASON} ({brand})"
    return None


def skip_reason(row: dict, brands: list[str] | None) -> str | None:
    """A softer rule than `violation`: the store isn't a lead, but its number isn't banned for both apps
    (the brands list may simply be missing one of its brands).

    Brands seen: the product the store was found on (`found_brand`) and its storefront's Brands menu.
    When no brand could be seen at all, the store is not skipped.
    """
    seen = [b for b in [row.get("found_brand", "")] + list(brands or []) if b]
    if not seen:
        return None
    known = brand_list.load()
    if any(brand_list.is_well_known(b, known) for b in seen):
        return None
    return NO_BRANDS_REASON


def approval_notes(row: dict, departments: list[str] | None, brands: list[str] | None) -> str:
    """Why a store that passed every rule is a lead, one short note per check (shown with the lead)."""
    notes = ["New US number (not in the shared list)"]
    seen = [b for b in [row.get("found_brand", "")] + list(brands or []) if b]
    known = brand_list.load()
    well_known = list(dict.fromkeys(b for b in seen if brand_list.is_well_known(b, known)))
    if well_known:
        more = f" +{len(well_known) - 3} more" if len(well_known) > 3 else ""
        notes.append(f"Sells well-known brands: {', '.join(well_known[:3])}{more}")
    else:
        notes.append("Brands couldn't be read (kept)")
    if departments:
        notes.append(f"Not just books/movies/music/games (sells {', '.join(departments[:3])})")
    else:
        notes.append("Departments couldn't be read (kept)")
    notes.append("Not its own brand" if seen else "Own brand: couldn't check (kept)")
    return " · ".join(notes)
