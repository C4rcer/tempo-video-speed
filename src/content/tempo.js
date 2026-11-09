// Tempo content script: runs in every frame, finds <video>/<audio>, applies
// the chosen speed, draws the on-video control and handles shortcuts.
(() => {
  "use strict";
  if (window.__tempoLoaded) return;
  window.__tempoLoaded = true;

  const api = globalThis.browser;
  const MIN = 0.07;
  const MAX = 16;
  const HOST_ATTR = "data-tempo-overlay";
  const IS_TOP = window.top === window;
  // Identifies this frame's instance so it can ignore the echo of its own
  // speed changes (an old echo arriving mid key-repeat would roll the speed back).
  const INSTANCE = Math.random().toString(36).slice(2);

  let settings = null;
  let site = "";
  let desired = 1;
  let lastNonOne = null; // for the reset toggle
  let active = false;
  let overlaysHidden = false;
  let observer = null;
  let statsTimer = null;
  let reportTimer = null;
  const media = new Set();
  const state = new WeakMap();
  const pending = { watched: 0, saved: 0 };

  const clamp = (r) => Math.min(MAX, Math.max(MIN, Math.round(Number(r) * 100) / 100));
  const fmt = (r) => r.toFixed(2).replace(/0$/, "").replace(/\.0$/, "") + "×";
  const send = (msg) => api.runtime.sendMessage(msg).catch(() => {});
  const isBlocked = () => !settings.enabled || settings.blockedSites.includes(site);

  // ------------------------------------------------------------ discovery

  function shadowOf(el) {
    // openOrClosedShadowRoot is a Firefox extension-only API that also sees closed roots.
    return el.openOrClosedShadowRoot || el.shadowRoot || null;
  }

  function scan(root) {
    if (!root || !root.querySelectorAll) return;
    if (root instanceof HTMLMediaElement) attach(root);
    for (const el of root.querySelectorAll("video, audio")) attach(el);
    const all = root.querySelectorAll("*");
    for (const el of all) {
      const sr = shadowOf(el);
      if (sr && !sr.__tempoObserved) {
        sr.__tempoObserved = true;
        if (observer) observer.observe(sr, { childList: true, subtree: true });
        scan(sr);
      }
    }
  }

  let queued = [];
  let flushTimer = null;
  function onMutations(records) {
    for (const r of records) for (const n of r.addedNodes) if (n.nodeType === 1) queued.push(n);
    if (!flushTimer) flushTimer = setTimeout(flushQueued, 60);
  }
  function flushQueued() {
    flushTimer = null;
    const nodes = queued;
    queued = [];
    for (const n of nodes) if (n.isConnected) scan(n);
    for (const el of media) if (!el.isConnected) detach(el);
  }

  // ------------------------------------------------------------ media

  function attach(el) {
    if (!active || state.has(el)) return;
    if (el instanceof HTMLAudioElement && !settings.controlAudio) return;
    const ctl = new AbortController();
    const st = {
      ctl, ui: null, loadAt: performance.now(), played: false, fights: [], yieldUntil: 0, lastT: null, lastW: 0,
      live: false, liveChosen: false,
    };
    state.set(el, st);
    media.add(el);
    const on = (type, fn) => el.addEventListener(type, fn, { signal: ctl.signal });
    on("ratechange", onRateChange);
    for (const t of ["loadstart", "emptied"]) {
      on(t, () => {
        st.loadAt = performance.now();
        st.played = false;
        st.live = false;
        st.liveChosen = false;
      });
    }
    for (const t of ["loadedmetadata", "canplay", "play"]) on(t, () => apply(el));
    on("playing", () => { st.played = true; apply(el); });
    on("timeupdate", () => track(el, st));
    apply(el);
    if (el instanceof HTMLVideoElement) buildOverlay(el, st);
    if (!statsTimer) statsTimer = setInterval(flushStats, 30000);
    reportSoon();
  }

  function detach(el) {
    const st = state.get(el);
    if (!st) return;
    st.ctl.abort();
    if (st.ui) {
      st.ui.ro.disconnect();
      st.ui.host.remove();
    }
    state.delete(el);
    media.delete(el);
    reportSoon();
  }

  // Live streams stay at normal speed unless the user picks one for them:
  // a remembered 1.5x would race to the live edge and then stall.
  function isLive(el) {
    const d = el.duration;
    // MediaStream, HLS/MSE live (Twitch reports ~9.2e12 s).
    if (d === Infinity || d > 1e7) return true;
    // YouTube live streams report an ordinary finite duration; its player knows.
    try {
      const p = el.closest && el.closest("#movie_player");
      const w = p && p.wrappedJSObject;
      const data = w && typeof w.getVideoData === "function" ? w.getVideoData() : null;
      return !!(data && data.isLive);
    } catch {
      return false;
    }
  }

  const held = (el) => {
    const st = state.get(el);
    return !!(st && st.live && !st.liveChosen);
  };

  // user = an explicit speed choice (key, popup, on-video control); otherwise
  // this is Tempo re-applying the remembered speed on a media event.
  function apply(el, user = false) {
    if (!active) return;
    const st = state.get(el);
    if (st && (st.live || isLive(el))) {
      if (user) st.liveChosen = true;
      if (!st.live) {
        st.live = true;
        if (!st.liveChosen) {
          // Undo a speed applied before the stream was recognised as live.
          try {
            if (Math.abs(el.defaultPlaybackRate - desired) < 0.001) el.defaultPlaybackRate = 1;
            if (Math.abs(el.playbackRate - desired) < 0.001) el.playbackRate = 1;
          } catch { /* ignore */ }
        }
        reportSoon();
      }
      if (!st.liveChosen) return updateOverlay(el);
    }
    try {
      if (el.preservesPitch !== settings.preservePitch) el.preservesPitch = settings.preservePitch;
      if (Math.abs(el.defaultPlaybackRate - desired) > 0.001) el.defaultPlaybackRate = desired;
      if (Math.abs(el.playbackRate - desired) > 0.001) el.playbackRate = desired;
    } catch {
      // Some players throw for out-of-range rates (e.g. MSE limits); keep going.
    }
    updateOverlay(el);
  }

  function onRateChange(e) {
    const el = e.target;
    const st = state.get(el);
    if (!st || !active) return;
    // A live stream's speed is the site's business until the user picks one,
    // and a change there must not become the remembered speed for the site.
    if (held(el)) return updateOverlay(el);
    const r = el.playbackRate;
    if (Math.abs(r - desired) < 0.001) return updateOverlay(el);
    const now = performance.now();
    const siteReset = now - st.loadAt < 2500 || !st.played;
    if (settings.lockSpeed || siteReset) {
      // The page changed the speed by itself (typically back to 1x when a new
      // video loads). Put ours back, but stop if the page keeps fighting.
      if (now < st.yieldUntil) return updateOverlay(el);
      st.fights = st.fights.filter((t) => now - t < 2000);
      st.fights.push(now);
      if (st.fights.length > 8) {
        st.yieldUntil = now + 10000;
        return updateOverlay(el);
      }
      apply(el);
    } else {
      // The user picked a speed in the site's own player menu: adopt it.
      setDesired(r, { persist: true, flash: true });
    }
  }

  function setDesired(rate, { persist = true, flash = true } = {}) {
    const r = clamp(rate);
    if (Math.abs(r - 1) > 0.001) lastNonOne = r;
    desired = r;
    for (const el of media) apply(el, true);
    if (flash) flashOverlays();
    if (persist) send({ type: "tempo:setRate", rate: r, from: INSTANCE });
    reportSoon();
  }

  function primary() {
    let best = null;
    let bestScore = -1;
    for (const el of media) {
      const rect = el.getBoundingClientRect();
      const area = rect.width * rect.height;
      const score = (el.paused ? 0 : 1e9) + area + (el instanceof HTMLVideoElement ? 1 : 0);
      if (score > bestScore) { best = el; bestScore = score; }
    }
    return best;
  }

  // ------------------------------------------------------------ actions

  function run(action) {
    const step = Number(settings.step) || 0.1;
    // Step from what is playing: a held live stream shows 1x, not the remembered speed.
    const cur = shownRate();
    switch (action) {
      case "slower": return setDesired(cur - step);
      case "faster": return setDesired(cur + step);
      case "reset":
        return setDesired(Math.abs(cur - 1) < 0.001 ? (lastNonOne || settings.favoriteSpeed) : 1);
      case "favorite": {
        const fav = clamp(settings.favoriteSpeed);
        return setDesired(Math.abs(cur - fav) < 0.001 ? 1 : fav);
      }
      case "rewind":
      case "advance": {
        const el = primary();
        if (!el) return undefined;
        const d = (Number(settings.seekSeconds) || 10) * (action === "rewind" ? -1 : 1);
        const end = Number.isFinite(el.duration) ? el.duration : Infinity;
        el.currentTime = Math.min(end, Math.max(0, el.currentTime + d));
        return flashOverlays();
      }
      case "toggleOverlay":
        overlaysHidden = !overlaysHidden;
        for (const el of media) updateOverlay(el);
        return undefined;
      default:
        return undefined;
    }
  }

  function isTyping(e) {
    const t = (e.composedPath && e.composedPath()[0]) || e.target;
    if (!t || t.nodeType !== 1) return false;
    if (t.isContentEditable) return true;
    const tag = t.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  function onKey(e) {
    if (!active || e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;
    if (isTyping(e)) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const action = Object.keys(settings.keys).find((a) => settings.keys[a] === k);
    if (!action) return;
    if (!media.size) {
      // The video may live in another frame (e.g. an embed): let the
      // background hand the key to whichever frame has media.
      send({ type: "tempo:relayKey", action });
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    run(action);
  }

  // ------------------------------------------------------------ overlay

  const CSS = `
    .pill { display: inline-flex; align-items: center; gap: 3px; margin: 10px; padding: 3px 5px;
      font: 600 13px/1 system-ui, -apple-system, "Segoe UI", sans-serif; color: #fff;
      background: rgba(20, 16, 36, 0.72); border-radius: 7px; user-select: none;
      opacity: var(--o); transition: opacity .18s ease; cursor: default; pointer-events: auto; }
    .pill:hover, .pill.open, .pill.flash { opacity: 1; }
    .pill.gone { display: none; }
    .rate { min-width: 40px; text-align: center; padding: 3px 2px; cursor: pointer; font-variant-numeric: tabular-nums; }
    button { all: unset; display: none; box-sizing: border-box; width: 22px; height: 22px; border-radius: 5px;
      text-align: center; line-height: 22px; font-size: 14px; color: #fff; background: rgba(255,255,255,.14); cursor: pointer; }
    .pill:hover button, .pill.open button { display: inline-block; }
    button:hover { background: rgba(167, 139, 250, .55); }
  `;

  function buildOverlay(el, st) {
    const parent = el.parentNode;
    if (!parent) return;
    const host = document.createElement("div");
    host.setAttribute(HOST_ATTR, "");
    host.style.cssText =
      "all: initial !important; position: absolute !important; z-index: 2147483646 !important; " +
      "top: 0; left: 0; width: 0 !important; height: 0 !important; overflow: visible !important; display: block !important;";
    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = CSS;
    const pill = document.createElement("div");
    pill.className = "pill";
    const mk = (action, label, title) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.a = action;
      b.textContent = label;
      b.title = title;
      return b;
    };
    const rate = document.createElement("span");
    rate.className = "rate";
    rate.title = "Tempo: tap for controls";
    pill.append(
      mk("rewind", "«", "Rewind"),
      mk("slower", "−", "Slower"),
      rate,
      mk("faster", "+", "Faster"),
      mk("advance", "»", "Skip ahead"),
      mk("toggleOverlay", "×", "Hide (press the shortcut again to show)")
    );
    root.append(style, pill);

    // Keep clicks on the control from reaching the player (which would pause it).
    for (const t of ["click", "dblclick", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend"]) {
      pill.addEventListener(t, (ev) => {
        ev.stopPropagation();
        if (t === "click") {
          ev.preventDefault();
          const b = ev.target.closest && ev.target.closest("button");
          if (b) run(b.dataset.a);
          else pill.classList.toggle("open");
        }
      });
    }

    parent.insertBefore(host, el);
    const ro = new ResizeObserver(() => position(el, host, pill));
    ro.observe(el);
    st.ui = { host, pill, rate, ro, flashTimer: null };
    position(el, host, pill);
    updateOverlay(el);
  }

  function position(el, host, pill) {
    host.style.setProperty("top", el.offsetTop + "px", "important");
    host.style.setProperty("left", el.offsetLeft + "px", "important");
    const small = el.offsetWidth < 160 || el.offsetHeight < 90;
    pill.classList.toggle("gone", small || overlaysHidden || settings.overlay === "hidden");
  }

  function updateOverlay(el) {
    const st = state.get(el);
    if (!st || !st.ui) return;
    const { host, pill, rate } = st.ui;
    rate.textContent = fmt(el.playbackRate || desired);
    const op = settings.overlay === "flash" ? 0 : Number(settings.overlayOpacity) || 0.45;
    pill.style.setProperty("--o", String(op));
    position(el, host, pill);
  }

  function flashOverlays() {
    for (const el of media) {
      const st = state.get(el);
      if (!st || !st.ui) continue;
      updateOverlay(el);
      const p = st.ui.pill;
      p.classList.add("flash");
      clearTimeout(st.ui.flashTimer);
      st.ui.flashTimer = setTimeout(() => p.classList.remove("flash"), 1400);
    }
  }

  // ------------------------------------------------------------ stats + reporting

  function track(el, st) {
    const t = el.currentTime;
    const w = performance.now();
    if (st.lastT != null && !el.paused && !el.muted && el.volume > 0) {
      const dm = t - st.lastT;
      const dw = (w - st.lastW) / 1000;
      if (dm > 0 && dm < 4 && dw < 4) {
        pending.watched += dm;
        pending.saved += dm * (1 - 1 / (el.playbackRate || 1));
      }
    }
    st.lastT = t;
    st.lastW = w;
  }

  function flushStats() {
    if (pending.watched < 0.5) return;
    send({ type: "tempo:stats", watched: pending.watched, saved: pending.saved });
    pending.watched = 0;
    pending.saved = 0;
  }

  // The speed the main media really plays at (1x for a held live stream).
  function shownRate() {
    const p = primary();
    return p && held(p) ? 1 : desired;
  }

  function reportSoon() {
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => send({ type: "tempo:report", rate: shownRate(), hasMedia: media.size > 0 }), 150);
  }

  // ------------------------------------------------------------ lifecycle

  function start() {
    if (active) return;
    active = true;
    // Remove controls left behind by a previous (reloaded/updated) Tempo instance.
    for (const old of document.querySelectorAll(`[${HOST_ATTR}]`)) old.remove();
    observer = new MutationObserver(onMutations);
    observer.observe(document, { childList: true, subtree: true });
    const kick = () => scan(document);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", kick, { once: true });
    kick();
    // Media events don't bubble but do pass the capture phase: catch players
    // the observer missed (e.g. elements created before we started).
    for (const t of ["play", "loadedmetadata"]) {
      document.addEventListener(t, (e) => {
        if (e.target instanceof HTMLMediaElement) attach(e.target);
      }, true);
    }
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pagehide", flushStats);
    document.addEventListener("visibilitychange", () => { if (document.hidden) flushStats(); });
    document.addEventListener("fullscreenchange", () => { for (const el of media) updateOverlay(el); });
  }

  function stop() {
    if (!active) return;
    for (const el of Array.from(media)) {
      try { el.playbackRate = 1; el.defaultPlaybackRate = 1; } catch { /* ignore */ }
      detach(el);
    }
    active = false;
    if (observer) observer.disconnect();
    window.removeEventListener("keydown", onKey, true);
    flushStats();
    reportSoon();
  }

  api.runtime.onMessage.addListener((msg) => {
    if (!msg || !settings) return undefined;
    switch (msg.type) {
      case "tempo:apply":
        if (msg.from === INSTANCE) return undefined;
        // Even an unchanged rate is applied: it is an explicit choice, which
        // releases a held live stream (the popup shows it at 1x).
        if (active) setDesired(msg.rate, { persist: false });
        else desired = clamp(msg.rate);
        return undefined;
      case "tempo:key":
        if (active && media.size) run(msg.action);
        return undefined;
      case "tempo:query": {
        const p = primary();
        const res = {
          rate: shownRate(), live: !!(p && held(p)), hasMedia: media.size > 0, count: media.size,
          blocked: isBlocked(), site, frame: IS_TOP ? "top" : "sub",
        };
        if (media.size) return Promise.resolve(res);
        // The top frame always answers, but late, so a frame that has the video wins.
        if (IS_TOP) return new Promise((r) => setTimeout(() => r(res), 200));
        return undefined;
      }
      default:
        return undefined;
    }
  });

  api.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.settings || !settings) return;
    settings = { ...settings, ...changes.settings.newValue };
    if (isBlocked()) return stop();
    if (!active) return start();
    for (const el of Array.from(media)) {
      if (el instanceof HTMLAudioElement && !settings.controlAudio) detach(el);
      else apply(el);
    }
    if (settings.controlAudio) scan(document);
  });

  api.runtime.sendMessage({ type: "tempo:hello" }).then((res) => {
    if (!res) return;
    settings = res.settings;
    site = res.site;
    desired = clamp(res.rate);
    if (Math.abs(desired - 1) > 0.001) lastNonOne = desired;
    if (!isBlocked()) start();
  }).catch(() => {});
})();
