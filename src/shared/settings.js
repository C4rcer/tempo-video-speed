// Settings shared by background, popup and options (ES module).
// The content script is a classic script, so it receives the merged settings
// from the background in its hello reply instead of importing this.

export const MIN_RATE = 0.07;
export const MAX_RATE = 16;

export const ACTIONS = {
  slower: "Slower",
  faster: "Faster",
  reset: "Reset to 1x (press again to go back)",
  favorite: "Jump to favourite speed (toggle)",
  rewind: "Rewind",
  advance: "Skip ahead",
  toggleOverlay: "Show / hide the on-video control",
};

export const DEFAULTS = {
  enabled: true,
  defaultSpeed: 1,
  step: 0.1,
  favoriteSpeed: 1.8,
  seekSeconds: 10,
  remember: "site", // site | global | off
  lockSpeed: false,
  preservePitch: true,
  controlAudio: true,
  overlay: "always", // always | flash | hidden
  overlayOpacity: 0.45,
  presets: [1, 1.25, 1.5, 1.75, 2, 2.5, 3],
  keys: { slower: "s", faster: "d", reset: "r", favorite: "g", rewind: "z", advance: "x", toggleOverlay: "v" },
  blockedSites: [],
};

// Pages where Firefox never lets extensions run.
export const RESTRICTED_HOSTS = new Set([
  "addons.mozilla.org", "accounts.firefox.com", "accounts-static.cdn.mozilla.net",
  "addons.cdn.mozilla.net", "api.accounts.firefox.com", "content.cdn.mozilla.net",
  "discovery.addons.mozilla.org", "install.mozilla.org", "oauth.accounts.firefox.com",
  "profile.accounts.firefox.com", "support.mozilla.org", "sync.services.mozilla.com",
]);

export function clampRate(r) {
  const n = Number(r);
  if (!Number.isFinite(n)) return 1;
  return Math.min(MAX_RATE, Math.max(MIN_RATE, Math.round(n * 100) / 100));
}

export function formatRate(r) {
  return clampRate(r).toFixed(2).replace(/0$/, "").replace(/\.0$/, "");
}

/** Hostname used for per-site memory and blocking ("" when not a web page). */
export function siteKey(url) {
  try {
    const u = new URL(url);
    if (u.protocol === "file:") return "file";
    if (!/^https?:$/.test(u.protocol)) return "";
    return u.hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function mergeSettings(stored) {
  const s = { ...DEFAULTS, ...(stored || {}) };
  s.keys = { ...DEFAULTS.keys, ...((stored && stored.keys) || {}) };
  if (!Array.isArray(s.presets) || !s.presets.length) s.presets = DEFAULTS.presets.slice();
  if (!Array.isArray(s.blockedSites)) s.blockedSites = [];
  return s;
}

export async function getSettings() {
  const { settings } = await browser.storage.local.get("settings");
  return mergeSettings(settings);
}

export async function saveSettings(patch) {
  const next = mergeSettings({ ...(await getSettings()), ...patch });
  await browser.storage.local.set({ settings: next });
  return next;
}

export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}
