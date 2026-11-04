import sqlite3

import pytest

import brand_list
import rules
import shared_db


@pytest.fixture(autouse=True)
def my_brands(tmp_path, monkeypatch):
    monkeypatch.setattr(brand_list, "MINE", tmp_path / "my-brands.txt")


def test_built_in_list_loads_and_matches_loosely():
    assert len(brand_list.read_list(brand_list.BUILT_IN)) > 400
    for name in ["LEGO", "lego", "L’Oréal Paris", "L'Oreal", "Melissa & Doug", "Fisher Price", "Fisher-Price",
                 "e.l.f. Cosmetics", "Dr. Brown's", "Hot Wheels Monster Trucks", "NIKE"]:
        assert brand_list.is_well_known(name), name
    for name in ["XIJIAOYAN", "Generic", "Unbranded", "Nikeland", "Legoland Fakes", "", "Toy Depot"]:
        assert not brand_list.is_well_known(name), name


def test_my_brands_are_added_and_kept_separately():
    assert not brand_list.is_well_known("Kiddo Corner")
    assert brand_list.save_mine("Kiddo Corner\n\n  Lumi Toys  \n") == 2
    assert brand_list.is_well_known("kiddo corner") and brand_list.is_well_known("Lumi Toys")
    assert brand_list.read_list(brand_list.MINE) == ["Kiddo Corner", "Lumi Toys"]


def test_skip_reason():
    assert rules.skip_reason({"found_brand": "XIJIAOYAN"}, ["Generic", "QWERTY Home"]) == "No well-known brands"
    assert rules.skip_reason({"found_brand": "XIJIAOYAN"}, ["Generic", "Mattel"]) is None   # one is enough
    assert rules.skip_reason({"found_brand": "LEGO"}, None) is None
    assert rules.skip_reason({}, None) is None                                              # nothing seen: keep
    assert rules.skip_reason({"found_brand": ""}, []) is None


def test_skipped_store_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_db, "DB_FILE", tmp_path / "numbers.db")
    row = {"seller_name": "Cheap Stuff 4U", "phones": "+1 512-555-0100", "found_brand": "XIJIAOYAN", "country": "US"}
    assert shared_db.record_seller(row, get_store=lambda: (["Home & Kitchen"], ["XIJIAOYAN"])) == "skipped"
    assert row["reason"] == "No well-known brands"
    con = sqlite3.connect(tmp_path / "numbers.db")
    assert con.execute("SELECT COUNT(*) FROM numbers").fetchone()[0] == 0
    # a reseller of a known brand is still a lead
    row2 = {"seller_name": "Toy Depot", "phones": "+1 512-555-0101", "found_brand": "XIJIAOYAN", "country": "US"}
    assert shared_db.record_seller(row2, get_store=lambda: (["Toys & Games"], ["Hasbro"])) == "new_lead"


def test_approval_notes_say_why():
    notes = rules.approval_notes({"found_brand": "XIJIAOYAN", "country": "US"}, ["Toys & Games", "Baby"],
                                 ["Hasbro", "Generic", "Mattel"])
    assert notes == ("Valid US phone number, not in the shared list · US business address · "
                     "Sells well-known brands: Hasbro, Mattel · "
                     "Not just books/movies/music/games (sells Toys & Games, Baby) · Not its own brand")
    blind = rules.approval_notes({}, None, None)
    assert "Brands couldn't be read (kept)" in blind and "Departments couldn't be read (kept)" in blind
    assert "Own brand: couldn't check (kept)" in blind


def test_new_lead_row_gets_its_note(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_db, "DB_FILE", tmp_path / "numbers.db")
    row = {"seller_name": "Toy Depot", "phones": "+1 512-555-0101", "found_brand": "LEGO",
           "business_address": "1 Main St, Austin, TX, 78701, US"}
    assert shared_db.record_seller(row, get_store=lambda: (["Toys & Games"], ["LEGO"])) == "new_lead"
    assert row["approved_why"].startswith("Valid US phone number, not in the shared list · "
                                          "US business address · Sells well-known brands: LEGO")


STOREFRONT = """<div id="departments"><ul><li><span>Beauty &amp; Personal Care</span></li><li><span>Toys &amp; Games</span></li></ul></div>
<div data-component-type="s-search-result"><h2 aria-label="Old Spice Body Wash for Men, 24 fl oz"><span>Old Spice Body Wash</span></h2></div>
<div data-component-type="s-search-result"><h2 aria-label="L'Oreal Paris Sulfate Free Shampoo, 13.6 Fl Oz"><span>x</span></h2></div>
<div data-component-type="s-search-result"><h2><span>XIJIAOYAN Phone Case for iPhone 15</span></h2></div>
<div data-component-type="s-search-result"><h2 aria-label="Fisher-Price Classic Xylophone Pull Toy"><span>x</span></h2></div>"""


def test_brands_from_storefront_product_names():
    from seller_parser import brands_from_titles, parse_brands, parse_result_titles
    titles = parse_result_titles(STOREFRONT)
    assert titles[2] == "XIJIAOYAN Phone Case for iPhone 15" and len(titles) == 4
    assert parse_brands(STOREFRONT) == []  # many-department stores have no Brands menu
    brands = brands_from_titles(titles, brand_list.names())
    assert brands == ["Old Spice", "L'Oreal", "XIJIAOYAN", "Fisher-Price"]
    assert rules.skip_reason({}, brands) is None  # sells well-known brands
    assert rules.skip_reason({}, brands_from_titles(["XIJIAOYAN Phone Case", "QWERTY Lamp"], brand_list.names())) \
        == "No well-known brands"
    # a brand owner whose products all carry its name
    own = brands_from_titles(["Sassy Fishy Fascination Station", "Sassy Stacks of Circles"], brand_list.names())
    assert rules.violation({"seller_name": "Sassy"}, ["Baby"], own) == "Sells its own brand (Sassy)"



def test_phone_must_be_a_valid_us_number():
    assert rules.valid_us_numbers({"phones": "+1 713-817-5533"}) == ["7138175533"]
    assert rules.valid_us_numbers({"phones": "+1 123-456-7890 | +1 999-123-4567"}) == []   # made up
    assert rules.valid_us_numbers({"phones": "+1 416-979-2000"}) == []                     # Toronto, Canada
    assert rules.valid_us_numbers({"phones": "+1 416-979-2000 | (713) 817-5533"}) == ["7138175533"]


def test_us_seller_checked_before_the_store_is_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_db, "DB_FILE", tmp_path / "numbers.db")
    opened = []
    store = lambda: opened.append(1) or (["Toys & Games"], ["LEGO"])
    fake = {"seller_name": "Fake Co", "phones": "+1 123-456-7890"}
    assert shared_db.record_seller(fake, get_store=store) == "bad_number"
    canada = {"seller_name": "Maple Toys", "phones": "+1 416-979-2000"}
    assert shared_db.record_seller(canada, get_store=store) == "bad_number"
    abroad = {"seller_name": "Far Away Ltd", "phones": "+1 713-817-5533", "country": "CN"}
    assert shared_db.record_seller(abroad, get_store=store) == "rejected"
    assert abroad["reason"] == "Not a US seller (business address in CN)"
    assert opened == []  # none of these needed their storefront read
    assert shared_db.lookup(["7138175533"]) == {"7138175533": "Rejected by Amazon app"}
    us = {"seller_name": "Toy Depot", "phones": "+1 917-592-1495", "country": "US"}  # same page as the phone
    assert shared_db.record_seller(us, get_store=store) == "new_lead" and opened == [1]



def test_address_must_be_in_the_us():
    assert rules.address_country({"business_address": "535 Luling St, Pearl, MS, 39208, US"}) == "US"
    assert rules.address_country({"country": "cn"}) == "CN"
    assert rules.not_us_seller({"business_address": "Bao'an District, Shenzhen, Guangdong, 518000, CN"}) \
        == "Not a US seller (business address in CN)"
    assert rules.not_us_seller({"business_address": "1 Main St, Austin, TX, 78701, US"}) is None
    assert rules.no_country({"business_address": "1 Main St, Austin"}) == "Business address country couldn't be read"


def test_unreadable_country_is_skipped_not_banned(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_db, "DB_FILE", tmp_path / "numbers.db")
    row = {"seller_name": "Mystery Co", "phones": "+1 917-592-1495", "business_address": "PO Box 7"}
    assert shared_db.record_seller(row, get_store=lambda: (["Toys & Games"], ["LEGO"])) == "skipped"
    assert shared_db.lookup(["9175921495"]) == {}  # nothing written
