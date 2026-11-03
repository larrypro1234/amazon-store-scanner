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
To get a normal Mac app instead (no Terminal window), run `bash make_mac_app.sh` once; then open **Amazon Scanner** from Spotlight or Applications.
If macOS blocks it, go to System Settings → Privacy & Security, scroll down, and click **Open Anyway**.

## Shared seller list (with the Walmart Seller Finder)

The app shares one thing with the Walmart Seller Finder: the list of every number either app already has
(`numbers` in `~/SellerFinderData/numbers.db`). Each store's phone is checked against it at the moment
the store is checked:

- **New lead**: a US number neither app has yet. The number is added (`source = "Found by Amazon app"`),
  so neither app finds it again. The lead itself stays in this app for you to review and download.
- **Banned**: before a store becomes a new lead, the rules in `rules.py` run. Today: if its storefront's
  Department menu shows only books, movies, music and video games (Books, Kindle Store, Audible, Movies &
  TV, Prime Video, CDs & Vinyl, Digital Music, Video Games), its number is added as
  `Rejected by Amazon app` with the reason, so both apps skip it.
  Also banned: stores that **sell their own brand** (the brand owner, e.g. the bareMinerals store selling
  bareMinerals). The seller's name or business name is compared with the brand of the product it was found
  on and with the brands in its storefront's Brands menu, ignoring words like "Official", "Store", "Inc".
- **Skipped**: stores that don't sell any well-known brand (none of the brands seen on the product they
  were found on or in their storefront's Brands menu is in `brands.txt` or your `my-brands.txt`). Nothing
  is written to the shared list for these, since the list may just be missing a brand. Add brands under
  **Well-known brands** in the app.
- **Already have this number** / **No US phone** / **no phone**: nothing is written.

Amazon leads are not added to the shared Leads inbox. The app never deletes or changes rows in the
shared database. On its first start it added the numbers it had found before (once).

### Reviewing leads

- **Delete** removes a lead from the list (the number stays in the shared list, so it never comes back).
  **Show deleted** shows them again, with **Undo delete**.
- **Re-check leads** runs the current rules again on leads still waiting (not downloaded, not deleted).
  When a rule is added or changed, `RULES_VERSION` in `rules.py` goes up and the app re-checks waiting
  leads by itself on its next start (for example right after an update).

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
