import csv
import json
import sqlite3

import pytest

import shared_db


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "SellerFinderData" / "numbers.db"
    monkeypatch.setattr(shared_db, "DB_FILE", path)
    return path


def walmart_rows(db_file):
    """What the Walmart app already saved, before the Amazon app touches the file."""
    con = shared_db.db()
    with con:
        con.execute("INSERT INTO numbers VALUES ('9175921495', 'Walmart Seller · NY', 'Found by Walmart app', '2026-10-01')")
        con.execute("INSERT INTO leads VALUES ('9175921495', ?, 'added', '2026-10-01 10:00', '2026-10-02')",
                    (json.dumps({"marketplace": "Walmart", "seller_name": "Walmart Seller"}),))
        con.execute("CREATE TABLE searches (q TEXT)")
        con.execute("INSERT INTO searches VALUES ('lego')")
    con.close()


def dump(db_file):
    con = sqlite3.connect(db_file)
    out = {t: sorted(con.execute(f"SELECT * FROM {t}").fetchall()) for t in ("numbers", "leads", "searches")}
    con.close()
    return out


def row(phones, **kw):
    base = {"seller_id": "A1", "seller_name": "Toy Depot", "business_name": "Toy Depot LLC", "phones": phones,
            "emails": "sales@example.com | b@example.com", "business_address": "1 Main St, Austin, TX, 78701, US",
            "stars": "4.7", "rating_count": "312", "profile_url": "https://www.amazon.com/sp?seller=A1"}
    return base | kw


def test_normalize_phone():
    assert shared_db.normalize_phone("(917) 592-1495") == "9175921495"
    assert shared_db.normalize_phone("+1 917-592-1495") == "9175921495"
    assert shared_db.normalize_phone("9175921495") == "9175921495"
    assert shared_db.normalize_phone("+86 755 2345 6789") == ""
    assert shared_db.normalize_phone(None) == ""


def test_new_lead_saved_with_spec_fields(db_file):
    status = shared_db.record_seller(row("+1 512-555-0100"), '"lego" search: LEGO Classic',
                                     "https://www.amazon.com/dp/B0X", "lego")
    assert status == "new_lead"
    con = sqlite3.connect(db_file)
    phone, details, source, added = con.execute("SELECT * FROM numbers").fetchone()
    assert (phone, source) == ("5125550100", "Found by Amazon app")
    assert details == "Toy Depot · 1 Main St, Austin, TX, 78701, US · https://www.amazon.com/sp?seller=A1"
    key, data, lead_status, found, lead_added = con.execute("SELECT * FROM leads").fetchone()
    data = json.loads(data)
    assert (key, lead_status, lead_added) == ("5125550100", "new", "")
    assert data == {
        "marketplace": "Amazon", "status": "Good", "seller_name": "Toy Depot", "business_name": "Toy Depot LLC",
        "phone": "(512) 555-0100", "email": "sales@example.com", "address": "1 Main St, Austin, TX, 78701, US",
        "rating": "4.7", "review_count": "312", "seller_url": "https://www.amazon.com/sp?seller=A1",
        "store_mix": "", "found_from": '"lego" search: LEGO Classic',
        "found_from_url": "https://www.amazon.com/dp/B0X", "keywords": "lego"}


def test_number_walmart_already_has_is_not_a_lead_and_walmart_rows_untouched(db_file):
    walmart_rows(db_file)
    before = dump(db_file)
    assert shared_db.record_seller(row("(917) 592-1495")) == "known"
    assert dump(db_file) == before


def test_any_known_number_marks_seller_known(db_file):
    walmart_rows(db_file)
    assert shared_db.record_seller(row("+1 512-555-0100 | +1 917-592-1495")) == "known"
    assert shared_db.is_known("5125550100") is False


def test_non_us_and_missing_phones_are_not_saved(db_file):
    assert shared_db.record_seller(row("+86 755 2345 6789")) == "no_us_phone"
    assert shared_db.record_seller(row("")) == "no_phone"
    assert dump_tables_empty(db_file)


def dump_tables_empty(db_file):
    con = shared_db.db()
    n = con.execute("SELECT (SELECT COUNT(*) FROM numbers) + (SELECT COUNT(*) FROM leads)").fetchone()[0]
    con.close()
    return n == 0


def test_add_number_never_overwrites_existing_details(db_file):
    walmart_rows(db_file)
    shared_db.add_number("9175921495", "Amazon details")
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT details, source FROM numbers").fetchone() == ("Walmart Seller · NY", "Found by Walmart app")


def test_import_once(db_file, tmp_path):
    walmart_rows(db_file)
    leads = tmp_path / "leads.csv"
    with leads.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row("").keys()) + ["status"])
        w.writeheader()
        w.writerow(row("+1 877-283-5060", seller_id="A2", status="ok"))      # new to the shared list
        w.writerow(row("+1 917-592-1495", seller_id="A3", status="ok"))      # Walmart already has it
        w.writerow(row("+86 755 2345 6789", seller_id="A4", status="ok"))    # not a US number
        w.writerow(row("", seller_id="A5", status="no_phone"))
    flag = tmp_path / ".shared-db-imported"
    assert shared_db.import_once(leads, flag) == 1
    assert shared_db.is_known("8772835060")
    assert shared_db.import_once(leads, flag) == 0  # second start: nothing happens
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 2  # Walmart's + the one import
    assert con.execute("SELECT status FROM leads WHERE key='9175921495'").fetchone() == ("added",)


def test_live_state_shows_who_had_each_number(db_file):
    walmart_rows(db_file)  # 9175921495 saved by the Walmart app
    shared_db.record_seller(row("+1 512-555-0100"))  # new: saved by this app
    rows = [row("(917) 592-1495"), row("+1 512-555-0100"), row("+1 646-555-0199"),
            row("+86 755 2345 6789"), row("")]
    phones = [shared_db.normalize_phone(p) for r in rows for p in r["phones"].split(" | ")]
    sources = shared_db.lookup(phones)
    states = [shared_db.shared_state(r, sources) for r in rows]
    assert states == [{"state": "had", "source": "Found by Walmart app"},
                      {"state": "new", "source": "Found by Amazon app"},
                      {"state": "missing"}, {"state": "no_us_phone"}, {"state": "no_phone"}]


def test_lookup_is_read_only_and_handles_many_numbers(db_file):
    walmart_rows(db_file)
    before = dump(db_file)
    many = [f"{5550000000 + i}" for i in range(1200)] + ["9175921495"]
    assert shared_db.lookup(many) == {"9175921495": "Found by Walmart app"}
    assert dump(db_file) == before
