"""Amazon seller contact scraper.

Visits seller profile pages (amazon.com/sp?seller=ID) in a real Chrome window and
writes business name, address, phone numbers and emails to a CSV.

    python scraper.py sellers sellers.txt -o leads.csv
    python scraper.py asins asins.txt -o leads.csv
    python scraper.py search "micro sd card" --pages 3 -o leads.csv
    python scraper.py parse saved_page.html
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from seller_parser import (
    SellerInfo, extract_asins, extract_seller_ids, is_captcha, parse_seller_page,
)

CSV_FIELDS = ["seller_id", "status", "seller_name", "storefront_url", "profile_url", "business_name",
              "phones", "phone_source", "emails", "business_address", "country", "positive_pct",
              "rating_count", "stars", "extra_fields", "about_text", "scraped_at"]

PROFILE_DIR = Path(__file__).parent / ".browser-profile"


class Stopped(Exception):
    """Raised inside a scan when the user presses Stop."""


class Browser:
    """A persistent Chromium profile so cookies (and any CAPTCHA you solve) carry over between runs."""

    def __init__(self, domain: str, headless: bool, min_delay: float, max_delay: float,
                 save_html: Path | None, log=print, should_stop=lambda: False, hidden: bool = False):
        from playwright.sync_api import sync_playwright

        self.log, self.should_stop = log, should_stop
        self.domain = domain
        self.headless = headless
        self.min_delay, self.max_delay = min_delay, max_delay
        self.save_html = save_html
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless, locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._last = 0.0
        self.hidden = hidden and not headless
        if self.hidden:
            self.set_window("minimized")
            self.log(f"Chrome window: {self.window_state()}")

    def window_state(self) -> str:
        try:
            cdp = self._ctx.new_cdp_session(self.page)
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            state = cdp.send("Browser.getWindowBounds", {"windowId": window_id})["bounds"]["windowState"]
            cdp.detach()
            return state
        except Exception as e:
            return f"unknown ({e})"

    def set_window(self, state: str) -> None:
        """Minimize ("minimized") or show ("normal") the Chrome window."""
        try:
            cdp = self._ctx.new_cdp_session(self.page)
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": state}})
            cdp.detach()
            if state == "normal":
                self.page.bring_to_front()
        except Exception:
            pass  # cosmetic only; scanning works either way

    def sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self.should_stop():
                raise Stopped
            time.sleep(min(0.5, max(0, end - time.time())))

    def _wait_for_captcha_solved(self, timeout: float = 300) -> str | None:
        end = time.time() + timeout
        while time.time() < end:
            self.sleep(3)
            html = self.page.content()
            if not is_captcha(html):
                return html
        return None

    def debug_snapshot(self, name: str) -> Path:
        """Save a picture and the HTML of what the browser shows right now."""
        out = Path(__file__).parent / "debug"
        out.mkdir(exist_ok=True)
        (out / f"{name}.html").write_text(self.page.content(), encoding="utf-8")
        if self.hidden:
            self.set_window("normal")  # minimized windows can't be photographed
        for attempt in range(3):
            try:
                self.page.wait_for_timeout(700)
                self.page.screenshot(path=str(out / f"{name}.png"))
                break
            except Exception:
                pass  # window still restoring; the HTML copy is saved regardless
        if self.hidden:
            self.set_window("minimized")
        return out / f"{name}.png"

    def get(self, url: str, name: str = "", wait_for: str | None = None) -> str | None:
        """Load a page politely; returns HTML, or None if Amazon keeps showing a bot check."""
        self.sleep(random.uniform(self.min_delay, self.max_delay) - (time.time() - self._last))
        self._last = time.time()

        for attempt in range(3):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                self.page.wait_for_timeout(1500)
                if wait_for:
                    try:
                        self.page.wait_for_selector(wait_for, timeout=10_000)
                    except Exception:
                        pass  # checked by the caller
                html = self.page.content()
            except Exception as e:  # network hiccup, timeout
                self.log(f"  ! load failed ({e.__class__.__name__}), retrying")
                self.sleep(10 * (attempt + 1))
                continue

            if is_captcha(html):
                if self.headless:
                    self.log("  ! Amazon showed a CAPTCHA; backing off 2 minutes "
                             "(run without --headless to solve it yourself)")
                    self.sleep(120)
                    continue
                self.log("  ! Amazon showed a CAPTCHA. Solve it in the Chrome window; "
                         "the scan continues by itself.")
                if self.hidden:
                    self.set_window("normal")
                html = self._wait_for_captcha_solved()
                if self.hidden:
                    self.set_window("minimized")
                if html is None:
                    continue

            if self.save_html and name:
                self.save_html.mkdir(parents=True, exist_ok=True)
                (self.save_html / f"{name}.html").write_text(html, encoding="utf-8")
            return html
        return None

    def close(self):
        self._ctx.close()
        self._pw.stop()


def read_lines(path: str) -> list[str]:
    return [l.strip() for l in Path(path).read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def to_seller_id(s: str) -> str:
    """Accept a bare seller ID or any Amazon URL that contains seller=/smid=/me=."""
    ids = extract_seller_ids(s if "?" in s else "?seller=" + s)
    return ids[0] if ids else s


def already_done(out: Path) -> set[str]:
    if not out.exists():
        return set()
    with out.open(newline="", encoding="utf-8") as f:
        return {r["seller_id"] for r in csv.DictReader(f) if r.get("status") != "captcha"}


OFFER_SELECTOR = "#aod-offer, #aod-pinned-offer"
MORE_OFFERS = re.compile(r"See \d+ more options|Show more (results|offers)", re.I)


def expand_offers(b: Browser, max_rounds: int = 8) -> None:
    """Load every seller in the open "All offers" panel ("See 31 more options", infinite scroll)."""
    page = b.page
    for _ in range(max_rounds):
        before = page.locator(OFFER_SELECTOR).count()
        more = page.get_by_text(MORE_OFFERS).first
        clicked = False
        try:
            if more.is_visible():
                more.click(timeout=3000)
                clicked = True
        except Exception:
            pass
        page.evaluate("""() => {
            const el = document.querySelector('#all-offers-display-scroller, #aod-container');
            if (el) el.scrollTop = el.scrollHeight;
        }""")
        b.sleep(1.5)
        if not clicked and page.locator(OFFER_SELECTOR).count() <= before:
            return


def iter_product_sellers(b: Browser, asins: list[str]):
    """For each product, yield (index, total, asin, seller IDs not seen earlier in this run)."""
    seen: set[str] = set()
    for i, asin in enumerate(asins, 1):
        # "?aod=1" opens the product page with its "All offers" panel showing: the featured
        # seller and every other seller of this product are linked on one page.
        html = b.get(f"https://{b.domain}/dp/{asin}?aod=1", name=f"asin-{asin}",
                     wait_for=OFFER_SELECTOR + ", #aod-offer-list")
        found: list[str] = []
        if html:
            expand_offers(b)
            found = extract_seller_ids(b.page.content())
        new = [s for s in found if s not in seen]
        seen.update(new)
        b.log(f"Product {i}/{len(asins)} ({asin}): {len(found)} sellers, {len(new)} new")
        yield i, len(asins), asin, new


def discover_from_asins(b: Browser, asins: list[str]) -> list[str]:
    return [sid for *_, new in iter_product_sellers(b, asins) for sid in new]


def find_products(b: Browser, query: str, pages: int) -> list[str]:
    asins: dict[str, None] = {}
    for p in range(1, pages + 1):
        html = b.get(f"https://{b.domain}/s?k={quote_plus(query)}&page={p}", name=f"search-{p}",
                     wait_for="[data-asin], [data-component-type='s-search-result']")
        if not html:
            break
        page_asins = extract_asins(html)
        b.log(f"Search page {p}: {len(page_asins)} products")
        if not page_asins:
            pic = b.debug_snapshot(f"search-page-{p}")
            b.log(f"  ! No products on this page. Amazon's page title was: {b.page.title()!r}. "
                  f"Saved a picture of it: {pic}")
            break
        asins.update(dict.fromkeys(page_asins))
    return list(asins)


def discover_from_search(b: Browser, query: str, pages: int) -> list[str]:
    return discover_from_asins(b, find_products(b, query, pages))


class SellerChecker:
    """Visits seller profile pages and appends one CSV row per store. Skips stores already in the CSV."""

    def __init__(self, b: Browser, out: Path, on_progress=None):
        self.b, self.out, self.on_progress = b, out, on_progress
        self.skip = already_done(out)
        self.queued = self.checked = self.with_phone = self.skipped = 0

    def check(self, seller_ids: list[str]) -> None:
        todo = [s for s in dict.fromkeys(seller_ids) if s not in self.skip]
        self.skipped += len(seller_ids) - len(todo)
        self.queued += len(todo)
        self._report()
        for sid in todo:
            self._check_one(sid)

    def _check_one(self, sid: str) -> None:
        b = self.b
        url = f"https://{b.domain}/sp?seller={sid}"
        html = b.get(url, name=f"seller-{sid}")
        if html is None:
            info, status = SellerInfo(seller_id=sid), "captcha"
        else:
            info = parse_seller_page(html, sid)
            status = "ok" if info.phones else "no_phone"
            if not info.seller_name and not info.business_name:
                status = "not_found"
        row = info.to_row() | {
            "status": status,
            "profile_url": url,
            "storefront_url": f"https://{b.domain}/s?me={sid}",
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        new_file = not self.out.exists() or self.out.stat().st_size == 0
        fields = CSV_FIELDS
        if not new_file:  # keep the column order the file already has (older versions differ)
            with self.out.open(newline="", encoding="utf-8") as f:
                fields = next(csv.reader(f), CSV_FIELDS)
        with self.out.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in fields})
        self.skip.add(sid)
        self.checked += 1
        self.with_phone += bool(info.phones)
        b.log(f"  Store {self.checked}/{self.queued} {info.seller_name or sid}: "
              f"{', '.join(info.phones) or status}")
        self._report()

    def _report(self) -> None:
        if self.on_progress:
            self.on_progress(self.checked, self.queued, self.with_phone)

    def finish(self) -> None:
        if self.skipped:
            self.b.log(f"Skipped {self.skipped} stores already checked in earlier scans")
        self.b.log(f"Done. {self.with_phone}/{self.checked} stores had a phone number.")


def scrape_sellers(b: Browser, seller_ids: list[str], out: Path, on_progress=None) -> None:
    checker = SellerChecker(b, out, on_progress)
    checker.check(seller_ids)
    checker.finish()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def browse_opts(p):
        p.add_argument("-o", "--out", default="leads.csv", help="CSV to append to (default leads.csv)")
        p.add_argument("--domain", default="www.amazon.com", help="e.g. www.amazon.co.uk")
        p.add_argument("--headless", action="store_true", help="hide the browser window")
        p.add_argument("--min-delay", type=float, default=4, help="seconds between pages (min)")
        p.add_argument("--max-delay", type=float, default=9, help="seconds between pages (max)")
        p.add_argument("--save-html", type=Path, help="save every page to this folder for debugging")

    p = sub.add_parser("sellers", help="file of seller IDs or seller-page URLs, one per line")
    p.add_argument("file"); browse_opts(p)
    p = sub.add_parser("asins", help="file of ASINs: find every seller offering them, then scrape")
    p.add_argument("file"); browse_opts(p)
    p = sub.add_parser("search", help="search Amazon, collect sellers of the results, then scrape")
    p.add_argument("query"); p.add_argument("--pages", type=int, default=1); browse_opts(p)
    p = sub.add_parser("parse", help="parse a saved seller-page HTML file and print JSON (offline)")
    p.add_argument("html_file")

    args = ap.parse_args(argv)

    if args.cmd == "parse":
        html = Path(args.html_file).read_text(encoding="utf-8")
        m = re.search(r"seller=([A-Z0-9]{10,20})", html)
        print(json.dumps(parse_seller_page(html, m.group(1) if m else "").to_row(), indent=2))
        return

    b = Browser(args.domain, args.headless, args.min_delay, args.max_delay, args.save_html)
    try:
        if args.cmd == "sellers":
            ids = list(dict.fromkeys(to_seller_id(s) for s in read_lines(args.file)))
        elif args.cmd == "asins":
            ids = discover_from_asins(b, read_lines(args.file))
        else:
            ids = discover_from_search(b, args.query, args.pages)
        print(f"{len(ids)} sellers to check\n")
        scrape_sellers(b, ids, Path(args.out))
    except (KeyboardInterrupt, Stopped):
        print("\nStopped. Re-run the same command to resume where you left off.")
    finally:
        b.close()


if __name__ == "__main__":
    main()
