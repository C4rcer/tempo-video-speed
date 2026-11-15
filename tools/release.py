#!/usr/bin/env python3
"""Build, lint and publish this add-on to addons.mozilla.org (stdlib only).

Run from the add-on folder (the one holding src/ and store-assets/):

  python tools/release.py build          zip src/ into dist/<slug>-<version>.zip
  python tools/release.py lint           build, extract the zip, run web-ext lint on it
  python tools/release.py submit [--notes "What changed"]
                                         build + upload (listed). Creates the AMO
                                         listing on first run, adds a version after.
  python tools/release.py status         show listing + latest version review state
  python tools/release.py contrib        push contributions_url (Ko-fi) to the listing
  python tools/release.py icon           upload store-assets/icon-128.png as the listing icon
                                         (AMO never uses an SVG manifest icon; without
                                         this the listing shows a grey puzzle piece)
  python tools/release.py screenshots    upload store-assets/screenshots/*.png
                                         (waits out AMO's image throttle)

Credentials: ~/.amo-upload-key with WEB_EXT_API_KEY=... / WEB_EXT_API_SECRET=...
Metadata:    store-assets/listing.json (slug, name, summary, description, ...)

Zip entries are always written with forward slashes: Windows backslash entries
get rejected by store validators even though load-unpacked works.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

API = "https://addons.mozilla.org/api/v5"
ROOT = Path.cwd()
SRC = ROOT / "src"
DIST = ROOT / "dist"
ASSETS = ROOT / "store-assets"
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".map", ".md"}


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def manifest():
    return json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))


def listing():
    p = ASSETS / "listing.json"
    if not p.exists():
        die("store-assets/listing.json not found (run from the add-on folder)")
    return json.loads(p.read_text(encoding="utf-8"))


def guid():
    try:
        return manifest()["browser_specific_settings"]["gecko"]["id"]
    except KeyError:
        die("manifest has no browser_specific_settings.gecko.id")


# ---------------------------------------------------------------- build / lint

def build():
    m = manifest()
    slug = listing()["slug"]
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{slug}-{m['version']}.zip"
    if out.exists():
        out.unlink()
    files = sorted(
        p for p in SRC.rglob("*")
        if p.is_file() and p.name not in SKIP_NAMES and p.suffix not in SKIP_SUFFIXES
    )
    lic = ROOT / "LICENSE"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(SRC).as_posix())
        if lic.exists():
            z.write(lic, "LICENSE")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    bad = [n for n in names if "\\" in n]
    if bad:
        die("backslash entries in zip: " + ", ".join(bad))
    if "manifest.json" not in names:
        die("manifest.json is not at the zip root")
    print(f"Built {out.relative_to(ROOT)} ({len(names)} files, {out.stat().st_size // 1024} KB)")
    return out


def lint():
    out = build()
    tmp = Path(tempfile.mkdtemp(prefix="amo-lint-"))
    try:
        with zipfile.ZipFile(out) as z:
            z.extractall(tmp)
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            die("npx not found; install Node.js to lint")
        r = subprocess.run([npx, "--yes", "web-ext", "lint", "--source-dir", str(tmp)])
        if r.returncode:
            die("web-ext lint reported errors")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- API plumbing

def creds():
    p = Path.home() / ".amo-upload-key"
    if not p.exists():
        die(f"{p} not found")
    kv = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip().strip('"')
    try:
        return kv["WEB_EXT_API_KEY"], kv["WEB_EXT_API_SECRET"]
    except KeyError:
        die("~/.amo-upload-key needs WEB_EXT_API_KEY and WEB_EXT_API_SECRET")


def _b64(b):
    if isinstance(b, str):
        b = b.encode()
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token():
    key, secret = creds()
    now = int(time.time())
    # AMO rejects tokens whose iat->exp span is over 5 minutes.
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}))
    body = _b64(json.dumps({"iss": key, "jti": uuid.uuid4().hex, "iat": now - 5, "exp": now + 250}))
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def call(method, path, data=None, files=None, auth=True, wait_throttle=True):
    """JSON or multipart request. Returns (status, parsed_body)."""
    url = path if path.startswith("http") else API + path
    while True:
        headers = {"User-Agent": "addon-release-script/1.0"}
        if auth:
            headers["Authorization"] = "JWT " + token()
        body = None
        if files:
            boundary = uuid.uuid4().hex
            parts = []
            for k, v in (data or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, p in files.items():
                p = Path(p)
                ctype = "image/png" if p.suffix == ".png" else "application/zip"
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{p.name}"\r\n'
                    f"Content-Type: {ctype}\r\n\r\n".encode() + p.read_bytes() + b"\r\n"
                )
            parts.append(f"--{boundary}--\r\n".encode())
            body = b"".join(parts)
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if e.code == 429 and wait_throttle:
                m = re.search(r"available in (\d+) second", raw)
                secs = int(m.group(1)) + 5 if m else 120
                print(f"  throttled by AMO, waiting {secs}s ...")
                time.sleep(secs)
                continue
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}


def upload(zip_path):
    st, up = call("POST", "/addons/upload/", data={"channel": "listed"}, files={"upload": zip_path})
    if st not in (200, 201):
        die(f"upload failed ({st}): {up}")
    uid = up["uuid"]
    print(f"Uploaded, validating (uuid {uid}) ...")
    for _ in range(90):
        st, up = call("GET", f"/addons/upload/{uid}/")
        if up.get("processed"):
            break
        time.sleep(4)
    else:
        die("validation did not finish in time")
    v = up.get("validation") or {}
    print(f"  validation: {v.get('errors', 0)} errors, {v.get('warnings', 0)} warnings, {v.get('notices', 0)} notices")
    if not up.get("valid"):
        for msg in (v.get("messages") or [])[:20]:
            print(f"  [{msg.get('type')}] {msg.get('message')} {msg.get('file') or ''}")
        die("AMO rejected the package")
    return uid


# ---------------------------------------------------------------- commands

def flat_categories(cats):
    """AMO v5 takes one flat list of category slugs; accept the old per-app dict too."""
    if isinstance(cats, dict):
        out = []
        for slugs in cats.values():
            out += [c for c in slugs if c not in out]
        return out
    return list(cats)


def submit(notes):
    meta = listing()
    out = lint()
    uid = upload(out)
    gid = guid()
    st, _ = call("GET", f"/addons/addon/{gid}/")
    version = {"upload": uid, "license": meta.get("license", "MPL-2.0"),
               "compatibility": meta.get("compatibility", ["firefox"])}
    if notes:
        version["release_notes"] = {"en-US": notes}
    if st == 200:
        st, res = call("POST", f"/addons/addon/{gid}/versions/", data=version)
        if st == 409:
            die("this version already exists on AMO; bump manifest version")
        if st not in (200, 201):
            die(f"version create failed ({st}): {res}")
        print(f"New version {res.get('version')} submitted for review.")
    else:
        body = {
            "slug": meta["slug"],
            "name": {"en-US": meta["name"]},
            "summary": {"en-US": meta["summary"]},
            "description": {"en-US": meta["description"]},
            "categories": flat_categories(meta["categories"]),
            "version": version,
        }
        for key in ("homepage", "support_url"):
            if meta.get(key):
                body[key] = {"en-US": meta[key]}
        if meta.get("contributions_url"):
            body["contributions_url"] = meta["contributions_url"]
        st, res = call("POST", "/addons/addon/", data=body)
        if st not in (200, 201):
            die(f"listing create failed ({st}): {res}")
        print(f"Listing created: id {res.get('id')}, status {res.get('status')}")
        print(f"Public URL after review: https://addons.mozilla.org/firefox/addon/{meta['slug']}/")
        if ICON.exists():
            ok, msg = _upload_icon()
            print(("icon set: " if ok else "WARNING: icon not set, run `icon` later: ") + msg)
        else:
            print("WARNING: no store-assets/icon-128.png, the listing shows AMO's default icon")


def status():
    st, a = call("GET", f"/addons/addon/{guid()}/")
    if st != 200:
        die(f"not on AMO yet ({st})")
    name = a.get("name")
    if isinstance(name, dict):
        name = name.get("en-US")
    print(f"{name}  [{a.get('status')}]  users={a.get('average_daily_users')}  slug={a.get('slug')}")
    print(f"contributions_url: {(a.get('contributions_url') or {}).get('url') if isinstance(a.get('contributions_url'), dict) else a.get('contributions_url')}")
    st, vs = call("GET", f"/addons/addon/{guid()}/versions/?filter=all_with_unlisted")
    for v in (vs.get("results") or [])[:5]:
        f = v.get("file") or {}
        print(f"  {v.get('version'):<10} channel={v.get('channel'):<8} file={f.get('status')}")


def contrib():
    url = listing().get("contributions_url")
    if not url:
        die("listing.json has no contributions_url")
    st, res = call("PATCH", f"/addons/addon/{guid()}/", data={"contributions_url": url})
    if st != 200:
        die(f"PATCH failed ({st}): {res}")
    print("contributions_url set:", (res.get("contributions_url") or {}).get("url"))


ICON = ASSETS / "icon-128.png"


def _upload_icon():
    """PATCH the listing icon (multipart). Returns (ok, url or error)."""
    st, res = call("PATCH", f"/addons/addon/{guid()}/", files={"icon": ICON})
    if st != 200:
        return False, f"upload failed ({st}): {res}"
    url = (res.get("icons") or {}).get("128") or res.get("icon_url") or ""
    if not url or "/default-" in url:
        return False, f"AMO still shows its default icon ({url})"
    return True, url


def icon():
    if not ICON.exists():
        die("no store-assets/icon-128.png (square PNG, 128x128)")
    ok, msg = _upload_icon()
    if not ok:
        die(msg)
    print("icon set:", msg)


def _caption(pv):
    c = pv.get("caption")
    return c.get("en-US") if isinstance(c, dict) else c


def screenshots():
    shots = sorted((ASSETS / "screenshots").glob("*.png"))
    if not shots:
        die("no store-assets/screenshots/*.png")
    captions = listing().get("screenshot_captions", {})
    names = {}
    for i, p in enumerate(shots):
        st, res = call("POST", f"/addons/addon/{guid()}/previews/", data={"position": str(i)}, files={"image": p})
        if st not in (200, 201):
            die(f"{p.name}: upload failed ({st}): {res}")
        names[res["id"]] = p.name
        print(f"  uploaded {p.name} (preview id {res['id']})")
    # Captions go on after every upload, then get read back. AMO processes each image
    # in the background and has dropped a caption saved right after its upload even
    # though the PATCH answered 200 (seen on Tempo and Forte, 2026-11-09).
    for attempt in range(4):
        time.sleep(20)
        st, a = call("GET", f"/addons/addon/{guid()}/")
        missing = [(pv["id"], names[pv["id"]]) for pv in a.get("previews", [])
                   if pv["id"] in names and captions.get(names[pv["id"]])
                   and _caption(pv) != captions[names[pv["id"]]]]
        if not missing:
            print("  captions verified")
            return
        for pid, name in missing:
            cst, cres = call("PATCH", f"/addons/addon/{guid()}/previews/{pid}/",
                             data={"caption": {"en-US": captions[name]}})
            if cst != 200:
                print(f"  WARNING: caption for {name} not saved ({cst}): {cres}")
    die("captions still missing after retries: " + ", ".join(n for _, n in missing))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "lint", "submit", "status", "contrib", "icon", "screenshots"])
    ap.add_argument("--notes", default="", help="release notes for submit")
    a = ap.parse_args()
    if not (SRC / "manifest.json").exists():
        die("run this from the add-on folder (src/manifest.json not found)")
    {"build": build, "lint": lint, "status": status, "contrib": contrib, "icon": icon,
     "screenshots": screenshots, "submit": lambda: submit(a.notes)}[a.cmd]()


if __name__ == "__main__":
    main()
