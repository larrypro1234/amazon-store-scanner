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


def extract_seller_ids(html: str) -> list[str]:
    """Seller IDs linked from any page (product page, offer listing, search results)."""
    seen: dict[str, None] = {}
    for sid in SELLER_ID_RE.findall(html.replace("&amp;", "&")):
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
