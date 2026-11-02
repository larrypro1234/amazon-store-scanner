"""Amazon Store Scanner: a local web app around scraper.py.

Double-click "Start Scanner.command" (or run `python app.py`); it opens the app in your browser.
"""

from __future__ import annotations

import csv
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.serving import make_server

import scraper
import shared_db
import updater

HERE = Path(__file__).parent
LEADS = HERE / "leads.csv"
CLEARED = HERE / "cleared.json"  # stores whose lead was already downloaded: hidden, never deleted
PORT = 8791  # first choice; another free port is used if something else has it
RESTART_EXIT_CODE = 3  # tells the launcher loop to start the app again after an update

app = Flask(__name__, static_folder=None)
BOOT_ID = f"{os.getpid()}-{datetime.now().timestamp()}"  # changes on every restart


class Scan:
    """The one scan that can run at a time. A worker thread owns the browser."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_requested = False
        self.log_lines: deque[str] = deque(maxlen=300)
        self.phase = "idle"  # idle | finding | checking | stopping | done | stopped | error
        self.done = self.total = self.phones = 0

    def log(self, msg: str) -> None:
        with self.lock:
            self.log_lines.append(f"{datetime.now():%H:%M:%S}  {msg.strip()}")

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, mode: str, query: str, pages: int, links: list[str], domain: str,
              stop_after: int = 0) -> None:
        self.stop_requested = False
        self.done = self.total = self.phones = 0
        self.log_lines.clear()
        self.thread = threading.Thread(target=self._run, args=(mode, query, pages, links, domain, stop_after),
                                       daemon=True)
        self.thread.start()

    def _progress(self, done: int, total: int, phones: int) -> None:
        self.done, self.total, self.phones = done, total, phones

    def _run(self, mode, query, pages, links, domain, stop_after=0) -> None:
        b = None
        try:
            self.phase = "finding"
            self.log("Opening Chrome...")
            b = scraper.Browser(domain, headless=False, min_delay=4, max_delay=9, save_html=None,
                                log=self.log, should_stop=lambda: self.stop_requested, hidden=True)
            checker = scraper.SellerChecker(b, LEADS, on_progress=self._progress, shared=True,
                                            limit=stop_after)
            if stop_after:
                self.log(f"Quick check: stops after {stop_after} new leads.")
            if mode == "search":
                how_many = "all pages" if pages == scraper.ALL_PAGES else f'{pages} page{"s" * (pages > 1)}'
                self.log(f'Searching Amazon for "{query}" ({how_many})')
                self.phase = "checking"
                seen: set[str] = set()
                # One results page at a time: check its products' stores before loading the next page,
                # so numbers show up as the scan goes.
                for page_asins in scraper.iter_search_pages(b, query, pages):
                    for _, _, asin, title, new_sellers in scraper.iter_product_sellers(b, page_asins, seen):
                        checker.check(new_sellers, found_from=f'"{query}" search: {title}',
                                      found_from_url=f"https://{domain}/dp/{asin}", keywords=query)
                        if checker.reached:
                            break
                    if checker.reached:
                        break
            else:
                self.phase = "checking"
                checker.check([scraper.to_seller_id(s) for s in links], found_from="Pasted store link")
            if checker.reached:
                self.log(f"Found {stop_after} new leads, stopping here so you can spot-check them.")
            checker.finish()
            self.phase = "done"
        except scraper.Stopped:
            self.log("Stopped. Everything checked so far is saved.")
            self.phase = "stopped"
        except Exception as e:
            self.log(f"Something went wrong: {e}")
            self.phase = "error"
        finally:
            if b is not None:
                try:
                    b.close()
                except Exception:
                    pass


scan = Scan()


def load_cleared() -> dict[str, str]:
    try:
        return json.loads(CLEARED.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def mark_cleared(seller_ids: list[str]) -> None:
    cleared = load_cleared()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    for sid in seller_ids:
        cleared.setdefault(sid, stamp)
    tmp = CLEARED.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleared, indent=0))
    tmp.replace(CLEARED)


def read_results() -> list[dict]:
    if not LEADS.exists():
        return []
    with LEADS.open(newline="", encoding="utf-8") as f:
        latest = {r["seller_id"]: r for r in csv.DictReader(f)}  # a re-check replaces older rows
    rows = sorted(latest.values(), key=lambda r: r.get("scraped_at", ""), reverse=True)
    try:  # live status against the shared seller list, read at this moment
        phones = [shared_db.normalize_phone(p) for r in rows for p in (r.get("phones") or "").split(" | ")]
        sources = shared_db.lookup(phones)
        for r in rows:
            r["shared"] = shared_db.shared_state(r, sources)
    except Exception as e:
        print(f"Couldn't read the shared seller list: {e}")
    cleared = load_cleared()
    for r in rows:
        r["cleared"] = cleared.get(r["seller_id"], "")
    return rows


@app.get("/")
def index():
    return send_from_directory(HERE / "web", "index.html")


@app.post("/api/scan")
def start_scan():
    if scan.running:
        return jsonify(error="A scan is already running."), 409
    body = request.get_json(force=True)
    mode = body.get("mode", "search")
    query = (body.get("query") or "").strip()
    links = [l.strip() for l in (body.get("links") or "").splitlines() if l.strip()]
    raw_pages = str(body.get("pages") or "1")
    pages = scraper.ALL_PAGES if raw_pages == "all" else max(1, min(20, int(raw_pages)))
    domain = body.get("domain") or "www.amazon.com"
    if mode == "search" and not query:
        return jsonify(error="Type what to search for."), 400
    if mode == "links" and not links:
        return jsonify(error="Paste at least one store link."), 400
    stop_after = max(0, min(1000, int(body.get("stop_after") or 0)))
    scan.start(mode, query, pages, links, domain, stop_after)
    return jsonify(ok=True)


@app.post("/api/stop")
def stop_scan():
    if scan.running:
        scan.stop_requested = True
        scan.phase = "stopping"
        scan.log("Stopping after the current page...")
    return jsonify(ok=True)


@app.get("/api/status")
def status():
    with scan.lock:
        lines = list(scan.log_lines)
    return jsonify(running=scan.running, phase=scan.phase, done=scan.done, total=scan.total,
                   phones=scan.phones, log=lines)


@app.get("/api/results")
def results():
    return jsonify(read_results())


@app.get("/api/download")
def download():
    rows = read_results()
    if request.args.get("phones") == "1":
        rows = [r for r in rows if r.get("phones")]
    new_only = request.args.get("new") == "1"
    if new_only:  # only leads not downloaded before; they're cleared from the table afterwards
        rows = [r for r in rows if r.get("shared", {}).get("state") == "new" and not r["cleared"]]
    out = HERE / "leads-export.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=scraper.CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    name = (f"amazon-new-leads-{stamp}.csv" if new_only else
            "amazon-stores-with-phones.csv" if request.args.get("phones") == "1" else "amazon-stores.csv")
    if new_only:
        mark_cleared([r["seller_id"] for r in rows])
    return send_file(out, as_attachment=True, download_name=name)


def free_port(preferred: int, wait: float = 15) -> int:
    """The preferred port, waiting while a just-restarted copy of this app releases it."""
    deadline = time.time() + wait
    while True:
        with socket.socket() as s:
            # Same option the server uses, so connections still closing from the previous
            # copy (TIME_WAIT, common on macOS right after a restart) don't count as "in use".
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                if time.time() >= deadline:  # something else really has it
                    s.bind(("127.0.0.1", 0))
                    return s.getsockname()[1]
        time.sleep(0.5)


@app.get("/api/version")
def version():
    return jsonify(updater.check() | {"boot": BOOT_ID})


@app.post("/api/update")
def update():
    if scan.running:
        return jsonify(error="Stop the scan first, then update."), 409
    try:
        new_version = updater.install()
    except Exception as e:
        return jsonify(error=f"Update failed: {e}"), 500

    def restart():
        if os.environ.get("SCANNER_SUPERVISED"):
            os._exit(RESTART_EXIT_CODE)  # the launcher loop starts the new version
        os.environ["SCANNER_NO_BROWSER"] = "1"  # the open page reloads itself
        os.execv(sys.executable, [sys.executable, str(HERE / "app.py")])

    threading.Timer(1.0, restart).start()
    return jsonify(ok=True, version=new_version)


if __name__ == "__main__":
    try:  # one time: numbers this app found before the shared database existed
        n = shared_db.import_once(LEADS, HERE / ".shared-db-imported")
        if n:
            print(f"Added {n} earlier Amazon leads to the shared seller list")
    except Exception as e:
        print(f"Couldn't import earlier leads into the shared seller list (will retry next start): {e}")
    port = free_port(PORT)
    url = f"http://127.0.0.1:{port}"
    print(f"Amazon Store Scanner is running at {url}\nKeep this window open. Close it to quit.")
    if not os.environ.pop("SCANNER_NO_BROWSER", None):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # make_server (unlike app.run) keeps the listening socket out of a restarted process
    make_server("127.0.0.1", port, app, threaded=True).serve_forever()
