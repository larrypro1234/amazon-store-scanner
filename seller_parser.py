"""Parse an Amazon seller profile page (amazon.com/sp?seller=...) into contact fields.

Pure functions only: no network. Everything here can be tested against saved HTML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import phonenumbers
from bs4 import BeautifulSoup, Tag

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RATING_RE = re.compile(r"(\d{1,3})%\s+positive.*?\(([\d,]+)\s+ratings?\)", re.S | re.I)
STARS_RE = re.compile(r"([\d.]+)\s+out of\s+5", re.I)
SELLER_ID_RE = re.compile(r"[?&](?:seller|smid|me)=([A-Z0-9]{10,20})\b")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# Markers that mean Amazon served a bot check instead of the page.
CAPTCHA_MARKERS = (
    "/errors/validateCaptcha",
    "Enter the characters you see below",
    "Type the characters you see in this image",
    "make sure you're not a robot",
    "Click the button below to continue shopping",
)


@dataclass
class SellerInfo:
    seller_id: str = ""
    seller_name: str = ""
    business_name: str = ""
    business_address: str = ""
    country: str = ""
    phones: list[str] = field(default_factory=list)
    phone_source: str = ""  # "seller_info" (labeled field) or "about_seller" (free text)
    emails: list[str] = field(default_factory=list)
    positive_pct: str = ""
    rating_count: str = ""
    stars: str = ""
    about_text: str = ""
    extra_fields: dict[str, str] = field(default_factory=dict)

    def to_row(self) -> dict[str, str]:
        row = asdict(self)
        row["phones"] = " | ".join(self.phones)
        row["emails"] = " | ".join(self.emails)
        row["extra_fields"] = "; ".join(f"{k}: {v}" for k, v in self.extra_fields.items())
        row["about_text"] = " ".join(self.about_text.split())[:500]
        return row


def is_captcha(html: str) -> bool:
    low = html.lower()
    return any(m.lower() in low for m in CAPTCHA_MARKERS)


# Amazon's own retail accounts: their profile pages have no third-party contact details.
AMAZON_SELLER_IDS = {"ATVPDKIKX0DER", "A3DWYIK6Y9EEQB", "A1F83G8C2ARO7P", "A3JWKAKR8XB7XF",
                     "A1X6FK5RDHNB96", "A1RKKUPIHCS9HS"}


def extract_seller_ids(html: str) -> list[str]:
    """Seller IDs linked from any page (product page, offer listing, search results)."""
    seen: dict[str, None] = {}
    for sid in SELLER_ID_RE.findall(html.replace("&amp;", "&")):
        if sid not in AMAZON_SELLER_IDS:
            seen.setdefault(sid, None)
    return list(seen)


def extract_asins(html: str) -> list[str]:
    """ASINs from a search results page."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, None] = {}
    for el in soup.select("[data-asin]"):
        asin = el.get("data-asin", "").strip()
        if re.fullmatch(r"[A-Z0-9]{10}", asin):
            seen.setdefault(asin, None)
    if not seen:  # layout without data-asin attributes: fall back to product links
        for asin in re.findall(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?=[/?\"'&])", html):
            seen.setdefault(asin, None)
    return list(seen)


# Departments that make a store a "media-only" seller (books, movies, music, video games).
MEDIA_DEPARTMENTS = {"books", "kindle store", "audible books & originals", "movies & tv", "prime video",
                     "cds & vinyl", "digital music", "video games"}


def parse_departments(html: str) -> list[str]:
    """Top-level departments listed in the left "Department" menu of a store's storefront page.

    When everything a store sells is in one department, Amazon narrows the menu to that department
    (shown in bold, under "Any Department") followed by its sub-categories; only the bold one counts then.
    """
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("#departments") or soup.select_one("[aria-labelledby='n-title']")
    if box is None:
        return []
    items = []
    for li in box.select("li"):
        text = " ".join(li.get_text(" ").split())
        if not text or text.lower().startswith(("any department", "see more", "see less", "‹")):
            continue
        bold = li.select_one(".a-text-bold")
        items.append((text, bold is not None))
    selected = [t for t, is_bold in items if is_bold]
    return selected[:1] if selected else [t for t, _ in items]


def parse_brands(html: str) -> list[str]:
    """Brands listed in the left "Brands" menu of a store's storefront page."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("#brandsRefinements li") or soup.select("li[id^='p_89/'], li[id^='p_123/']")
    brands = []
    for li in items:
        text = " ".join(li.get_text(" ").split())
        if text and not text.lower().startswith(("see more", "see less", "any brand")) and text not in brands:
            brands.append(text)
    return brands


def parse_result_titles(html: str) -> list[str]:
    """Product names on a search results page, such as a store's storefront."""
    soup = BeautifulSoup(html, "html.parser")
    titles = []
    for h2 in soup.select("[data-component-type='s-search-result'] h2"):
        text = h2.get("aria-label") or " ".join(h2.get_text(" ").split())
        if text:
            titles.append(text)
    return titles


def brands_from_titles(titles: list[str], listed: dict) -> list[str]:
    """Brands of a store's products, read from the start of each product name. Well-known brands come
    out with their listed name; other products give their first word ("XIJIAOYAN Phone Case" -> "XIJIAOYAN")."""
    from brand_list import brand_at_start
    out: list[str] = []
    for title in titles:
        brand = brand_at_start(title, listed)
        if brand is None:
            first = re.sub(r"[^\w'’&.-]+$", "", title.split()[0]) if title.split() else ""
            brand = first if len(first) >= 2 else None
        if brand and brand not in out:
            out.append(brand)
    return out


def parse_product_brand(html: str) -> str:
    """The brand on a product page ("Visit the bareMinerals Store" / "Brand: bareMinerals")."""
    byline = BeautifulSoup(html, "html.parser").select_one("#bylineInfo")
    text = " ".join(byline.get_text(" ").split()) if byline else ""
    m = re.search(r"Visit the (.+?) Store", text) or re.search(r"Brand:\s*(.+)", text)
    return m.group(1).strip() if m else ""


NAME_FILLER = {"official", "store", "shop", "the", "inc", "llc", "ltd", "co", "corp", "company", "brand",
               "brands", "us", "usa", "direct", "online", "amazon", "by"}


def same_name(a: str, b: str) -> bool:
    """Whether a seller name and a brand are the same name ("BAREMINERALS Official Store" = "bareMinerals")."""
    def key(s):
        words = re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).split()
        return "".join(w for w in words if w not in NAME_FILLER)
    ka, kb = key(a), key(b)
    return len(ka) >= 3 and ka == kb


def is_media_only(departments: list[str]) -> bool:
    return bool(departments) and all(d.lower() in MEDIA_DEPARTMENTS for d in departments)


def has_next_page(html: str) -> bool:
    """Whether a search results page shows a working "Next" button."""
    nxt = BeautifulSoup(html, "html.parser").select_one(".s-pagination-next")
    return nxt is not None and "s-pagination-disabled" not in (nxt.get("class") or [])


def _lines(el: Tag | None) -> list[str]:
    if el is None:
        return []
    return [s.strip() for s in el.get_text("\n").split("\n") if s.strip()]


def _find_section(soup: BeautifulSoup, ids: tuple[str, ...], heading: str) -> Tag | None:
    for i in ids:
        el = soup.find(id=i)
        if el:
            return el
    # Fallback: locate by heading text in case Amazon renames the ids.
    h = soup.find(lambda t: t.name in ("h2", "h3", "h4", "span", "div")
                  and t.get_text(strip=True) == heading)
    if h is None:
        return None
    parent = h.parent
    while parent is not None and len(parent.get_text(strip=True)) <= len(heading) + 5:
        parent = parent.parent
    return parent


def _labeled_fields(lines: list[str]) -> dict[str, list[str]]:
    """Turn ["Business Name:", "Foo LLC", "Business Address:", "1 Main St", "US"] into a dict.

    Handles both "Label:" + value on the next line(s) and "Label: value" on one line.
    """
    fields: dict[str, list[str]] = {}
    current = None
    for line in lines:
        m = re.match(r"^([A-Za-z][A-Za-z /&-]{1,40}):\s*(.*)$", line)
        if m:
            current = m.group(1).strip()
            fields[current] = [m.group(2).strip()] if m.group(2).strip() else []
        elif current is not None:
            fields[current].append(line)
    return fields


def _find_phones(text: str, region: str) -> list[str]:
    found: list[str] = []
    for match in phonenumbers.PhoneNumberMatcher(text, region or "US"):
        num = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        if num not in found:
            found.append(num)
    return found


def parse_seller_page(html: str, seller_id: str = "") -> SellerInfo:
    soup = BeautifulSoup(html, "html.parser")
    info = SellerInfo(seller_id=seller_id)

    name_el = soup.find(id="seller-name") or soup.find("h1")
    if name_el:
        info.seller_name = name_el.get_text(strip=True)

    summary = soup.find(id="seller-info-feedback-summary") or soup.find(id="feedback-summary-table")
    summary_text = " ".join(_lines(summary)) or soup.get_text(" ")
    if m := RATING_RE.search(summary_text):
        info.positive_pct, info.rating_count = m.group(1), m.group(2).replace(",", "")
    if m := STARS_RE.search(soup.get_text(" ")):
        info.stars = m.group(1)

    about = _find_section(soup, ("page-section-about-seller", "spp-expander-about-seller",
                                  "about-seller-text"), "About Seller")
    about_lines = [l for l in _lines(about) if l not in ("About Seller", "Read more", "Read less")]
    info.about_text = "\n".join(about_lines)

    detail = _find_section(soup, ("page-section-detail-seller-info",), "Detailed Seller Information")
    detail_lines = [l for l in _lines(detail) if l != "Detailed Seller Information"]
    fields = _labeled_fields(detail_lines)

    labeled_phones: list[str] = []
    for label, values in fields.items():
        low = label.lower()
        value = ", ".join(values)
        if low == "business name":
            info.business_name = value
        elif low == "business address":
            info.business_address = value
            if values and COUNTRY_RE.match(values[-1]):
                info.country = values[-1]
        elif "phone" in low:
            labeled_phones.append(value)
        elif "email" in low or "e-mail" in low:
            info.emails.extend(EMAIL_RE.findall(value))
        else:
            info.extra_fields[label] = value

    region = info.country or "US"
    if labeled_phones:
        for raw in labeled_phones:
            info.phones.extend(p for p in (_find_phones(raw, region) or [raw]) if p not in info.phones)
        info.phone_source = "seller_info"

    # Many sellers only put contact details in their free-text "About Seller" blurb.
    about_phones = [p for p in _find_phones(info.about_text, region) if p not in info.phones]
    if about_phones:
        info.phones.extend(about_phones)
        info.phone_source = info.phone_source or "about_seller"

    for email in EMAIL_RE.findall(info.about_text + "\n" + "\n".join(detail_lines)):
        if email not in info.emails:
            info.emails.append(email)

    return info
