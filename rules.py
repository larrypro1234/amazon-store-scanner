"""Rules a store must pass to be a lead.

Bump RULES_VERSION whenever a rule is added or changed: on its next start (for example right after
an update), the app re-checks the leads still waiting in the list with the new rules.
"""

from __future__ import annotations

from seller_parser import is_media_only, same_name

RULES_VERSION = 2

MEDIA_ONLY_REASON = "Only sells books, movies, music & video games"
OWN_BRAND_REASON = "Sells its own brand"


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
