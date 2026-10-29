# Amazon Seller Contact Scraper

Opens Amazon seller profile pages (`amazon.com/sp?seller=...`) in a real Chrome window and saves each seller's
business name, address, phone numbers and emails to a CSV.

It looks for phone numbers in two places:
- **Detailed Seller Information**: a labeled "Phone" field, when the seller has one (`phone_source = seller_info`)
- **About Seller**: the free-text blurb, where many sellers put a contact number (`phone_source = about_seller`)

It ignores review text, so a number a customer typed in a review won't be picked up.

## The app (easiest)

Double-click **Start Scanner.command**. The first time, it sets itself up (a couple of minutes).
Then a page opens in your browser where you can:

- **Search Amazon** for a product word, or **paste store links**
- Press **Start scan** and watch stores and phone numbers appear
- Press **Stop** anytime (everything found so far is kept)
- Filter the list, copy numbers, and **Download** a spreadsheet (all stores, or only ones with a phone)

Keep the black Terminal window open while you use the app. Close it to quit.
If macOS blocks it, go to System Settings → Privacy & Security, scroll down, and click **Open Anyway**.

## Command line

### Setup (Mac)

```bash
cd amazon-seller-scraper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Use

```bash
# 1. You already have seller IDs or seller-page URLs (one per line, any mix)
python scraper.py sellers sellers.txt -o leads.csv

# 2. You have product ASINs: finds every seller offering them, then scrapes those sellers
python scraper.py asins asins.txt -o leads.csv

# 3. Start from a search: products on the first 3 result pages -> their sellers -> contacts
python scraper.py search "micro sd card" --pages 3 -o leads.csv
```

- A Chrome window opens and stays logged in between runs (profile saved in `.browser-profile/`).
- If Amazon shows a CAPTCHA, solve it in the Chrome window; the scan continues by itself.
- Results are written row by row. Press Ctrl+C anytime; re-running the same command skips sellers already in the CSV.
- Waits 4–9 seconds between pages by default (`--min-delay` / `--max-delay`). Going faster gets you CAPTCHAs.
- Other marketplaces: `--domain www.amazon.co.uk`, `--domain www.amazon.de`, etc.

#### CSV `status` column
| status | meaning |
|---|---|
| `ok` | at least one phone number found |
| `no_phone` | page loaded, no phone listed |
| `not_found` | page had no seller name (bad ID or removed seller) |
| `captcha` | blocked after 3 tries; re-run to retry these |

## When a page doesn't parse right

Amazon changes its HTML from time to time. Save the pages and check one offline:

```bash
python scraper.py sellers sellers.txt --save-html pages/
python scraper.py parse pages/seller-A21AQ316WNX0L1.html
```

## Tests

```bash
pip install pytest && python -m pytest tests
```

The fixtures in `tests/fixtures/` are hand-written copies of Amazon's seller-page layout, not saved live pages.
