"""Rules a store must pass to be a lead.

Bump RULES_VERSION whenever a rule is added or changed: on its next start (for example right after
an update), the app re-checks the leads still waiting in the list with the new rules.
"""

from __future__ import annotations

from seller_parser import is_media_only

RULES_VERSION = 1

MEDIA_ONLY_REASON = "Only sells books, movies, music & video games"


def violation(row: dict, departments: list[str] | None) -> str | None:
    """Why this store is not a lead, or None when it passes every rule.

    `departments` is what the storefront's Department menu shows (None when it couldn't be read;
    a store is never rejected for something the app couldn't see).
    """
    if departments and is_media_only(departments):
        return MEDIA_ONLY_REASON
    return None
