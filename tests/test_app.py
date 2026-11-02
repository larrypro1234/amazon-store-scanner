import csv

import pytest

import app
import rules
import scraper
import shared_db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_db, "DB_FILE", tmp_path / "SellerFinderData" / "numbers.db")
    for name in ("LEADS", "CLEARED", "DELETED", "RULES_CHECKED"):
        monkeypatch.setattr(app, name, tmp_path / getattr(app, name).name)
    rows = []
    for i, sid in enumerate(["A1BOOKS", "A2TOYS", "A3OLD"]):
        r = {"seller_id": sid, "status": "new_lead", "seller_name": sid, "phones": f"+1 512-555-010{i}",
             "scraped_at": f"2026-11-02T10:0{i}:00+00:00"}
        shared_db.add_number(shared_db.normalize_phone(r["phones"]), sid)
        rows.append(r)
    with app.LEADS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=scraper.CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return app.app.test_client()


def by_id(client):
    return {r["seller_id"]: r for r in client.get("/api/results").get_json()}


def test_delete_and_undo(env):
    assert env.post("/api/delete", json={"seller_id": "A2TOYS"}).status_code == 200
    r = by_id(env)["A2TOYS"]
    assert r["deleted"] == "Deleted by hand" and r["deleted_when"]
    assert shared_db.is_known("5125550101")  # number stays in the shared list: never comes back
    # deleted leads aren't downloaded as new leads
    body = env.get("/api/download?new=1").get_data(as_text=True)
    assert "A2TOYS" not in body and "A1BOOKS" in body
    env.post("/api/restore", json={"seller_id": "A2TOYS"})
    assert by_id(env)["A2TOYS"]["deleted"] == ""


def test_recheck_applies_new_rules_to_waiting_leads(env, monkeypatch):
    class FakeBrowser:
        def __init__(self, *a, **k): pass
        def close(self): pass
    monkeypatch.setattr(scraper, "Browser", FakeBrowser)
    stores = {"A1BOOKS": ["Books", "Movies & TV"], "A2TOYS": ["Toys & Games"], "A3OLD": None}
    monkeypatch.setattr(scraper.SellerChecker, "departments", lambda self, sid: stores[sid])
    # A3OLD was downloaded already: not waiting, so not re-checked
    app.mark_cleared(["A3OLD"])

    assert {r["seller_id"] for r in app.leads_to_recheck(everything=False)} == {"A1BOOKS", "A2TOYS"}
    scan = app.Scan()
    scan._recheck(everything=False)
    assert scan.phase == "done"

    rows = by_id(env)
    assert rows["A1BOOKS"]["deleted"] == f"Removed by re-check: {rules.MEDIA_ONLY_REASON}"
    assert rows["A2TOYS"]["deleted"] == ""
    # passed the current rules: not re-checked again until RULES_VERSION goes up
    assert app.leads_to_recheck(everything=False) == []
    monkeypatch.setattr(rules, "RULES_VERSION", rules.RULES_VERSION + 1)
    assert [r["seller_id"] for r in app.leads_to_recheck(everything=False)] == ["A2TOYS"]
