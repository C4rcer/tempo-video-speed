// The portfolio's other free add-ons, for the "More free add-ons" card and the
// "Rate on Firefox Add-ons" link in each add-on's settings page. This file is
// identical in every add-on; the current one is found by its gecko id.
//
// `live` means the listing is public on AMO. A listing still in review answers
// "not found" to visitors, so it stays hidden until a later version flips it
// on. The author page link shows newly approved add-ons in the meantime.
// `android` false hides desktop-only add-ons on Firefox for Android.

export const AMO_ADDON = "https://addons.mozilla.org/firefox/addon/";
export const AUTHOR_URL = "https://addons.mozilla.org/firefox/user/20013808/";

export const ADDONS = [
  { id: "tempo-video-speed@c4rcer", slug: "tempo-video-speed-controller", name: "Tempo",
    what: "Speed up or slow down any video, with shortcuts and per-site memory", android: true, live: true },
  { id: "forte-volume-booster@c4rcer", slug: "forte-volume-booster-equalizer", name: "Forte",
    what: "Make any site louder, up to 600%, with a 10-band equalizer", android: true, live: true },
  { id: "youtube-blocker@c4rcer", slug: "youtube-twitch-enhancer", name: "YouTube/Twitch Enhancer",
    what: "Block channels, remove Shorts, chat overlay and more for YouTube and Twitch", android: true, live: true },
  { id: "prune-bookmark-cleaner@c4rcer", slug: "prune-bookmark-cleaner", name: "Prune",
    what: "Find dead links and duplicate bookmarks, and clean up with undo", android: false, live: true },
  { id: "pipette-color-picker@c4rcer", slug: "pipette-color-picker", name: "Pipette",
    what: "Pick any color on a page and copy it as HEX, RGB or HSL", android: false, live: true },
  { id: "excerpt-markdown-clipper@c4rcer", slug: "excerpt-markdown-web-clipper", name: "Excerpt",
    what: "Clip an article or a selection to clean Markdown", android: false, live: true },
  { id: "heron-website-change-monitor@c4rcer", slug: "heron-website-change-monitor", name: "Heron",
    what: "Get told when a web page, or one part of it, changes", android: false, live: true },
  { id: "dustpan-cookie-cleaner@c4rcer", slug: "dustpan-cookie-cleaner", name: "Dustpan",
    what: "Delete a site's cookies after you close its last tab, except the sites you keep", android: false, live: true },
];

export function amoUrl(addon) {
  return AMO_ADDON + addon.slug + "/";
}

/** Which add-ons to recommend from the one with `selfId`, on platform `os`. */
export function moreAddons(selfId, os, list = ADDONS) {
  return list.filter((a) => a.live && a.id !== selfId && (os !== "android" || a.android));
}

function link(href, className) {
  const a = document.createElement("a");
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  if (className) a.className = className;
  return a;
}

/**
 * Fill the list in `container` (its [data-more-list] element) and point every
 * [data-rate] link at this add-on's AMO page. Built with DOM calls only.
 */
export async function renderMoreAddons(container, root = document) {
  const selfId = browser.runtime.id;
  let os = "";
  try {
    os = (await browser.runtime.getPlatformInfo()).os;
  } catch {
    // Unknown platform: show the full list.
  }
  const self = ADDONS.find((a) => a.id === selfId);
  for (const rate of root.querySelectorAll("[data-rate]")) {
    if (!self) {
      rate.hidden = true;
      continue;
    }
    rate.href = amoUrl(self);
    rate.target = "_blank";
    rate.rel = "noopener";
  }
  if (!container) return;
  const list = container.querySelector("[data-more-list]");
  const items = moreAddons(selfId, os).map((addon) => {
    const li = document.createElement("li");
    li.className = "more-item";
    const a = link(amoUrl(addon));
    const name = document.createElement("span");
    name.className = "more-name";
    name.textContent = addon.name;
    const what = document.createElement("span");
    what.className = "more-what";
    what.textContent = addon.what;
    a.append(name, what);
    li.append(a);
    return li;
  });
  if (list) {
    list.replaceChildren(...items);
    list.hidden = items.length === 0;
  }
  const all = container.querySelector("[data-more-all]");
  if (all) {
    all.href = AUTHOR_URL;
    all.target = "_blank";
    all.rel = "noopener";
  }
}
