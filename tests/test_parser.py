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


def test_has_next_page():
    from seller_parser import has_next_page
    assert has_next_page('<a class="s-pagination-item s-pagination-next" href="/s?k=x&page=2">Next</a>')
    assert not has_next_page('<span class="s-pagination-item s-pagination-next s-pagination-disabled">Next</span>')
    assert not has_next_page('<div>no pagination</div>')


MULTI_DEPT = '''<div id="departments"><ul>
  <li><span class="a-list-item"><a href="#"><span class="a-size-base a-color-base">Books</span></a></span></li>
  <li><span class="a-list-item"><a href="#"><span class="a-size-base a-color-base">Movies &amp; TV</span></a></span></li>
  <li><span class="a-list-item"><a href="#"><span class="a-size-base a-color-base">Video Games</span></a></span></li>
</ul></div>'''

SINGLE_DEPT = '''<div id="departments"><ul>
  <li><span class="a-list-item"><a href="#"><span class="a-size-base a-color-base">‹ Any Department</span></a></span></li>
  <li><span class="a-list-item"><span class="a-size-base a-color-base a-text-bold">Books</span></span></li>
  <li><span class="a-list-item"><a href="#"><span class="a-size-base a-color-base">Literature &amp; Fiction</span></a></span></li>
  <li><span class="a-list-item"><a href="#"><span class="a-size-base a-color-base">Children's Books</span></a></span></li>
</ul></div>'''


def test_departments_and_media_only():
    from seller_parser import is_media_only, parse_departments
    assert parse_departments(MULTI_DEPT) == ["Books", "Movies & TV", "Video Games"]
    assert is_media_only(parse_departments(MULTI_DEPT))
    assert parse_departments(SINGLE_DEPT) == ["Books"]          # sub-categories don't count
    assert is_media_only(parse_departments(SINGLE_DEPT))
    mixed = MULTI_DEPT.replace("Video Games", "Toys &amp; Games")
    assert parse_departments(mixed) == ["Books", "Movies & TV", "Toys & Games"]
    assert not is_media_only(parse_departments(mixed))           # sells something else too: still a lead
    assert parse_departments("<div>no menu</div>") == []
    assert not is_media_only([])                                 # couldn't tell: never banned
    assert is_media_only(["Books", "CDs & Vinyl"])               # music counts as media too


def test_brand_reading_and_name_matching():
    from seller_parser import parse_brands, parse_product_brand, same_name
    assert parse_product_brand('<a id="bylineInfo" href="#">Visit the bareMinerals Store</a>') == "bareMinerals"
    assert parse_product_brand('<a id="bylineInfo" href="#">Brand: bareMinerals</a>') == "bareMinerals"
    assert parse_product_brand("<div>no byline</div>") == ""
    html = ('<div id="brandsRefinements"><ul><li><span>bareMinerals</span></li><li><span>Clinique</span></li>'
            '<li><span>See more</span></li></ul></div>')
    assert parse_brands(html) == ["bareMinerals", "Clinique"]
    assert same_name("bareMinerals", "bareMinerals")
    assert same_name("BAREMINERALS Official Store", "bareMinerals")
    assert same_name("Bare Minerals Inc", "bareMinerals")
    assert not same_name("Beauty Deals Depot", "bareMinerals")
    assert not same_name("Bare Minerals Outlet", "bareMinerals")   # a differently named reseller stays a lead
    assert not same_name("", "") and not same_name("The Store", "Store")


def test_own_brand_rule():
    import rules
    seller = {"seller_name": "bareMinerals", "business_name": "Bare Escentuals Beauty, Inc."}
    assert rules.violation(seller | {"found_brand": "bareMinerals"}, None) == "Sells its own brand (bareMinerals)"
    assert rules.violation(seller, ["Beauty & Personal Care"], ["bareMinerals"]) == "Sells its own brand (bareMinerals)"
    reseller = {"seller_name": "Glow Beauty Deals", "business_name": "Glow LLC", "found_brand": "bareMinerals"}
    assert rules.violation(reseller, ["Beauty & Personal Care"], ["bareMinerals", "Clinique"]) is None
    # matched on the legal business name too
    assert rules.violation({"seller_name": "Shop 77", "business_name": "Lumi Toys LLC"}, None, ["Lumi Toys"]) \
        == "Sells its own brand (Lumi Toys)"
