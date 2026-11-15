# Tempo: Video Speed Controller

Firefox add-on (desktop + Android) that controls the playback speed of any HTML5
video or audio. Free, every feature unlocked, no ads, no tracking, optional Ko-fi tip.

Why this one first: the long-standing "Video Speed Controller" listing on AMO
still has ~105k daily users but hasn't been updated since April 2021. Tempo is
a maintained, modern alternative built on stable web APIs (no site-specific
scraping), so it should need very little upkeep.

## Features

- 0.07x to 16x, configurable step, presets in the popup
- Shortcuts on every site (S / D / R / G / Z / X / V, all rebindable); ignored while typing
- Small on-video control (always faint / flash on change / hidden), tap-to-open on Android
- Per-site or global speed memory; one-click "off on this site"
- Re-applies speed when a site resets it on the next video, but adopts a speed
  you pick in the site's own player menu (optional hard lock)
- Works in iframes (keys pressed in the page are relayed to the embedded player)
  and shadow-DOM players
- Leaves live streams at 1x (a remembered 1.5x would race to the live edge and
  stall) until you pick a speed for that stream. Detected by duration (Infinity,
  or Twitch's ~9.2e12 s) or, on YouTube, the player's own `getVideoData().isLive`
- Toolbar badge with the tab's speed; time-saved counter
- Injects into already-open tabs on install, so no reload needed

## Layout

```
src/                     everything that ships
  manifest.json
  background.js          site memory, broadcasting, badge, stats (event page, ES module)
  content/tempo.js       per-frame player detection, shortcuts, overlay (classic script)
  popup/                 toolbar popup (?tab=<id> opens it against a tab, for tests)
  options/               settings page
  shared/settings.js     defaults + helpers for background/popup/options
  shared/support.*       Ko-fi pill + milestone thank-you card (from the template)
  shared/more-addons.*   "More free add-ons" card + rate link (same file in every add-on; set `live: true` as listings are approved)
store-assets/listing.json  AMO listing text, categories, Ko-fi contributions_url
store-assets/screenshots/  1280x800 listing images (made by tests/screenshots.py)
tests/                   real-Firefox E2E + screenshot generator (not shipped)
tools/release.py         build / lint / submit / status / contrib / screenshots
```

## Develop and test

```powershell
python tools/release.py lint                 # build + web-ext lint the zip (expect 0/0/0)
python -m pip install selenium pillow        # once, for the tests
python tests/e2e_firefox.py                  # 38 checks in headless Firefox
python tests/screenshots.py                  # regenerate store screenshots
```

Manual: `about:debugging#/runtime/this-firefox` → Load Temporary Add-on → `src/manifest.json`.

## Release

```powershell
python tools/release.py submit --notes "First release"
python tools/release.py screenshots          # after the listing exists
python tools/release.py status
```

Bump `version` in `src/manifest.json` for every later submit.
