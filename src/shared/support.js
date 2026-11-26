// Shared support helpers, identical in every add-on built from the template.
// Rules we hold ourselves to (and that keep AMO reviewers happy):
//   * Donations and ratings unlock nothing. Every feature is free for everyone.
//   * Asks live only in our own UI (popup / options), never in web pages,
//     never as a new tab on install or update, never as a notification.
//   * The milestone card shows at most once per milestone and has a
//     permanent "don't show again".
//   * The first card a user ever gets asks for a rating on Firefox Add-ons:
//     it costs them nothing, and ratings are what let other people find the
//     add-on. Every later card asks for a tip. Nobody is asked to rate twice.

import { ADDONS, amoUrl } from "./more-addons.js";

export const KOFI_URL = "https://ko-fi.com/carcer7378";

const NUDGE_KEY = "supportNudge";
const FREE = "It's free, with no ads and no tracking.";
const ASKS = {
  rate: "If it's useful to you, a rating on Firefox Add-ons helps other people find it.",
  tip: "If that's worth a coffee, tips keep it maintained.",
};

/** Wire every element with [data-support] to open Ko-fi in a new tab. */
export function wireSupportLinks(root = document) {
  for (const a of root.querySelectorAll("[data-support]")) {
    a.href = KOFI_URL;
    a.target = "_blank";
    a.rel = "noopener";
  }
}

function pill(className, symbolClass, symbol, label) {
  const a = document.createElement("a");
  a.className = className;
  const s = document.createElement("span");
  s.className = symbolClass;
  s.setAttribute("aria-hidden", "true");
  s.textContent = symbol;
  a.append(s, " " + label);
  return a;
}

function linkButton(text) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "link-btn";
  b.textContent = text;
  return b;
}

/**
 * Show a one-off thank-you card when `value` has crossed a milestone the user
 * has not seen yet. `fact(milestone)` returns one sentence saying what the
 * add-on has done for them ("You've picked 50 colors with Pipette."); the card
 * adds the rest. Returns true if a card was shown.
 */
export async function maybeShowMilestone(container, value, milestones, fact) {
  if (!container) return false;
  let state = { seen: [], optOut: false, rateAsked: false };
  try {
    const got = await browser.storage.local.get(NUDGE_KEY);
    state = { ...state, ...(got[NUDGE_KEY] || {}) };
  } catch {
    return false;
  }
  if (state.optOut) return false;
  const reached = milestones.filter((m) => value >= m && !state.seen.includes(m));
  if (!reached.length) return false;
  const milestone = reached[reached.length - 1];
  // Without a listing to point at (an add-on not in the shared list yet) there is nothing to rate.
  const self = ADDONS.find((a) => a.id === browser.runtime.id);
  const ask = !state.rateAsked && self ? "rate" : "tip";

  container.replaceChildren();
  container.hidden = false;
  container.className = "support-card";

  const msg = document.createElement("p");
  msg.textContent = `${fact(milestone)} ${FREE} ${ASKS[ask]}`;
  const row = document.createElement("div");
  row.className = "support-card-row";

  let action;
  if (ask === "rate") {
    action = pill("support-pill rate", "star", "★", `Rate ${self.name}`);
    action.setAttribute("data-rate-ask", "");
    action.href = amoUrl(self);
    action.target = "_blank";
    action.rel = "noopener";
  } else {
    action = pill("support-pill", "heart", "♥", "Buy me a coffee");
    action.setAttribute("data-support", "");
  }
  const later = linkButton("Not now");
  const never = linkButton("Don't ask again");

  row.append(action, later, never);
  container.append(msg, row);
  wireSupportLinks(container);

  const close = async (optOut) => {
    // Mark every reached milestone as seen so we never stack prompts.
    const seen = Array.from(new Set([...state.seen, ...reached]));
    const rateAsked = state.rateAsked || ask === "rate";
    await browser.storage.local.set({ [NUDGE_KEY]: { seen, optOut, rateAsked } });
    container.hidden = true;
  };
  action.addEventListener("click", () => close(false));
  later.addEventListener("click", () => close(false));
  never.addEventListener("click", () => close(true));
  return true;
}
