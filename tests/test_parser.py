from pathlib import Path

from seller_parser import extract_asins, extract_seller_ids, is_captcha, parse_seller_page

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_labeled_phone_in_seller_info():
    info = parse_seller_page(load("seller_labeled_phone.html"), "A1")
    assert info.seller_name == "Reflective Illumination LLC"
    assert info.business_name == "Reflective Illumination LLC"
    assert info.business_address == "1200 Market St, Suite 400, Denver, CO, 80202, US"
    assert info.country == "US"
    assert info.phones == ["+1 877-283-5060"]
    assert info.phone_source == "seller_info"
    assert (info.positive_pct, info.rating_count, info.stars) == ("98", "1204", "4.8")


def test_review_text_and_order_numbers_are_not_phones():
    info = parse_seller_page(load("seller_labeled_phone.html"))
    assert "+1 415-555-0199" not in info.phones


def test_phone_and_email_from_about_text_use_seller_country():
    info = parse_seller_page(load("seller_about_phone.html"), "A21AQ316WNX0L1")
    assert info.country == "CN"
    assert info.phones == ["+86 755 2345 6789"]
    assert info.phone_source == "about_seller"
    assert info.emails == ["support@kexin-example.com"]
    assert (info.positive_pct, info.rating_count) == ("97", "502")


def test_no_phone():
    info = parse_seller_page(load("seller_no_phone.html"))
    assert info.business_name == "Quiet Store Inc"
    assert info.phones == [] and info.phone_source == ""


def test_falls_back_to_heading_when_ids_change():
    html = load("seller_labeled_phone.html").replace("page-section-detail-seller-info", "renamed")
    assert parse_seller_page(html).phones == ["+1 877-283-5060"]


def test_captcha_detection():
    assert is_captcha('<form action="/errors/validateCaptcha">')
    assert not is_captcha(load("seller_no_phone.html"))


def test_extract_ids():
    html = ('<a href="/sp?ie=UTF8&amp;seller=A21AQ316WNX0L1&amp;asin=B0FBW3483C">x</a>'
            '<a href="/gp/aag/main?smid=A3XYZ12345ABCD">y</a><a href="/sp?seller=A21AQ316WNX0L1">z</a>')
    assert extract_seller_ids(html) == ["A21AQ316WNX0L1", "A3XYZ12345ABCD"]
    assert extract_asins('<div data-asin="B0FBW3483C"></div><div data-asin=""></div>') == ["B0FBW3483C"]


def test_continue_shopping_interstitial_counts_as_bot_check():
    assert is_captcha("<p>Click the button below to continue shopping</p>")
    assert is_captcha("Sorry, we just need to make sure you're not a robot.")


def test_asins_from_links_when_no_data_asin():
    html = '<a href="/Some-Case/dp/B0CASE0001/ref=sr_1_1">a</a><a href="/gp/product/B0CASE0002?th=1">b</a>'
    assert extract_asins(html) == ["B0CASE0001", "B0CASE0002"]


def test_offer_panel_sellers_skip_amazon_itself():
    html = ('<div id="aod-pinned-offer"><a href="/gp/help/seller/at-a-glance.html?ie=UTF8&amp;seller=ATVPDKIKX0DER">Amazon.com</a></div>'
            '<div id="aod-offer"><a href="/gp/aag/main?ie=UTF8&amp;seller=A2OFFERSELL01&amp;isAmazonFulfilled=1">Shop A</a></div>'
            '<div id="aod-offer"><a href="/gp/aag/main?seller=A3OFFERSELL02">Shop B</a></div>')
    assert extract_seller_ids(html) == ["A2OFFERSELL01", "A3OFFERSELL02"]
