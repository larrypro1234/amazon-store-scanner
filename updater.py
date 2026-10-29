"""Self-update from the GitHub repo: compare VERSION files, download the zip, copy it over this folder."""

from __future__ import annotations

import io
import shutil
import ssl
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "larrypro1234/amazon-store-scanner"
BRANCH = "main"
HERE = Path(__file__).parent
# Never overwritten by an update: your results, browser login, and installed packages.
KEEP = {".venv", ".browser-profile", "leads.csv", "leads-export.csv", "debug", "scanner.log", ".git"}


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "amazon-store-scanner"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read()


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.strip().split(".") if x.isdigit())


def current_version() -> str:
    try:
        return (HERE / "VERSION").read_text().strip()
    except FileNotFoundError:
        return "0"


def check() -> dict:
    current = current_version()
    try:
        latest = _fetch(f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION", timeout=10).decode().strip()
    except Exception as e:
        return {"current": current, "latest": None, "available": False, "error": str(e)}
    return {"current": current, "latest": latest, "available": _parse(latest) > _parse(current)}


def install() -> str:
    """Download the latest code over this folder and install any new packages. Returns the new version."""
    data = _fetch(f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}", timeout=60)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = z.namelist()[0].split("/")[0] + "/"
        for info in z.infolist():
            rel = info.filename[len(root):]
            if not rel or rel.split("/")[0] in KEEP:
                continue
            dest = HERE / rel
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            if (info.external_attr >> 16) & 0o111:
                dest.chmod(0o755)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                    "-r", str(HERE / "requirements.txt")], check=False)
    return current_version()
