"""The seller database shared with the Walmart Seller Finder (see SHARED_DATABASE.md).

Both apps open ~/SellerFinderData/numbers.db. Rules: one small operation per connection,
never delete or rewrite rows, never change a lead's status, US numbers only (10 digits).

This app shares only the `numbers` table (so neither app finds the same number twice). Its leads
stay in this app for review and are not added to the shared `leads` inbox.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import rules

DB_FILE = Path.home() / "SellerFinderData" / "numbers.db"
SOURCE_FOUND = "Found by Amazon app"
SOURCE_REJECTED = "Rejected by Amazon app"


def db() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB_FILE), timeout=30)  # wait (don't fail) while the other app writes
    con.execute("PRAGMA journal_mode=WAL")  # both apps can read and write at the same time
    con.execute("CREATE TABLE IF NOT EXISTS numbers (phone TEXT PRIMARY KEY, details TEXT NOT NULL DEFAULT '', "
                "source TEXT NOT NULL DEFAULT '', added TEXT NOT NULL DEFAULT '')")
    con.execute("CREATE TABLE IF NOT EXISTS leads (key TEXT PRIMARY KEY, data TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'new', found TEXT NOT NULL DEFAULT '', added TEXT NOT NULL DEFAULT '')")
    return con


def normalize_phone(p) -> str:
    digits = re.sub(r"\D", "", str(p or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""  # "" means: not a usable US number


def pretty_phone(digits: str) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def is_known(phone: str) -> bool:
    con = db()
    try:
        return con.execute("SELECT 1 FROM numbers WHERE phone = ?", (phone,)).fetchone() is not None
    finally:
        con.close()


def lookup(phones: list[str]) -> dict[str, str]:
    """Which of these 10-digit numbers are in the shared list right now, and who saved each (read-only)."""
    found: dict[str, str] = {}
    phones = list(dict.fromkeys(p for p in phones if p))
    con = db()
    try:
        for i in range(0, len(phones), 500):  # stay under SQLite's variable limit
            chunk = phones[i:i + 500]
            marks = ",".join("?" * len(chunk))
            found.update(con.execute(f"SELECT phone, source FROM numbers WHERE phone IN ({marks})", chunk))
    finally:
        con.close()
    return found


def shared_state(row: dict, sources: dict[str, str]) -> dict:
    """Live status of one scanned store against the shared list, for display."""
    us = [n for n in map(normalize_phone, (row.get("phones") or "").split(" | ")) if n]
    if not row.get("phones"):
        return {"state": "no_phone"}
    if not us:
        return {"state": "no_us_phone"}
    hits = [sources[n] for n in us if n in sources]
    if not hits:
        return {"state": "missing"}
    if all(src == SOURCE_FOUND for src in hits):
        return {"state": "new", "source": SOURCE_FOUND}
    if all(src == SOURCE_REJECTED for src in hits):
        return {"state": "rejected", "source": SOURCE_REJECTED}
    return {"state": "had", "source": next(src for src in hits if src != SOURCE_FOUND) or "shared list"}


def add_number(phone: str, details: str, source: str = SOURCE_FOUND) -> None:
    con = db()
    try:
        with con:  # never overwrite details someone else saved
            con.execute(
                "INSERT INTO numbers (phone, details, source, added) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(phone) DO UPDATE SET details = CASE WHEN numbers.details = '' "
                "THEN excluded.details ELSE numbers.details END",
                (phone, details, source, datetime.now().strftime("%Y-%m-%d")))
    finally:
        con.close()


def details_for(row: dict) -> str:
    parts = [row.get("seller_name") or row.get("business_name"), row.get("business_address"),
             row.get("profile_url")]
    return " · ".join(p for p in parts if p)


def record_seller(row: dict, get_store=None) -> str:
    """Decide what a freshly scraped seller is, saving its number to the shared list when it's new.

    Returns "new_lead", "rejected" (breaks a rule in rules.py: banned in both apps), "skipped" (no
    well-known brands: not a lead, but nothing is written), "known" (already have this number),
    "no_us_phone", or "no_phone". The check against `numbers` happens right now, not from a cached
    copy. `get_store` (called only for would-be new leads) returns the storefront's
    (departments, brands), each None when they couldn't be read.
    """
    raw = [p for p in (row.get("phones") or "").split(" | ") if p]
    if not raw:
        return "no_phone"
    us = list(dict.fromkeys(n for n in map(normalize_phone, raw) if n))
    if not us:
        return "no_us_phone"
    if any(is_known(n) for n in us):
        return "known"
    departments, brands = get_store() if get_store else (None, None)
    if departments:
        row["store_mix"] = " · ".join(departments)
    details = details_for(row)
    reason = rules.violation(row, departments, brands)
    if reason:
        # A real reason about the seller: saved as rejected so both apps skip it from now on.
        for n in us:
            add_number(n, f"Rejected: {reason} · {details}", SOURCE_REJECTED)
        row["reason"] = reason
        return "rejected"
    soft = rules.skip_reason(row, brands)
    if soft:
        row["reason"] = soft
        return "skipped"
    for n in us:
        add_number(n, details)
    return "new_lead"


def import_once(leads_csv: Path, flag: Path) -> int:
    """Add numbers this app found before the shared database existed to it, one time only."""
    if flag.exists() or not leads_csv.exists():
        return 0
    added = 0
    with leads_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row.get("phones") and record_seller(row) == "new_lead":
            added += 1
    flag.write_text(f"imported {added} sellers on {datetime.now():%Y-%m-%d %H:%M}\n")
    return added
