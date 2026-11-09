import { wireSupportLinks } from "../shared/support.js";
import {
  ACTIONS, DEFAULTS, clampRate, formatDuration, formatRate, getSettings, saveSettings,
} from "../shared/settings.js";

const $ = (id) => document.getElementById(id);
let settings = null;
let toastTimer = null;

function toast() {
  const t = $("toast");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 1200);
}

async function save(patch) {
  settings = await saveSettings(patch);
  toast();
}

const NUMBER_FIELDS = {
  defaultSpeed: (v) => clampRate(v),
  step: (v) => Math.min(4, Math.max(0.01, Math.round(Number(v) * 100) / 100 || DEFAULTS.step)),
  favoriteSpeed: (v) => clampRate(v),
  seekSeconds: (v) => Math.min(600, Math.max(1, Math.round(Number(v)) || DEFAULTS.seekSeconds)),
};
const CHECKS = ["enabled", "lockSpeed", "preservePitch", "controlAudio"];
const SELECTS = ["remember", "overlay"];

function fill() {
  for (const k of Object.keys(NUMBER_FIELDS)) $(k).value = settings[k];
  for (const k of CHECKS) $(k).checked = !!settings[k];
  for (const k of SELECTS) $(k).value = settings[k];
  $("overlayOpacity").value = settings.overlayOpacity;
  $("presets").value = settings.presets.map(formatRate).join(", ");
  $("blockedSites").value = settings.blockedSites.join("\n");
  renderKeys();
}

function keyLabel(k) {
  if (!k) return "off";
  if (k === " ") return "Space";
  return k.length === 1 ? k.toUpperCase() : k;
}

function renderKeys() {
  const box = $("keys");
  box.replaceChildren();
  for (const [action, label] of Object.entries(ACTIONS)) {
    const row = document.createElement("div");
    row.className = "key-row";
    const name = document.createElement("span");
    name.textContent = label;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "key-btn" + (settings.keys[action] ? "" : " off");
    btn.textContent = keyLabel(settings.keys[action]);
    btn.addEventListener("click", () => listen(btn, action));
    row.append(name, btn);
    box.append(row);
  }
}

function listen(btn, action) {
  btn.classList.add("listening");
  btn.textContent = "press a key";
  const onKey = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    window.removeEventListener("keydown", onKey, true);
    btn.classList.remove("listening");
    if (e.key === "Escape") return renderKeys();
    if (["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key)) return renderKeys();
    const key = e.key === "Backspace" || e.key === "Delete" ? "" : (e.key.length === 1 ? e.key.toLowerCase() : e.key);
    const keys = { ...settings.keys };
    // One key, one action: steal it from whichever action had it.
    if (key) for (const a of Object.keys(keys)) if (keys[a] === key) keys[a] = "";
    keys[action] = key;
    await save({ keys });
    renderKeys();
  };
  window.addEventListener("keydown", onKey, true);
}

async function renderSites() {
  const { sites = {} } = await browser.storage.local.get("sites");
  const box = $("sites");
  box.replaceChildren();
  const entries = Object.entries(sites).sort((a, b) => a[0].localeCompare(b[0]));
  if (!entries.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "None yet. Change the speed on a site and Tempo remembers it there.";
    box.append(p);
    return;
  }
  for (const [name, rate] of entries) {
    const row = document.createElement("div");
    row.className = "site";
    const n = document.createElement("span");
    n.className = "name";
    n.textContent = name;
    const s = document.createElement("span");
    s.className = "spd";
    s.textContent = formatRate(rate) + "×";
    const del = document.createElement("button");
    del.type = "button";
    del.className = "link-btn";
    del.textContent = "Forget";
    del.addEventListener("click", async () => {
      const cur = (await browser.storage.local.get("sites")).sites || {};
      delete cur[name];
      await browser.storage.local.set({ sites: cur });
      renderSites();
      toast();
    });
    row.append(n, s, del);
    box.append(row);
  }
}

async function renderStats() {
  const { stats } = await browser.storage.local.get("stats");
  if (!stats || stats.watched < 1) {
    $("stats").textContent = "Nothing yet. Watch something faster than 1x and the time you get back adds up here.";
    return;
  }
  const since = new Date(stats.since).toLocaleDateString();
  $("stats").textContent =
    `${formatDuration(Math.max(0, stats.saved))} saved across ${formatDuration(stats.watched)} of video since ${since}.`;
}

async function init() {
  wireSupportLinks();
  settings = await getSettings();
  fill();
  renderSites();
  renderStats();

  for (const [k, norm] of Object.entries(NUMBER_FIELDS)) {
    $(k).addEventListener("change", async (e) => {
      const v = norm(e.target.value);
      e.target.value = v;
      await save({ [k]: v });
    });
  }
  for (const k of CHECKS) $(k).addEventListener("change", (e) => save({ [k]: e.target.checked }));
  for (const k of SELECTS) $(k).addEventListener("change", (e) => save({ [k]: e.target.value }));
  $("overlayOpacity").addEventListener("change", (e) => save({ overlayOpacity: Number(e.target.value) }));
  $("presets").addEventListener("change", async (e) => {
    const list = e.target.value.split(/[\s,;]+/).map(Number).filter((n) => n > 0).map(clampRate);
    const presets = Array.from(new Set(list)).sort((a, b) => a - b).slice(0, 12);
    await save({ presets: presets.length ? presets : DEFAULTS.presets.slice() });
    $("presets").value = settings.presets.map(formatRate).join(", ");
  });
  $("blockedSites").addEventListener("change", async (e) => {
    const list = e.target.value.split(/\s+/).map((s) => s.trim().toLowerCase()
      .replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/.*$/, "")).filter(Boolean);
    await save({ blockedSites: Array.from(new Set(list)) });
    $("blockedSites").value = settings.blockedSites.join("\n");
  });
  $("resetStats").addEventListener("click", async () => {
    await browser.storage.local.set({ stats: { watched: 0, saved: 0, since: new Date().toISOString() } });
    renderStats();
    toast();
  });

  browser.storage.onChanged.addListener((changes) => {
    if (changes.sites) renderSites();
    if (changes.stats) renderStats();
  });
}

init();
