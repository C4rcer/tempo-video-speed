"""End-to-end test of Tempo in a real Firefox (Selenium + geckodriver).

  python -m pip install selenium        (once, ideally in a venv)
  python tools/release.py build
  python tests/e2e_firefox.py [--headed]

Loads the built zip as a temporary add-on in a throwaway profile, serves
tests/test-page.html on 127.0.0.1:8931 and drives keys, popup and options.
"""
import functools
import http.server
import json
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options

ROOT = Path(__file__).resolve().parents[1]
ADDON_ID = "tempo-video-speed@c4rcer"
UUID = "6f1c2c1e-7e3a-4b8a-9d55-7e3a00c0ffee"
PORT = 8931
BASE = f"http://127.0.0.1:{PORT}/test-page.html"
EXT = f"moz-extension://{UUID}"
HEADED = "--headed" in sys.argv

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def wait(fn, timeout=10.0, step=0.1):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(step)
    return last


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "tests"))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    zips = sorted((ROOT / "dist").glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        sys.exit("build first: python tools/release.py build")
    httpd = serve()

    opts = Options()
    opts.binary_location = r"C:\Program Files\Mozilla Firefox\firefox.exe"
    if not HEADED:
        opts.add_argument("-headless")
    opts.set_preference("extensions.webextensions.uuids", json.dumps({ADDON_ID: UUID}))
    opts.set_preference("media.autoplay.default", 0)
    opts.set_preference("media.autoplay.blocking_policy", 0)
    opts.set_preference("media.volume_scale", "0.0")  # muted: the test page plays a tone, nothing should be audible
    driver = webdriver.Firefox(options=opts)
    driver.set_window_size(1280, 900)
    js = driver.execute_script

    def ext_call(code):
        """Run async code with the WebExtension API inside an extension page."""
        return driver.execute_async_script(
            "const done = arguments[arguments.length - 1];"
            f"(async () => {{ {code} }})().then(done, (e) => done('ERR ' + e));"
        )

    unpacked = None
    try:
        # Install the *built zip*, extracted, by path. Handing geckodriver the zip
        # itself installs from a temp copy it deletes, so content scripts then
        # fail to load ("Unable to load script").
        unpacked = Path(tempfile.mkdtemp(prefix="tempo-e2e-"))
        with zipfile.ZipFile(zips[-1]) as z:
            z.extractall(unpacked)
        driver.execute("INSTALL_ADDON", {"path": str(unpacked), "temporary": True})
        time.sleep(1.0)

        def load(url=BASE):
            driver.get(url)
            driver.execute_async_script(
                "const d = arguments[arguments.length - 1]; window.ready.then(() => d(true));")
            js("""
              const vids = [document.getElementById('main'),
                            document.getElementById('shadowHost')?.shadowRoot?.getElementById('shadowVideo'),
                            document.getElementById('frame')?.contentDocument?.getElementById('main')];
              for (const v of vids) if (v) { v.loop = true; v.play(); }
            """)

        main_rate = lambda: js("return document.getElementById('main').playbackRate")  # noqa: E731
        shadow_rate = lambda: js(  # noqa: E731
            "return document.getElementById('shadowHost').shadowRoot.getElementById('shadowVideo').playbackRate")
        frame_rate = lambda: js(  # noqa: E731
            "return document.getElementById('frame').contentDocument.getElementById('main').playbackRate")
        keys = lambda s: ActionChains(driver).send_keys(s).perform()  # noqa: E731

        def focus_page():
            js("document.activeElement && document.activeElement.blur(); window.focus();")
            driver.find_element(By.ID, "title").click()

        # ---- basic control
        load()
        check("starts at 1x", abs(main_rate() - 1) < 0.001, main_rate())
        check("overlay injected on main video",
              wait(lambda: js("return document.querySelectorAll('[data-tempo-overlay]').length") >= 1))
        focus_page()
        keys("ddddd")
        check("D x5 -> 1.5x", wait(lambda: abs(main_rate() - 1.5) < 0.001), main_rate())
        check("shadow-DOM player follows", wait(lambda: abs(shadow_rate() - 1.5) < 0.001), shadow_rate())
        check("iframe player follows (broadcast)", wait(lambda: abs(frame_rate() - 1.5) < 0.001), frame_rate())
        keys("ss")
        check("S x2 -> 1.3x", wait(lambda: abs(main_rate() - 1.3) < 0.001), main_rate())
        keys("dd")

        # ---- typing in a text box must not trigger shortcuts
        js("const i = document.createElement('input'); i.id = 'box'; document.querySelector('.wrap').prepend(i); i.focus();")
        keys("ddd")
        check("keys ignored while typing", abs(main_rate() - 1.5) < 0.001, main_rate())
        js("document.getElementById('box').remove()")

        # ---- per-site memory survives reload
        load()
        check("site speed remembered after reload", wait(lambda: abs(main_rate() - 1.5) < 0.001), main_rate())

        # ---- site resets speed while loading the next video -> Tempo re-applies
        js("const v = document.getElementById('main'); const s = v.src; v.src = s; v.playbackRate = 1; v.play();")
        time.sleep(1.0)
        check("site reset to 1x on new video is undone", abs(main_rate() - 1.5) < 0.001, main_rate())

        # ---- user picks a speed in the site's own menu after playback -> adopted
        time.sleep(3.0)
        js("document.getElementById('main').playbackRate = 1.25")
        time.sleep(0.8)
        check("site-menu speed adopted", abs(main_rate() - 1.25) < 0.001, main_rate())
        check("adopted speed broadcast to iframe", wait(lambda: abs(frame_rate() - 1.25) < 0.001), frame_rate())

        # ---- reset toggle and favourite
        focus_page()
        keys("r")
        check("R -> 1x", wait(lambda: abs(main_rate() - 1) < 0.001), main_rate())
        keys("r")
        check("R again -> back to 1.25x", wait(lambda: abs(main_rate() - 1.25) < 0.001), main_rate())
        keys("g")
        check("G -> favourite 1.8x", wait(lambda: abs(main_rate() - 1.8) < 0.001), main_rate())
        keys("g")
        check("G again -> 1x", wait(lambda: abs(main_rate() - 1) < 0.001), main_rate())
        keys("ddddd")
        wait(lambda: abs(main_rate() - 1.5) < 0.001)

        # ---- seek keys
        t0 = js("return document.getElementById('main').currentTime")
        keys("z")
        t1 = js("return document.getElementById('main').currentTime")
        check("Z rewinds", t1 < t0 or t1 < 0.5, f"{t0:.2f} -> {t1:.2f}")

        # ---- overlay buttons don't pause the player and change speed
        js("document.getElementById('main').play()")
        time.sleep(0.3)

        # ---- popup (opened as a tab with ?tab=<id>)
        page_handle = driver.current_window_handle
        driver.switch_to.new_window("tab")
        driver.get(f"{EXT}/popup/popup.html")
        tab_id = ext_call(f"const t = (await browser.tabs.query({{}})).find((x) => x.url.startsWith('{BASE}')); return t ? t.id : null;")
        check("extension API reachable from test harness", isinstance(tab_id, int), tab_id)
        perms = ext_call("return await browser.permissions.contains({origins: ['<all_urls>']});")
        check("host permission granted at install", perms is True, perms)
        driver.get(f"{EXT}/popup/popup.html?tab={tab_id}")
        check("popup shows current speed", wait(lambda: driver.find_element(By.ID, "rate").text == "1.5\u00d7"),
              driver.find_element(By.ID, "rate").text)
        info = wait(lambda: driver.find_element(By.ID, "mediaInfo").text)
        check("popup sees the page's media", "Controlling" in (info or ""), info)
        driver.find_element(By.CSS_SELECTOR, '#presets button[data-rate="2"]').click()
        driver.switch_to.window(page_handle)
        check("popup preset 2x applied to page", wait(lambda: abs(main_rate() - 2) < 0.001), main_rate())
        check("popup preset reaches iframe", wait(lambda: abs(frame_rate() - 2) < 0.001), frame_rate())

        # ---- site toggle off -> speed back to 1x and control removed
        driver.switch_to.window(driver.window_handles[-1])
        driver.get(f"{EXT}/popup/popup.html?tab={tab_id}")
        wait(lambda: driver.find_element(By.ID, "siteName").text == "127.0.0.1")
        driver.find_element(By.ID, "siteEnabled").click()
        driver.switch_to.window(page_handle)
        check("disabling site restores 1x", wait(lambda: abs(main_rate() - 1) < 0.001), main_rate())
        check("disabling site removes control",
              wait(lambda: js("return document.querySelectorAll('[data-tempo-overlay]').length") == 0))
        driver.switch_to.window(driver.window_handles[-1])
        driver.find_element(By.ID, "siteEnabled").click()
        driver.switch_to.window(page_handle)
        check("re-enabling restores remembered 2x", wait(lambda: abs(main_rate() - 2) < 0.001), main_rate())

        # ---- options: rebind "faster" to F
        driver.switch_to.window(driver.window_handles[-1])
        driver.get(f"{EXT}/options/options.html")
        wait(lambda: driver.find_elements(By.CSS_SELECTOR, ".key-btn"))
        sites_text = wait(lambda: driver.find_element(By.ID, "sites").text)
        check("options lists remembered site", "127.0.0.1" in (sites_text or ""), sites_text)
        # ---- settings: rate link and "More free add-ons" (expected list comes from the shared file)
        more = wait(lambda: ext_call("""
          const items = [...document.querySelectorAll('#moreAddons .more-item a')];
          if (!items.length) return null;
          const rate = document.querySelector('[data-rate]');
          const all = document.querySelector('[data-more-all]');
          const m = await import('/shared/more-addons.js');
          const me = browser.runtime.id;
          const self = m.ADDONS.find((a) => a.id === me);
          const fake = [{id: 'a', live: true, android: false}, {id: 'b', live: true, android: true},
                        {id: 'self', live: true, android: true}, {id: 'c', live: false, android: true}];
          return {
            names: items.map((a) => a.querySelector('.more-name').textContent),
            expected: m.ADDONS.filter((a) => a.live && a.id !== me).map((a) => a.name),
            left_out: m.ADDONS.filter((a) => !a.live || a.id === me).map((a) => a.name),
            self_slug: self ? self.slug : null, self_live: self ? self.live : null,
            hrefs: items.map((a) => a.href),
            newTab: items.every((a) => a.target === '_blank' && a.rel === 'noopener'),
            rate: rate.hidden ? null : rate.href, rateNewTab: rate.target === '_blank',
            all: all.href,
            androidPick: m.moreAddons('self', 'android', fake).map((x) => x.id),
            desktopPick: m.moreAddons('self', 'win', fake).map((x) => x.id),
          };
        """))
        more = more if isinstance(more, dict) else {}
        check("this add-on is marked live in the shared add-on list", more.get("self_live") is True, more.get("self_slug"))
        check("settings page lists every other live add-on, in order",
              bool(more.get("names")) and more.get("names") == more.get("expected"), (more.get("names"), more.get("expected")))
        check("it leaves out itself and add-ons still in review",
              bool(more.get("names")) and not set(more["names"]) & set(more.get("left_out") or []), more.get("left_out"))
        check("each listed add-on links to its AMO page in a new tab",
              bool(more.get("hrefs")) and all(h.startswith("https://addons.mozilla.org/firefox/addon/") for h in more["hrefs"])
              and more.get("newTab") is True, more.get("hrefs"))
        check("rate link opens this add-on's own AMO page",
              bool(more.get("self_slug")) and more.get("rate") == "https://addons.mozilla.org/firefox/addon/%s/" % more.get("self_slug")
              and more.get("rateNewTab") is True, more.get("rate"))
        check("see-all link opens the author's AMO page",
              more.get("all") == "https://addons.mozilla.org/firefox/user/20013808/", more.get("all"))
        check("list skips itself and unreleased add-ons, and desktop-only ones on Android",
              more.get("desktopPick") == ["a", "b"] and more.get("androidPick") == ["b"],
              (more.get("desktopPick"), more.get("androidPick")))
        driver.find_elements(By.CSS_SELECTOR, ".key-btn")[1].click()
        ActionChains(driver).send_keys("f").perform()
        stored = wait(lambda: ext_call("const {settings} = await browser.storage.local.get('settings'); return settings && settings.keys.faster;") == "f")
        check("rebinding saved", stored is True)
        driver.switch_to.window(page_handle)
        focus_page()
        keys("f")
        check("new key F works", wait(lambda: abs(main_rate() - 2.1) < 0.001), main_rate())
        keys("d")
        time.sleep(0.4)
        check("old key D no longer bound", abs(main_rate() - 2.1) < 0.001, main_rate())

        # ---- key relay into an iframe-only page
        load(BASE + "?relay=1")
        before = frame_rate()
        focus_page()
        keys("s")
        check("key in top page reaches iframe player", wait(lambda: abs(frame_rate() - (before - 0.1)) < 0.001),
              f"{before} -> {frame_rate()}")
        remembered = round(before - 0.1, 2)

        # ---- live streams stay at 1x (a remembered speed would stall them)
        load(BASE + "?live=stream")
        time.sleep(1.5)
        check("live stream stays at 1x", abs(main_rate() - 1) < 0.001, main_rate())
        driver.switch_to.window(driver.window_handles[-1])
        driver.get(f"{EXT}/popup/popup.html?tab={tab_id}")
        info = wait(lambda: driver.find_element(By.ID, "mediaInfo").text)
        check("popup explains the live hold", "Live stream" in (info or ""), info)
        check("popup shows the live stream's real speed", driver.find_element(By.ID, "rate").text == "1×",
              driver.find_element(By.ID, "rate").text)
        driver.switch_to.window(page_handle)
        js("const v = document.getElementById('main'); v.srcObject = null; v.src = window.vodUrl; v.play();")
        check("next regular video gets the remembered speed",
              wait(lambda: abs(main_rate() - remembered) < 0.001), f"{main_rate()} vs {remembered}")
        js("const v = document.getElementById('main'); v.removeAttribute('src'); v.srcObject = window.liveStream; v.play();")
        check("switching back to live returns to 1x", wait(lambda: abs(main_rate() - 1) < 0.001), main_rate())
        # (Firefox ignores playbackRate on MediaStream sources, so the user
        # override is checked on the YouTube-style live video, a real file.)
        load(BASE + "?live=yt")
        time.sleep(1.5)
        check("YouTube-style live (player reports isLive) stays at 1x", abs(main_rate() - 1) < 0.001, main_rate())
        focus_page()
        keys("f")
        check("faster on a live stream steps up from 1x, not the remembered speed",
              wait(lambda: abs(main_rate() - 1.1) < 0.001), main_rate())

        # ---- stats flush (page hidden -> flush)
        load()
        time.sleep(4)
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(1.0)
        stats = wait(lambda: ext_call("const {stats} = await browser.storage.local.get('stats'); return stats && stats.saved > 0 ? stats : null;"), 8)
        check("time-saved stats recorded", isinstance(stats, dict), stats)
    finally:
        failed = [n for n, ok in results if not ok]
        print(f"\n{len(results) - len(failed)}/{len(results)} passed")
        if failed:
            print("FAILED: " + ", ".join(failed))
        driver.quit()
        httpd.shutdown()
        if unpacked:
            shutil.rmtree(unpacked, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
