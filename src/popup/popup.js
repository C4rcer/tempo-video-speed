import { wireSupportLinks, maybeShowMilestone } from "../shared/support.js";
import {
  RESTRICTED_HOSTS, clampRate, formatDuration, formatRate, getSettings, saveSettings, siteKey,
} from "../shared/settings.js";

const $ = (id) => document.getElementById(id);
const IS_ANDROID = /Android/i.test(navigator.userAgent);
let tab = null;
let site = "";
let settings = null;
let rate = 1;

function render() {
  $("rate").textContent = formatRate(rate) + "×";
  $("slider").value = String(Math.min(4, Math.max(0.25, rate)));
  for (const b of $("presets").children) b.classList.toggle("on", Math.abs(Number(b.dataset.rate) - rate) < 0.001);
}

async function setRate(r) {
  rate = clampRate(r);
  render();
  if (!tab) return;
  await browser.runtime.sendMessage({ type: "tempo:setRate", tabId: tab.id, url: tab.url, rate }).catch(() => {});
}

function renderPresets() {
  const box = $("presets");
  box.replaceChildren();
  for (const p of settings.presets) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.rate = String(p);
    b.textContent = formatRate(p) + "×";
    b.addEventListener("click", () => setRate(p));
    box.append(b);
  }
}

function notice(text, button) {
  const n = $("notice");
  n.replaceChildren();
  const p = document.createElement("div");
  p.textContent = text;
  n.append(p);
  if (button) n.append(button);
  n.hidden = false;
}

function setControlsEnabled(on) {
  $("controls").classList.toggle("disabled", !on);
}

async function query() {
  try {
    return (await browser.tabs.sendMessage(tab.id, { type: "tempo:query" })) || null;
  } catch {
    return null;
  }
}

async function inject() {
  try {
    await browser.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["content/tempo.js"] });
    await new Promise((r) => setTimeout(r, 250));
    return await query();
  } catch {
    return null;
  }
}

async function storedRate() {
  if (settings.remember === "site") {
    const { sites = {} } = await browser.storage.local.get("sites");
    if (sites[site] != null) return sites[site];
  } else if (settings.remember === "global") {
    const { lastSpeed } = await browser.storage.local.get("lastSpeed");
    if (lastSpeed != null) return lastSpeed;
  }
  return settings.defaultSpeed;
}

async function renderStats() {
  const { stats } = await browser.storage.local.get("stats");
  const saved = stats ? stats.saved : 0;
  $("saved").textContent = saved >= 60
    ? `Time saved: ${formatDuration(saved)}`
    : "Time saved shows here as you watch";
  await maybeShowMilestone($("supportCard"), saved / 3600, [1, 10, 50, 200], (h) =>
    `Tempo has saved you ${h} hour${h === 1 ? "" : "s"} of watching so far.`);
}

function renderKeysHint() {
  if (IS_ANDROID) return;
  const k = settings.keys;
  const up = (x) => (x || "").toUpperCase();
  $("keysHint").textContent = `${up(k.slower)} / ${up(k.faster)} speed, ${up(k.reset)} reset`;
}

async function onSiteToggle(e) {
  const on = e.target.checked;
  const blocked = new Set(settings.blockedSites);
  if (on) blocked.delete(site);
  else blocked.add(site);
  settings = await saveSettings({ blockedSites: Array.from(blocked) });
  setControlsEnabled(on && settings.enabled);
  $("mediaInfo").textContent = on ? "" : "Tempo is off here. Videos play at the site's own speed.";
}

async function init() {
  wireSupportLinks();
  $("btnOptions").addEventListener("click", () => {
    browser.runtime.openOptionsPage();
    window.close();
  });
  $("btnSlower").addEventListener("click", () => setRate(rate - (Number(settings.step) || 0.1)));
  $("btnFaster").addEventListener("click", () => setRate(rate + (Number(settings.step) || 0.1)));
  $("slider").addEventListener("input", (e) => setRate(e.target.value));
  $("siteEnabled").addEventListener("change", onSiteToggle);

  settings = await getSettings();
  renderPresets();
  renderKeysHint();
  renderStats();

  // ?tab=<id> lets the popup be opened in a normal tab for tests and store screenshots.
  const forced = Number(new URLSearchParams(location.search).get("tab"));
  if (forced) tab = await browser.tabs.get(forced).catch(() => null);
  else [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  site = siteKey(tab && tab.url ? tab.url : "");
  let host = "";
  try { host = new URL(tab.url).hostname; } catch { /* about: pages */ }

  if (!site || RESTRICTED_HOSTS.has(host)) {
    notice("Firefox doesn't let add-ons run on this page. Open a page with a video to use Tempo.");
    setControlsEnabled(false);
    $("siteEnabled").disabled = true;
    tab = null;
    render();
    return;
  }

  $("siteName").textContent = site === "file" ? "local files" : site;
  const siteOn = !settings.blockedSites.includes(site);
  $("siteEnabled").checked = siteOn;

  if (!settings.enabled) {
    const b = document.createElement("button");
    b.className = "primary";
    b.textContent = "Turn Tempo back on";
    b.addEventListener("click", async () => {
      settings = await saveSettings({ enabled: true });
      $("notice").hidden = true;
      setControlsEnabled(!settings.blockedSites.includes(site));
    });
    notice("Tempo is paused on every site.", b);
    setControlsEnabled(false);
  } else {
    setControlsEnabled(siteOn);
  }

  const granted = await browser.permissions.contains({ origins: ["<all_urls>"] });
  if (!granted) {
    const b = document.createElement("button");
    b.className = "primary";
    b.textContent = "Allow access to websites";
    b.addEventListener("click", async () => {
      // permissions.request must run straight from the click.
      const ok = await browser.permissions.request({ origins: ["<all_urls>"] });
      if (ok) window.location.reload();
    });
    notice("Tempo needs access to websites to change video speed. Nothing is collected or sent anywhere.", b);
  }

  let st = await query();
  if (!st && granted) st = await inject();
  if (st) {
    rate = st.rate;
    $("mediaInfo").textContent = !siteOn
      ? "Tempo is off here. Videos play at the site's own speed."
      : st.live
        ? "Live stream: kept at normal speed so it doesn't stall. Pick a speed to change it anyway."
        : st.hasMedia
          ? `Controlling ${st.count} video/audio element${st.count === 1 ? "" : "s"} on this page.`
        : "No video playing yet. Your speed applies as soon as one starts.";
  } else {
    rate = clampRate(await storedRate());
    if (granted) $("mediaInfo").textContent = "Reload this page to let Tempo control its videos.";
  }
  render();
}

init();
