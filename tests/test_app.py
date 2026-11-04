import csv

import pytest

import app
import rules
import scraper
import shared_db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_db, "DB_FILE", tmp_path / "SellerFinderData" / "numbers.db")
    for name in ("LEADS", "CLEARED", "DELETED", "RULES_CHECKED", "APPROVALS"):
        monkeypatch.setattr(app, name, tmp_path / getattr(app, name).name)
    rows = []
    for i, sid in enumerate(["A1BOOKS", "A2TOYS", "A3OLD"]):
        r = {"seller_id": sid, "status": "new_lead", "seller_name": sid, "phones": f"+1 512-555-010{i}", "country": "US",
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
    stores = {"A1BOOKS": (["Books", "Movies & TV"], None), "A2TOYS": (["Toys & Games"], ["Lego", "Mattel"]),
              "A3OLD": (None, None)}
    monkeypatch.setattr(scraper.SellerChecker, "storefront", lambda self, sid: stores[sid])
    # A3OLD was downloaded already: not waiting, so not re-checked
    app.mark_cleared(["A3OLD"])

    assert {r["seller_id"] for r in app.leads_to_recheck(everything=False)} == {"A1BOOKS", "A2TOYS"}
    scan = app.Scan()
    scan._recheck(everything=False)
    assert scan.phase == "done"

    rows = by_id(env)
    assert rows["A1BOOKS"]["deleted"] == f"Removed by re-check: {rules.MEDIA_ONLY_REASON}"
    assert rows["A2TOYS"]["deleted"] == ""
    assert "Sells well-known brands: Lego, Mattel" in rows["A2TOYS"]["approved_why"]
    assert rows["A2TOYS"]["approved_why"].startswith("Re-checked ")
    # passed the current rules: not re-checked again until RULES_VERSION goes up
    assert app.leads_to_recheck(everything=False) == []
    monkeypatch.setattr(rules, "RULES_VERSION", rules.RULES_VERSION + 1)
    assert [r["seller_id"] for r in app.leads_to_recheck(everything=False)] == ["A2TOYS"]


def test_storefront_reads_every_page_until_the_last(env):
    def page(n, last=False):
        cards = "".join(f'<div data-component-type="s-search-result"><h2 aria-label="{t}"></h2></div>'
                        for t in {1: ["LEGO Classic Bricks", "XIJIAOYAN Phone Case"], 2: ["Mattel Barbie Doll"],
                                  3: ["Hasbro Monopoly Game"]}[n])
        nxt = ('<span class="s-pagination-next s-pagination-disabled">Next</span>' if last
               else '<a class="s-pagination-next" href="#">Next</a>')
        dept = '<div id="departments"><ul><li><span>Toys &amp; Games</span></li></ul></div>' if n == 1 else ""
        return dept + cards + nxt

    class FakeBrowser:
        domain = "www.amazon.com"
        visited = []
        def get(self, url, name="", wait_for=None):
            n = int(url.split("page=")[1]); self.visited.append(n)
            return page(n, last=n == 3)
        def log(self, msg): pass

    b = FakeBrowser()
    departments, brands = scraper.SellerChecker(b, app.LEADS, store_pages=100).storefront("A1")
    assert b.visited == [1, 2, 3]                       # stopped at the store's last page
    assert departments == ["Toys & Games"]
    assert brands == ["LEGO", "XIJIAOYAN", "Mattel", "Hasbro"]
    b.visited.clear()
    scraper.SellerChecker(b, app.LEADS, store_pages=1).storefront("A1")
    assert b.visited == [1]                             # "Store pages to read: 1"
