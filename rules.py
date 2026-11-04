"""Rules a store must pass to be a lead.

Bump RULES_VERSION whenever a rule is added or changed: on its next start (for example right after
an update), the app re-checks the leads still waiting in the list with the new rules.
"""

from __future__ import annotations

import phonenumbers

import brand_list
from seller_parser import is_media_only, same_name

RULES_VERSION = 6  # 6: valid US phone number and US business address are required

MEDIA_ONLY_REASON = "Only sells books, movies, music & video games"
OWN_BRAND_REASON = "Sells its own brand"
NO_BRANDS_REASON = "No well-known brands"
BAD_NUMBER_REASON = "Phone isn't a valid US number"
US_REGIONS = {"US", "PR", "VI", "GU", "AS", "MP"}  # the US and its territories


def valid_us_numbers(row: dict) -> list[str]:
    """The seller's phone numbers that are real, dialable-format US numbers (10 digits, no +1).

    This catches made-up numbers (123-456-7890), Canadian and Caribbean numbers that look like US
    ones, and impossible area codes. It can't tell whether a number is connected: that needs a paid
    phone-lookup service."""
    out = []
    for raw in (row.get("phones") or "").split(" | "):
        try:
            num = phonenumbers.parse(raw, "US")
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(num) and phonenumbers.region_code_for_number(num) in US_REGIONS:
            digits = str(num.national_number)
            if len(digits) == 10 and digits not in out:
                out.append(digits)
    return out


NO_COUNTRY_REASON = "Business address country couldn't be read"


def address_country(row: dict) -> str:
    """The two-letter country at the end of the seller page's business address ("…, TX, 77503, US")."""
    country = (row.get("country") or "").strip().upper()
    if not country:
        last = (row.get("business_address") or "").split(",")[-1].strip().upper()
        country = last if len(last) == 2 and last.isalpha() else ""
    return country


def not_us_seller(row: dict) -> str | None:
    """A real reason about the seller: its business address is outside the US."""
    country = address_country(row)
    if country and country != "US":
        return f"Not a US seller (business address in {country})"
    return None


def no_country(row: dict) -> str | None:
    """Softer: the address shows no country, so the seller can't be confirmed as a US seller."""
    return None if address_country(row) else NO_COUNTRY_REASON


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
    notes = ["Valid US phone number, not in the shared list"]
    notes.append("US business address")
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
