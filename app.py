"""Amazon Store Scanner: a local web app around scraper.py.

Double-click "Start Scanner.command" (or run `python app.py`); it opens the app in your browser.
"""

from __future__ import annotations

import csv
import os
import socket
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

import scraper
import updater

HERE = Path(__file__).parent
LEADS = HERE / "leads.csv"
PORT = 8791  # first choice; another free port is used if something else has it

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

    def start(self, mode: str, query: str, pages: int, links: list[str], domain: str) -> None:
        self.stop_requested = False
        self.done = self.total = self.phones = 0
        self.log_lines.clear()
        self.thread = threading.Thread(target=self._run, args=(mode, query, pages, links, domain),
                                       daemon=True)
        self.thread.start()

    def _progress(self, done: int, total: int, phones: int) -> None:
        self.done, self.total, self.phones = done, total, phones

    def _run(self, mode, query, pages, links, domain) -> None:
        b = None
        try:
            self.phase = "finding"
            self.log("Opening Chrome...")
            b = scraper.Browser(domain, headless=False, min_delay=4, max_delay=9, save_html=None,
                                log=self.log, should_stop=lambda: self.stop_requested)
            if mode == "search":
                self.log(f'Searching Amazon for "{query}" ({pages} page{"s" * (pages > 1)})')
                ids = scraper.discover_from_search(b, query, pages)
            else:
                ids = list(dict.fromkeys(scraper.to_seller_id(s) for s in links))
            self.log(f"Found {len(ids)} stores. Checking each one for a phone number...")
            self.phase = "checking"
            scraper.scrape_sellers(b, ids, LEADS, on_progress=self._progress)
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


def read_results() -> list[dict]:
    if not LEADS.exists():
        return []
    with LEADS.open(newline="", encoding="utf-8") as f:
        latest = {r["seller_id"]: r for r in csv.DictReader(f)}  # a re-check replaces older rows
    return sorted(latest.values(), key=lambda r: r.get("scraped_at", ""), reverse=True)


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
    pages = max(1, min(20, int(body.get("pages") or 1)))
    domain = body.get("domain") or "www.amazon.com"
    if mode == "search" and not query:
        return jsonify(error="Type what to search for."), 400
    if mode == "links" and not links:
        return jsonify(error="Paste at least one store link."), 400
    scan.start(mode, query, pages, links, domain)
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
    out = HERE / "leads-export.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=scraper.CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    name = "amazon-stores-with-phones.csv" if request.args.get("phones") == "1" else "amazon-stores.csv"
    return send_file(out, as_attachment=True, download_name=name)


def free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


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
        os.environ["SCANNER_NO_BROWSER"] = "1"  # the open page reloads itself
        os.execv(sys.executable, [sys.executable, str(HERE / "app.py")])

    threading.Timer(1.0, restart).start()
    return jsonify(ok=True, version=new_version)


if __name__ == "__main__":
    port = free_port(PORT)
    url = f"http://127.0.0.1:{port}"
    print(f"Amazon Store Scanner is running at {url}\nKeep this window open. Close it to quit.")
    if not os.environ.pop("SCANNER_NO_BROWSER", None):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
