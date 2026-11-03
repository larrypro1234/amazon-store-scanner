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
    row = {"seller_name": "Cheap Stuff 4U", "phones": "+1 512-555-0100", "found_brand": "XIJIAOYAN"}
    assert shared_db.record_seller(row, get_store=lambda: (["Home & Kitchen"], ["XIJIAOYAN"])) == "skipped"
    assert row["reason"] == "No well-known brands"
    con = sqlite3.connect(tmp_path / "numbers.db")
    assert con.execute("SELECT COUNT(*) FROM numbers").fetchone()[0] == 0
    # a reseller of a known brand is still a lead
    row2 = {"seller_name": "Toy Depot", "phones": "+1 512-555-0101", "found_brand": "XIJIAOYAN"}
    assert shared_db.record_seller(row2, get_store=lambda: (["Toys & Games"], ["Hasbro"])) == "new_lead"
