"""Capture AMO listing screenshots (1280x800) of Tempo in a real Firefox.

  python tools/release.py build
  python tests/screenshots.py          (needs selenium + pillow)

Writes store-assets/screenshots/{1-popup,2-overlay,3-options}.png. The demo
profile is seeded with example per-site speeds and a time-saved total so the
UI isn't empty; nothing here ships in the add-on.
"""
import functools
import http.server
import json
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options

ROOT = Path(__file__).resolve().parents[1]
ADDON_ID = "tempo-video-speed@c4rcer"
UUID = "6f1c2c1e-7e3a-4b8a-9d55-7e3a00c0ffee"
PORT = 8932
DEMO_HOST = "lectures.example.com"  # mapped to 127.0.0.1 via network.dns.localDomains
BASE = f"http://{DEMO_HOST}:{PORT}/test-page.html"
EXT = f"moz-extension://{UUID}"
OUT = ROOT / "store-assets" / "screenshots"
W, H = 1280, 800
DPR = 1.6  # render at 1.6x so a 800x500 CSS viewport fills 1280x800 crisply

SEED = """
await browser.storage.local.set({
  sites: {"youtube.com": 1.75, "coursera.org": 2, "twitch.tv": 1.25, "udemy.com": 1.5, "lectures.example.com": 1.75},
  stats: {watched: 61200, saved: 19260, since: new Date(Date.now() - 41 * 864e5).toISOString()},
  supportNudge: {seen: [1, 10], optOut: false},
});
return true;
"""


def fit(img):
    """Crop/pad a capture to exactly 1280x800."""
    canvas = Image.new("RGB", (W, H), (15, 13, 22))
    canvas.paste(img.crop((0, 0, min(W, img.width), min(H, img.height))), (0, 0))
    return canvas


def drop_shadow(card, radius=12):
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.width - 1, card.height - 1), radius, fill=255)
    pad = 30
    shadow = Image.new("RGBA", (card.width + pad * 2, card.height + pad * 2), (0, 0, 0, 0))
    sm = Image.new("L", shadow.size, 0)
    ImageDraw.Draw(sm).rounded_rectangle((pad, pad + 6, pad + card.width, pad + card.height + 6), radius, fill=150)
    shadow.putalpha(sm.filter(ImageFilter.GaussianBlur(14)))
    shadow.paste(card, (pad, pad), mask)
    return shadow


def main():
    zips = sorted((ROOT / "dist").glob("*.zip"), key=lambda p: p.stat().st_mtime)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "tests"))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    opts = Options()
    opts.binary_location = r"C:\Program Files\Mozilla Firefox\firefox.exe"
    opts.add_argument("-headless")
    opts.set_preference("extensions.webextensions.uuids", json.dumps({ADDON_ID: UUID}))
    opts.set_preference("media.autoplay.default", 0)
    opts.set_preference("ui.systemUsesDarkTheme", 1)
    opts.set_preference("layout.css.devPixelsPerPx", str(DPR))
    opts.set_preference("network.dns.localDomains", DEMO_HOST)
    opts.set_preference("layout.css.prefers-color-scheme.content-override", 0)
    driver = webdriver.Firefox(options=opts)
    unpacked = Path(tempfile.mkdtemp(prefix="tempo-shots-"))
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zips[-1]) as z:
            z.extractall(unpacked)
        driver.execute("INSTALL_ADDON", {"path": str(unpacked), "temporary": True})
        time.sleep(1)
        cw, ch = int(W / DPR), int(H / DPR)
        driver.set_window_size(cw, ch)
        inner = driver.execute_script("return [innerWidth, innerHeight]")
        driver.set_window_size(cw + (cw - inner[0]), ch + (ch - inner[1]))

        # Seed demo state from an extension page.
        driver.get(f"{EXT}/options/options.html")
        driver.execute_async_script(
            "const done = arguments[arguments.length - 1];"
            f"(async () => {{ {SEED} }})().then(done, (e) => done(String(e)));")

        # Page with a playing video at 1.75x.
        page = driver.current_window_handle
        driver.get(BASE)
        driver.execute_async_script("const d = arguments[arguments.length - 1]; window.ready.then(() => d(1));")
        driver.execute_script("""
          document.getElementById('extras').style.display = 'none';
          document.querySelector('.meta').textContent = 'Tip: press D to go faster, S to slow down, R to reset.';
          const v = document.getElementById('main'); v.loop = true; v.muted = true; v.play();
        """)
        time.sleep(2.5)

        # 2: overlay, hovered so its buttons show.
        rect = driver.execute_script(
            "const r = document.getElementById('main').getBoundingClientRect(); return [r.width, r.height];")
        ActionChains(driver).move_to_element_with_offset(
            driver.find_element(By.ID, "main"), int(-rect[0] / 2 + 60), int(-rect[1] / 2 + 22)).perform()
        time.sleep(0.6)
        driver.save_screenshot(str(OUT / "2-overlay.png"))
        fit(Image.open(OUT / "2-overlay.png")).save(OUT / "2-overlay.png")

        # Base page without hover for the popup composite.
        ActionChains(driver).move_by_offset(300, 250).perform()
        time.sleep(1.8)
        base_path = OUT / "_base.png"
        driver.save_screenshot(str(base_path))

        # 1: popup, rendered in a tab against the video tab, composited top-right.
        driver.switch_to.new_window("tab")
        driver.get(f"{EXT}/popup/popup.html")
        tab_id = driver.execute_async_script(
            "const done = arguments[arguments.length - 1];"
            f"browser.tabs.query({{}}).then((t) => done((t.find((x) => x.url.startsWith('{BASE}')) || {{}}).id));")
        driver.get(f"{EXT}/popup/popup.html?tab={tab_id}")
        time.sleep(1.2)
        pop_path = OUT / "_popup.png"
        driver.find_element(By.TAG_NAME, "body").screenshot(str(pop_path))
        base = fit(Image.open(base_path)).convert("RGBA")
        dim = Image.new("RGBA", base.size, (0, 0, 0, 70))
        base = Image.alpha_composite(base, dim)
        card = drop_shadow(Image.open(pop_path).convert("RGBA"))
        base.alpha_composite(card, (W - card.width - 6, -14))
        base.convert("RGB").save(OUT / "1-popup.png")

        # 3: options, composed from its cards side by side.
        driver.get(f"{EXT}/options/options.html")
        time.sleep(1.0)
        driver.execute_script(
            "const st = document.createElement('style');"
            "st.textContent = '.page { max-width: 384px; padding: 0 } .card { margin: 0 }';"
            "document.head.append(st);")
        time.sleep(0.3)

        def card(el_id, name):
            el = driver.find_element(By.ID, el_id).find_element(By.XPATH, "./ancestor::section")
            path = OUT / f"_{name}.png"
            el.screenshot(str(path))
            return Image.open(path).convert("RGB")

        keys_img, sites_img, stats_img = card("keys", "keys"), card("sites", "sites"), card("stats", "stats")
        bg = driver.execute_script("return getComputedStyle(document.body).backgroundColor")
        rgb = tuple(int(x) for x in bg[bg.index("(") + 1:bg.index(")")].split(",")[:3])
        canvas = Image.new("RGB", (W, H), rgb)
        gap = 24
        # Shrink evenly so both columns fit inside 1280x800 with margins.
        tall = max(keys_img.height, sites_img.height + gap + stats_img.height)
        wide = keys_img.width + sites_img.width + gap
        k = min(1.0, (H - 2 * gap) / tall, (W - 2 * gap) / wide)
        if k < 1:
            keys_img, sites_img, stats_img = (
                im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
                for im in (keys_img, sites_img, stats_img))
        right_h = sites_img.height + gap + stats_img.height
        top_l = max(16, (H - keys_img.height) // 2)
        top_r = max(16, (H - right_h) // 2)
        x_l = (W - keys_img.width - sites_img.width - gap) // 2
        x_r = x_l + keys_img.width + gap
        canvas.paste(keys_img, (x_l, top_l))
        canvas.paste(sites_img, (x_r, top_r))
        canvas.paste(stats_img, (x_r, top_r + sites_img.height + gap))
        canvas.save(OUT / "3-options.png")
        driver.switch_to.window(page)
    finally:
        driver.quit()
        httpd.shutdown()
        shutil.rmtree(unpacked, ignore_errors=True)
        for tmp in OUT.glob("_*.png"):
            tmp.unlink()
    for p in sorted(OUT.glob("*.png")):
        print(p.relative_to(ROOT), Image.open(p).size)


if __name__ == "__main__":
    main()
