import { clampRate, formatRate, getSettings, siteKey } from "./shared/settings.js";

// Serialise every read-modify-write of storage so concurrent frames never
// lose each other's updates.
let queue = Promise.resolve();
function serial(fn) {
  const run = queue.then(fn, fn);
  queue = run.catch(() => {});
  return run;
}

async function rateFor(site, settings) {
  if (settings.remember === "site" && site) {
    const { sites = {} } = await browser.storage.local.get("sites");
    if (sites[site] != null) return clampRate(sites[site]);
  } else if (settings.remember === "global") {
    const { lastSpeed } = await browser.storage.local.get("lastSpeed");
    if (lastSpeed != null) return clampRate(lastSpeed);
  }
  return clampRate(settings.defaultSpeed);
}

function persistRate(site, rate, settings) {
  return serial(async () => {
    if (settings.remember === "site" && site) {
      const { sites = {} } = await browser.storage.local.get("sites");
      if (rate === clampRate(settings.defaultSpeed)) delete sites[site];
      else sites[site] = rate;
      await browser.storage.local.set({ sites });
    } else if (settings.remember === "global") {
      await browser.storage.local.set({ lastSpeed: rate });
    }
  });
}

function addStats(watched, saved) {
  if (!(watched > 0)) return Promise.resolve();
  return serial(async () => {
    const { stats } = await browser.storage.local.get("stats");
    const s = stats || { watched: 0, saved: 0, since: new Date().toISOString() };
    s.watched += watched;
    s.saved += saved;
    await browser.storage.local.set({ stats: s });
  });
}

// tabId -> true when some frame in the tab has media (used for key relay + badge).
const mediaTabs = new Set();

function setBadge(tabId, rate, hasMedia) {
  if (tabId == null) return;
  const text = hasMedia && Math.abs(rate - 1) > 0.001 ? formatRate(rate) : "";
  browser.action.setBadgeText({ tabId, text }).catch(() => {});
}

async function broadcast(tabId, msg) {
  try {
    await browser.tabs.sendMessage(tabId, msg);
  } catch {
    // No content script in this tab (restricted page or not injected yet).
  }
}

async function setRateForTab(tabId, url, rate, from) {
  const settings = await getSettings();
  const r = clampRate(rate);
  await persistRate(siteKey(url), r, settings);
  await broadcast(tabId, { type: "tempo:apply", rate: r, from });
  setBadge(tabId, r, mediaTabs.has(tabId));
  return r;
}

browser.runtime.onMessage.addListener((msg, sender) => {
  const tab = sender.tab;
  switch (msg && msg.type) {
    case "tempo:hello":
      return (async () => {
        const settings = await getSettings();
        const site = siteKey(tab ? tab.url : "");
        return { settings, site, rate: await rateFor(site, settings) };
      })();
    case "tempo:setRate": {
      // From the popup (explicit tabId/url; an extension page opened in a tab
      // also has sender.tab, so the explicit target wins) or a content script.
      const tabId = msg.tabId != null ? msg.tabId : tab && tab.id;
      const url = msg.tabId != null ? msg.url : tab && tab.url;
      if (tabId == null) return undefined;
      return setRateForTab(tabId, url, msg.rate, msg.from);
    }
    case "tempo:report":
      if (tab) {
        if (msg.hasMedia) mediaTabs.add(tab.id);
        setBadge(tab.id, msg.rate, msg.hasMedia || mediaTabs.has(tab.id));
      }
      return undefined;
    case "tempo:relayKey":
      if (tab && mediaTabs.has(tab.id)) broadcast(tab.id, { type: "tempo:key", action: msg.action });
      return undefined;
    case "tempo:stats":
      return addStats(Number(msg.watched) || 0, Number(msg.saved) || 0);
    default:
      return undefined;
  }
});

browser.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading" && info.url) mediaTabs.delete(tabId);
});
browser.tabs.onRemoved.addListener((tabId) => mediaTabs.delete(tabId));

browser.action.setBadgeBackgroundColor({ color: "#5b21b6" }).catch(() => {});
if (browser.action.setBadgeTextColor) browser.action.setBadgeTextColor({ color: "#ffffff" }).catch(() => {});

// On install/update, inject into tabs that were already open so Tempo works
// without a page reload. The content script guards against double injection.
browser.runtime.onInstalled.addListener(async () => {
  let tabs = [];
  try {
    tabs = await browser.tabs.query({ url: ["http://*/*", "https://*/*"] });
  } catch {
    return;
  }
  for (const t of tabs) {
    browser.scripting
      .executeScript({ target: { tabId: t.id, allFrames: true }, files: ["content/tempo.js"] })
      .catch(() => {});
  }
});
