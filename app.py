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

import brand_list
import rules
import scraper
import shared_db
import updater

HERE = Path(__file__).parent
LEADS = HERE / "leads.csv"
CLEARED = HERE / "cleared.json"  # stores whose lead was already downloaded: hidden, never deleted
DELETED = HERE / "deleted.json"  # leads removed by hand or by a re-check: {seller_id: {when, reason}}
RULES_CHECKED = HERE / "rules-checked.json"  # {seller_id: rules version the lead last passed}
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

    @staticmethod
    def _on_status(sid: str, status: str) -> None:
        if status == "new_lead":  # found under today's rules: no re-check needed until they change
            update_json(RULES_CHECKED, lambda d: d.__setitem__(sid, rules.RULES_VERSION))

    def start_recheck(self, everything: bool) -> None:
        self.stop_requested = False
        self.done = self.total = self.phones = 0
        self.log_lines.clear()
        self.thread = threading.Thread(target=self._recheck, args=(everything,), daemon=True)
        self.thread.start()

    def _recheck(self, everything: bool) -> None:
        """Run the current rules again over the leads still waiting in the list (not downloaded,
        not deleted). Leads that now break a rule are deleted with the reason."""
        b = None
        try:
            self.phase = "rechecking"
            waiting = leads_to_recheck(everything)
            if not waiting:
                self.log("No waiting leads need re-checking.")
                self.phase = "done"
                return
            self.log(f"Re-checking {len(waiting)} waiting lead{'s' * (len(waiting) > 1)} with the latest rules...")
            self.log("Opening Chrome...")
            b = scraper.Browser("www.amazon.com", headless=False, min_delay=4, max_delay=9, save_html=None,
                                log=self.log, should_stop=lambda: self.stop_requested, hidden=True)
            checker = scraper.SellerChecker(b, LEADS)
            removed = 0
            for i, r in enumerate(waiting, 1):
                sid = r["seller_id"]
                self.log(f"Lead {i}/{len(waiting)} {r.get('seller_name') or sid}")
                try:
                    departments, brands = checker.storefront(sid)
                except scraper.Stopped:
                    raise
                except Exception as e:  # one bad page shouldn't end the re-check
                    self.log(f"  ! Couldn't read this store ({e}); it'll be re-checked next time.")
                    departments = brands = None
                reason = rules.violation(r, departments, brands) or rules.skip_reason(r, brands)
                if reason:
                    mark_deleted(sid, f"Removed by re-check: {reason}")
                    removed += 1
                    self.log(f"  REMOVED: {reason}")
                elif departments is not None or brands is not None:  # unreadable store: try next time
                    update_json(RULES_CHECKED, lambda d: d.__setitem__(sid, rules.RULES_VERSION))
                self._progress(i, len(waiting), removed)
            self.log(f"Re-check done: {removed} of {len(waiting)} leads removed.")
            self.phase = "done"
        except scraper.Stopped:
            self.log("Stopped. Leads not re-checked yet will be checked next time.")
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

    def _run(self, mode, query, pages, links, domain, stop_after=0) -> None:
        b = None
        try:
            self.phase = "finding"
            self.log("Opening Chrome...")
            b = scraper.Browser(domain, headless=False, min_delay=4, max_delay=9, save_html=None,
                                log=self.log, should_stop=lambda: self.stop_requested, hidden=True)
            checker = scraper.SellerChecker(b, LEADS, on_progress=self._progress, shared=True,
                                            limit=stop_after, on_status=self._on_status)
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
                    for _, _, asin, title, brand, new_sellers in scraper.iter_product_sellers(b, page_asins, seen):
                        checker.check(new_sellers, found_from=f'"{query}" search: {title}',
                                      found_from_url=f"https://{domain}/dp/{asin}", keywords=query,
                                      found_brand=brand)
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


STATE_LOCK = threading.Lock()  # the scan thread and page requests both update these files


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def update_json(path: Path, change) -> None:
    with STATE_LOCK:
        data = load_json(path)
        change(data)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=0))
        tmp.replace(path)


def load_cleared() -> dict[str, str]:
    return load_json(CLEARED)


def mark_cleared(seller_ids: list[str]) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    update_json(CLEARED, lambda d: [d.setdefault(sid, stamp) for sid in seller_ids])


def mark_deleted(sid: str, reason: str) -> None:
    """Hide a lead from the list. Its number stays in the shared list, so it never comes back."""
    entry = {"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "reason": reason}
    update_json(DELETED, lambda d: d.setdefault(sid, entry))


def leads_to_recheck(everything: bool) -> list[dict]:
    """Leads still waiting in the list; unless `everything`, only those checked under older rules."""
    checked = load_json(RULES_CHECKED)
    return [r for r in read_results()
            if r.get("shared", {}).get("state") == "new" and not r["cleared"] and not r["deleted"]
            and (everything or checked.get(r["seller_id"], 0) < rules.RULES_VERSION)]


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
    cleared, deleted = load_cleared(), load_json(DELETED)
    for r in rows:
        r["cleared"] = cleared.get(r["seller_id"], "")
        d = deleted.get(r["seller_id"]) or {}
        r["deleted"] = d.get("reason", "")
        r["deleted_when"] = d.get("when", "")
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


@app.post("/api/recheck")
def recheck():
    if scan.running:
        return jsonify(error="Wait for the current scan to finish, or stop it first."), 409
    scan.start_recheck(everything=True)
    return jsonify(ok=True)


@app.post("/api/delete")
def delete_lead():
    sid = (request.get_json(force=True).get("seller_id") or "").strip()
    if not sid:
        return jsonify(error="Missing seller_id"), 400
    mark_deleted(sid, "Deleted by hand")
    return jsonify(ok=True)


@app.post("/api/restore")
def restore_lead():
    sid = (request.get_json(force=True).get("seller_id") or "").strip()
    update_json(DELETED, lambda d: d.pop(sid, None))
    return jsonify(ok=True)


@app.get("/api/brands")
def get_brands():
    return jsonify(built_in=len(brand_list.read_list(brand_list.BUILT_IN)),
                   mine="\n".join(brand_list.read_list(brand_list.MINE)))


@app.post("/api/brands")
def set_brands():
    n = brand_list.save_mine(request.get_json(force=True).get("mine") or "")
    return jsonify(ok=True, mine=n)


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
        rows = [r for r in rows if r.get("shared", {}).get("state") == "new"
                and not r["cleared"] and not r["deleted"]]
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

    def auto_recheck():
        try:
            if not scan.running and leads_to_recheck(everything=False):
                scan.start_recheck(everything=False)
        except Exception as e:
            print(f"Couldn't start the automatic re-check: {e}")
    threading.Timer(4.0, auto_recheck).start()  # after an update: apply new rules to waiting leads
    # make_server (unlike app.run) keeps the listening socket out of a restarted process
    make_server("127.0.0.1", port, app, threaded=True).serve_forever()
