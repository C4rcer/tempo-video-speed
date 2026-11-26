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
from selenium.webdriver.firefox.service import Service

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


def check_milestone_card(api):
    """The shared thank-you card, driven directly on the extension page that is open now:
    the first card a user ever gets asks for a rating, later ones for a tip. Leaves storage as it found it."""
    out = api("""
      const m = await import('/shared/support.js');
      const lists = await import('/shared/more-addons.js');
      const self = lists.ADDONS.find((a) => a.id === browser.runtime.id);
      const KEY = 'supportNudge';
      const saved = (await browser.storage.local.get(KEY))[KEY];
      const box = document.createElement('div');
      document.body.append(box);
      box.addEventListener('click', (e) => e.preventDefault(), true);  // a test click must not open AMO or Ko-fi
      const fact = (n) => `Fact ${n}.`;
      const stored = async () => (await browser.storage.local.get(KEY))[KEY];
      const settle = async (pred) => {
        for (let i = 0; i < 60; i++) {
          const s = await stored();
          if (s && pred(s)) return s;
          await new Promise((r) => setTimeout(r, 50));
        }
        return (await stored()) || null;
      };
      const snap = () => {
        const rate = box.querySelector('a[data-rate-ask]');
        const tip = box.querySelector('a[data-support]');
        return {
          text: box.querySelector('p') ? box.querySelector('p').textContent : '',
          rate: rate ? rate.href : null, rateLabel: rate ? rate.textContent.trim() : null,
          rateNewTab: rate ? rate.target === '_blank' && rate.rel === 'noopener' : null,
          tip: tip ? tip.href : null,
          buttons: [...box.querySelectorAll('button')].map((b) => b.textContent),
        };
      };
      const out = { slug: self ? self.slug : null, name: self ? self.name : null };
      try {
        await browser.storage.local.remove(KEY);
        out.early = await m.maybeShowMilestone(box, 4, [5, 50], fact);
        out.shown1 = await m.maybeShowMilestone(box, 5, [5, 50], fact);
        out.first = snap();
        box.querySelectorAll('button')[0].click();  // Not now
        out.after1 = await settle((s) => (s.seen || []).includes(5));
        out.hidden1 = box.hidden;
        out.again = await m.maybeShowMilestone(box, 5, [5, 50], fact);
        out.shown2 = await m.maybeShowMilestone(box, 50, [5, 50], fact);
        out.second = snap();
        box.querySelectorAll('button')[1].click();  // Don't ask again
        out.after2 = await settle((s) => s.optOut === true);
        out.afterOptOut = await m.maybeShowMilestone(box, 500, [5, 50, 500], fact);
        // Someone who saw a tip card under an older version: no rating ask recorded yet.
        await browser.storage.local.set({ [KEY]: { seen: [5], optOut: false } });
        out.shown3 = await m.maybeShowMilestone(box, 50, [5, 50], fact);
        out.third = snap();
        const link = box.querySelector('a[data-rate-ask]');
        if (link) link.click();
        out.after3 = await settle((s) => s.rateAsked === true && (s.seen || []).includes(50));
      } finally {
        if (saved === undefined) await browser.storage.local.remove(KEY);
        else await browser.storage.local.set({ [KEY]: saved });
        box.remove();
      }
      return out;
    """)
    out = out if isinstance(out, dict) else {"error": out}
    amo = "https://addons.mozilla.org/firefox/addon/%s/" % out.get("slug")
    first, second, third = (out.get(k) or {} for k in ("first", "second", "third"))
    check("thank-you card: nothing before the first milestone", out.get("early") is False, out.get("error") or out.get("early"))
    check("thank-you card: the first one a user ever gets asks for a rating, not a tip",
          out.get("shown1") is True and bool(out.get("slug")) and first.get("rate") == amo and first.get("rateNewTab") is True
          and first.get("tip") is None and first.get("rateLabel") == "\u2605 Rate %s" % out.get("name")
          and first.get("text") == "Fact 5. It's free, with no ads and no tracking. If it's useful to you, a rating on Firefox Add-ons helps other people find it."
          and first.get("buttons") == ["Not now", "Don't ask again"], first)
    check("thank-you card: 'Not now' closes it and that milestone never comes back",
          out.get("hidden1") is True and 5 in ((out.get("after1") or {}).get("seen") or [])
          and (out.get("after1") or {}).get("rateAsked") is True and out.get("again") is False, (out.get("after1"), out.get("again")))
    check("thank-you card: the next milestone asks for a tip on Ko-fi",
          out.get("shown2") is True and second.get("tip") == "https://ko-fi.com/carcer7378" and second.get("rate") is None
          and second.get("text") == "Fact 50. It's free, with no ads and no tracking. If that's worth a coffee, tips keep it maintained.", second)
    check("thank-you card: 'Don't ask again' is final",
          (out.get("after2") or {}).get("optOut") is True and out.get("afterOptOut") is False, (out.get("after2"), out.get("afterOptOut")))
    check("thank-you card: someone who saw a tip card under an older version is asked to rate once",
          out.get("shown3") is True and third.get("rate") == amo and third.get("tip") is None
          and (out.get("after3") or {}).get("rateAsked") is True and 50 in ((out.get("after3") or {}).get("seen") or []), (third, out.get("after3")))


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
    driver = webdriver.Firefox(options=opts, service=Service(service_args=["--allow-system-access"]))
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

        def background_state():
            with driver.context("chrome"):
                return driver.execute_script(
                    "return WebExtensionPolicy.getByID(arguments[0]).extension.backgroundState;", ADDON_ID)

        def stop_background():
            """What Firefox does after 30 s without work: stop the background script."""
            with driver.context("chrome"):
                driver.execute_async_script(
                    "const done = arguments[arguments.length - 1];"
                    "WebExtensionPolicy.getByID(arguments[0]).extension.terminateBackground()"
                    ".then(() => done(true), (e) => done('ERR ' + e));", ADDON_ID)
            return wait(lambda: background_state() == "stopped", 5) is True

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
        check_milestone_card(ext_call)
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
        # Firefox stops the idle background script after 30 s. The relay must not
        # depend on anything that script remembered.
        check("background script stopped, as Firefox does when it is idle", stop_background(), background_state())
        focus_page()
        keys("s")
        check("key in top page still reaches the iframe player after the background script was stopped",
              wait(lambda: abs(frame_rate() - (before - 0.2)) < 0.001), f"{before} -> {frame_rate()}")
        driver.switch_to.window(driver.window_handles[-1])
        want_badge = ("%.2f" % (before - 0.2)).rstrip("0").rstrip(".")
        badge = wait(lambda: ext_call(f"return await browser.action.getBadgeText({{tabId: {tab_id}}});") == want_badge, 4)
        check("and the badge still shows that tab's speed", badge is True,
              ext_call(f"return await browser.action.getBadgeText({{tabId: {tab_id}}});"))
        driver.switch_to.window(page_handle)

        # ---- a site's own single-key shortcuts: a page with no player, only <audio> elements
        load(BASE + "?keys=1")
        driver.switch_to.window(driver.window_handles[-1])
        bound = ext_call("const {settings} = await browser.storage.local.get('settings'); return Object.values(settings.keys);")
        driver.switch_to.window(page_handle)

        def stored_site_rate():
            driver.switch_to.window(driver.window_handles[-1])
            try:
                return ext_call("const {sites = {}} = await browser.storage.local.get('sites'); return sites['127.0.0.1'] ?? 1;")
            finally:
                driver.switch_to.window(page_handle)

        def press(chars):
            js("window.seen.length = 0;")
            focus_page()
            for k in chars:
                keys(k)
            time.sleep(0.4)
            return js("return window.seen")

        rate_before = stored_site_rate()
        seen = press(bound)
        check("an audio element that never played leaves the page all of its keys",
              len(bound) == 7 and seen == bound, (bound, seen))
        js("const a = document.getElementById('ping'); a.src = window.makeWav(1); a.play();")
        wait(lambda: js("return document.getElementById('ping').ended"), 6)
        seen = press(bound)
        check("a short sound that has played (a notification ping) still leaves the page its keys",
              js("return document.getElementById('ping').ended") is True and seen == bound, seen)
        check("and none of those key presses changed the speed remembered for the site",
              stored_site_rate() == rate_before, (rate_before, stored_site_rate()))
        pod_rate = lambda: js("return document.getElementById('podcast').playbackRate")  # noqa: E731
        js("const a = document.getElementById('podcast'); a.src = window.makeWav(40); a.play();")
        wait(lambda: js("return document.getElementById('podcast').currentTime") > 0.3, 6)
        r0 = pod_rate()
        seen = press("s")
        check("long audio that is playing gets the key, and the page does not",
              wait(lambda: abs(pod_rate() - (r0 - 0.1)) < 0.001) is True and seen == [], (r0, pod_rate(), seen))
        js("document.getElementById('podcast').pause();")
        time.sleep(0.3)
        seen = press("s")
        check("paused long audio that has played still gets the key",
              wait(lambda: abs(pod_rate() - (r0 - 0.2)) < 0.001) is True and seen == [], (r0, pod_rate(), seen))
        remembered = round(r0 - 0.2, 2)

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
