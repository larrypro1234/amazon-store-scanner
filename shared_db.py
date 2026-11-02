"""The seller database shared with the Walmart Seller Finder (see SHARED_DATABASE.md).

Both apps open ~/SellerFinderData/numbers.db. Rules: one small operation per connection,
never delete or rewrite rows, never change a lead's status, US numbers only (10 digits).
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from seller_parser import is_media_only

DB_FILE = Path.home() / "SellerFinderData" / "numbers.db"
SOURCE_FOUND = "Found by Amazon app"
SOURCE_REJECTED = "Rejected by Amazon app"
MEDIA_ONLY_REASON = "Only sells books, movies & video games"


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


def add_lead(lead: dict) -> None:
    key = normalize_phone(lead.get("phone")) or lead.get("seller_url") or lead["seller_name"].lower()
    con = db()
    try:
        with con:
            con.execute("INSERT OR IGNORE INTO leads (key, data, found) VALUES (?, ?, ?)",
                        (key, json.dumps(lead), datetime.now().strftime("%Y-%m-%d %H:%M")))
    finally:
        con.close()


def lead_from_row(row: dict, phone: str, found_from: str = "", found_from_url: str = "",
                  keywords: str = "") -> dict:
    """The shared `leads.data` object for a seller row from this app (field names fixed by the spec)."""
    return {
        "marketplace": "Amazon",
        "status": "Good",
        "seller_name": row.get("seller_name") or row.get("business_name") or row.get("seller_id", ""),
        "business_name": row.get("business_name", ""),
        "phone": pretty_phone(phone),
        "email": (row.get("emails") or "").split(" | ")[0],
        "address": row.get("business_address", ""),
        "rating": row.get("stars", ""),
        "review_count": row.get("rating_count", ""),
        "seller_url": row.get("profile_url", ""),
        "store_mix": row.get("store_mix", ""),
        "found_from": found_from,
        "found_from_url": found_from_url,
        "keywords": keywords,
    }


def details_for(row: dict) -> str:
    parts = [row.get("seller_name") or row.get("business_name"), row.get("business_address"),
             row.get("profile_url")]
    return " · ".join(p for p in parts if p)


def record_seller(row: dict, found_from: str = "", found_from_url: str = "", keywords: str = "",
                  get_departments=None) -> str:
    """Decide what a freshly scraped seller is, saving it to the shared database when it's a new lead.

    Returns "new_lead", "rejected" (sells only books/movies/video games), "known" (already have this
    number), "no_us_phone", or "no_phone". The check against `numbers` happens right now, not from a
    cached copy. `get_departments` (called only for would-be new leads) returns the store's departments,
    or None when the storefront couldn't be read; then the seller is not rejected.
    """
    raw = [p for p in (row.get("phones") or "").split(" | ") if p]
    if not raw:
        return "no_phone"
    us = list(dict.fromkeys(n for n in map(normalize_phone, raw) if n))
    if not us:
        return "no_us_phone"
    if any(is_known(n) for n in us):
        return "known"
    departments = get_departments() if get_departments else None
    if departments:
        row["store_mix"] = " · ".join(departments)
    details = details_for(row)
    if departments and is_media_only(departments):
        # A real reason about the seller: saved as rejected so both apps skip it from now on.
        for n in us:
            add_number(n, f"Rejected: {MEDIA_ONLY_REASON} · {details}", SOURCE_REJECTED)
        return "rejected"
    for n in us:
        add_number(n, details)
    add_lead(lead_from_row(row, us[0], found_from, found_from_url, keywords))
    return "new_lead"


def import_once(leads_csv: Path, flag: Path) -> int:
    """Bring numbers this app found before the shared database existed into it, one time only."""
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
