// Shared donation helpers, identical in every add-on built from the template.
// Rules we hold ourselves to (and that keep AMO reviewers happy):
//   * Donations unlock nothing. Every feature is free for everyone.
//   * Asks live only in our own UI (popup / options), never in web pages,
//     never as a new tab on install or update, never as a notification.
//   * The milestone card shows at most once per milestone and has a
//     permanent "don't show again".

export const KOFI_URL = "https://ko-fi.com/carcer7378";

const NUDGE_KEY = "supportNudge";

/** Wire every element with [data-support] to open Ko-fi in a new tab. */
export function wireSupportLinks(root = document) {
  for (const a of root.querySelectorAll("[data-support]")) {
    a.href = KOFI_URL;
    a.target = "_blank";
    a.rel = "noopener";
  }
}

/**
 * Show a one-off thank-you card when `value` has crossed a milestone the user
 * has not seen yet. `render(milestone)` returns the message text.
 * Returns true if a card was shown.
 */
export async function maybeShowMilestone(container, value, milestones, render) {
  if (!container) return false;
  let state = { seen: [], optOut: false };
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

  container.replaceChildren();
  container.hidden = false;
  container.className = "support-card";

  const msg = document.createElement("p");
  msg.textContent = render(milestone);
  const row = document.createElement("div");
  row.className = "support-card-row";

  const give = document.createElement("a");
  give.className = "support-pill";
  give.setAttribute("data-support", "");
  const heart = document.createElement("span");
  heart.className = "heart";
  heart.textContent = "♥";
  give.append(heart, " Buy me a coffee");

  const later = document.createElement("button");
  later.type = "button";
  later.className = "link-btn";
  later.textContent = "Not now";

  const never = document.createElement("button");
  never.type = "button";
  never.className = "link-btn";
  never.textContent = "Don't ask again";

  row.append(give, later, never);
  container.append(msg, row);
  wireSupportLinks(container);

  const close = async (optOut) => {
    // Mark every reached milestone as seen so we never stack prompts.
    const seen = Array.from(new Set([...state.seen, ...reached]));
    await browser.storage.local.set({ [NUDGE_KEY]: { seen, optOut } });
    container.hidden = true;
  };
  give.addEventListener("click", () => close(false));
  later.addEventListener("click", () => close(false));
  never.addEventListener("click", () => close(true));
  return true;
}
