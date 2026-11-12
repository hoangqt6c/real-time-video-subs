/* Realtime Vietnamese Subtitles (JA->VI) — content script.
 * ONLY runs on websites the user has enabled in the extension popup (stored in chrome.storage.local).
 * Features:
 * 1. Extracts Video Code (e.g. MEYD-916), Title, and Plot Summary from the page (missav, etc.) for 18+ context.
 * 2. Caches translated subtitles by timestamp & Video Code in localStorage (`rtsubs_subcache_<CODE>`) AND syncs
 *    with the server's disk cache (`workspace/cache/<CODE>.json`), so reopening a video loads existing subtitles
 *    instantly without re-running Whisper and only translates remaining untranslated parts.
 * 3. Original video audio stays UNMUTED and the video NEVER pauses to wait for translation. */
(() => {
  if (window.__rtDub) return;
  window.__rtDub = true;

  const SERVER = "http://127.0.0.1:8765";
  const LS_BAR = "rtdub.bar";
  const LS_SUB_PREFIX = "rtsubs_subcache_";
  const STORAGE_KEY = "rtdub_allowed_domains";
  const TICK_MS = 80;
  const MIN_VIDEO_W = 150;
  const MIN_VISIBLE = 0.4;

  // ---------- site whitelist check (controlled via Extension Popup) ----------
  let siteEnabled = false;

  function normalizeDomain(input) {
    if (!input) return "";
    return String(input).trim().toLowerCase().replace(/^\*\./, "").replace(/^www\./, "");
  }

  function checkDomainAllowed(domains) {
    const host = normalizeDomain(location.hostname);
    if (host === "localhost" || host === "127.0.0.1") return true; // Always allow local test fixture
    if (!Array.isArray(domains) || domains.length === 0) return false;
    return domains.some((d) => {
      const rule = normalizeDomain(d);
      return rule && (host === rule || host.endsWith("." + rule));
    });
  }

  function applySiteAllowedState(allowed) {
    if (siteEnabled === allowed) return;
    siteEnabled = allowed;
    if (!siteEnabled) {
      if (act) stop(act);
      for (const [, b] of buttons) b.remove();
      buttons.clear();
      bar.style.display = "none";
      frame.style.display = "none";
      status.style.display = "none";
      if (root.parentNode) root.remove();
    } else {
      mount();
      syncButtons();
    }
  }

  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
    chrome.storage.local.get({ [STORAGE_KEY]: [] }, (res) => {
      applySiteAllowedState(checkDomainAllowed(res[STORAGE_KEY]));
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes[STORAGE_KEY]) {
        applySiteAllowedState(checkDomainAllowed(changes[STORAGE_KEY].newValue || []));
      }
    });
  } else {
    siteEnabled = true;
  }

  // ---------- styles injection ----------
  const STYLE_ID = "rtdub-styles";
  if (!document.getElementById(STYLE_ID)) {
    const st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = `
      .rtdub-btn {
        position: fixed;
        display: none;
        pointer-events: auto;
        z-index: 2147483647;
        height: 32px;
        padding: 0 16px;
        border-radius: 9999px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        font-size: 12.5px;
        font-weight: 700;
        letter-spacing: 0.25px;
        cursor: pointer;
        white-space: nowrap;
        user-select: none;
        touch-action: manipulation;
        transition: transform 0.18s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.18s ease, filter 0.18s ease, background 0.18s ease;
        box-sizing: border-box;
        align-items: center;
        justify-content: center;
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        line-height: 1;
      }
      .rtdub-btn-idle {
        background: linear-gradient(135deg, #ef4444 0%, #dc2626 50%, #b91c1c 100%);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.32);
        box-shadow: 0 4px 14px rgba(220, 38, 38, 0.42), 0 2px 6px rgba(0, 0, 0, 0.35);
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
      }
      .rtdub-btn-idle:hover {
        transform: translateY(-1px) scale(1.04);
        filter: brightness(1.12);
        box-shadow: 0 6px 20px rgba(220, 38, 38, 0.58), 0 3px 8px rgba(0, 0, 0, 0.4);
      }
      .rtdub-btn-idle:active {
        transform: translateY(0) scale(0.97);
      }
      .rtdub-btn-active {
        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.38);
        box-shadow: 0 4px 16px rgba(16, 185, 129, 0.48), 0 2px 6px rgba(0, 0, 0, 0.35);
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
      }
      .rtdub-btn-active:hover {
        transform: translateY(-1px) scale(1.03);
        filter: brightness(1.1);
        box-shadow: 0 6px 20px rgba(16, 185, 129, 0.65), 0 3px 8px rgba(0, 0, 0, 0.4);
      }
      .rtdub-btn-active:active {
        transform: translateY(0) scale(0.97);
      }
      .rtdub-pulse {
        display: inline-block;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #ffffff;
        margin-right: 6px;
        box-shadow: 0 0 8px #ffffff;
        animation: rtdub-blink 1.2s infinite ease-in-out;
      }
      @keyframes rtdub-blink {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.35; transform: scale(0.75); }
      }
      .rtdub-status {
        position: fixed;
        z-index: 2147483647;
        max-width: 380px;
        padding: 5px 14px;
        border-radius: 20px;
        background: rgba(15, 23, 42, 0.85);
        color: #f8fafc;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 12px;
        font-weight: 600;
        line-height: 20px;
        letter-spacing: 0.2px;
        text-align: center;
        display: none;
        pointer-events: none;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.16);
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
        transition: opacity 0.2s ease, transform 0.2s ease;
      }
      .rtdub-bar {
        position: absolute;
        pointer-events: auto;
        cursor: move;
        display: none;
        box-sizing: border-box;
        width: auto;
        max-width: 84%;
        padding: 6px 14px;
        border-radius: 8px;
        text-align: center;
        color: #ffffff;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        font-weight: 600;
        letter-spacing: 0.25px;
        line-height: 1.4;
        text-shadow: 
          0 1px 2px rgba(0, 0, 0, 0.95),
          0 2px 4px rgba(0, 0, 0, 0.85),
          0 0 10px rgba(0, 0, 0, 0.7);
        background: rgba(0, 0, 0, 0.48);
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
        user-select: none;
        touch-action: none;
        transition: background 0.15s ease, border-color 0.15s ease;
      }
      .rtdub-bar:hover {
        background: rgba(0, 0, 0, 0.68);
        border-color: rgba(255, 255, 255, 0.22);
      }
      .rtdub-frame {
        position: fixed;
        display: none;
        box-sizing: border-box;
        border: 2px solid rgba(16, 185, 129, 0.75);
        border-radius: 8px;
        pointer-events: none;
        box-shadow: 0 0 14px rgba(16, 185, 129, 0.35);
      }
      .rtdub-strip {
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 3.5px;
        border-radius: 6px 6px 0 0;
        background: rgba(255, 255, 255, 0.25);
        overflow: hidden;
      }
    `;
    (document.head || document.documentElement).appendChild(st);
  }

  // ---------- overlay root (moved into the fullscreen element when needed) ----------
  const root = document.createElement("div");
  root.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483646;";
  const status = document.createElement("div");
  status.className = "rtdub-status";
  const bar = document.createElement("div");
  bar.className = "rtdub-bar";
  bar.title = "Kéo để di chuyển • Cuộn chuột để đổi cỡ chữ • Nháy đúp để đặt lại";

  const frame = document.createElement("div");
  frame.className = "rtdub-frame";
  const strip = document.createElement("div");
  strip.className = "rtdub-strip";
  frame.append(strip);
  root.append(frame, bar, status);

  const mount = () => {
    if (!siteEnabled) return;
    const host = document.fullscreenElement || document.documentElement;
    if (root.parentNode !== host) host.append(root);
  };
  document.addEventListener("fullscreenchange", mount);

  // ---------- page context scraper (Video Code, Title, Summary & Cast on missav / JAV sites) ----------
  const CODE_REGEX = /\b(FC2-PPV-\d{5,8}|[A-Z]{2,6}-\d{2,6})\b/i;

  function extractPageContext() {
    const h1 = document.querySelector("h1.text-nord6, h1");
    const pageTitle = (h1 ? h1.innerText : document.title || "").replace(/\s+/g, " ").trim();

    let videoCode = "";
    for (const source of [pageTitle, document.title || "", decodeURIComponent(location.pathname || "")]) {
      const m = source.match(CODE_REGEX);
      if (m) {
        videoCode = m[1].toUpperCase();
        break;
      }
    }
    if (!videoCode) {
      const slug = (location.pathname || "").split("/").filter(Boolean).pop() || location.hostname;
      videoCode = slug.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 48).toUpperCase();
    }

    const summaryParts = [];
    const seenText = new Set();
    const addPart = (txt) => {
      const clean = (txt || "").replace(/\s+/g, " ").trim();
      if (clean && clean.length >= 10 && !seenText.has(clean)) {
        seenText.add(clean);
        summaryParts.push(clean);
      }
    };

    // 1. missav plot summary box: div.mb-1.text-secondary.break-all (and expanded line-clamp-none)
    for (const sel of [
      "div.mb-1.text-secondary.break-all",
      "div.text-secondary.break-all",
      "div.mb-4 div.text-secondary",
      "[class*='synopsis']",
      "[class*='video-description']",
      ".description",
    ]) {
      for (const el of document.querySelectorAll(sel)) {
        addPart(el.textContent || el.innerText || "");
      }
    }

    // 2. missav cast / tags / genre rows inside details section
    for (const row of document.querySelectorAll("div.space-y-2 > div.text-secondary")) {
      const t = (row.innerText || "").replace(/\s+/g, " ").trim();
      if (t && t.length < 300) addPart(t);
    }

    // 3. Meta description fallback
    const metaDesc =
      document.querySelector('meta[property="og:description"]')?.getAttribute("content") ||
      document.querySelector('meta[name="description"]')?.getAttribute("content") ||
      "";
    if (metaDesc) addPart(metaDesc);

    const pageSummary = summaryParts.join("\n").slice(0, 2500);
    return { videoCode, pageTitle, pageSummary };
  }

  // ---------- browser localStorage subtitle cache by Video Code ----------
  function loadLocalSubCache(videoCode) {
    if (!videoCode) return null;
    try {
      const raw = localStorage.getItem(LS_SUB_PREFIX + videoCode);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.subs)) return null;
      return data;
    } catch (_) {
      return null;
    }
  }

  function saveLocalSubCache(videoCode, info, readySet) {
    if (!videoCode || !info || !Array.isArray(info.subs)) return;
    try {
      const payload = {
        videoCode,
        duration: info.duration || 0,
        chunks: info.chunks || [],
        doneChunks: Array.from(readySet || []),
        subs: info.subs,
        updatedAt: Date.now(),
      };
      localStorage.setItem(LS_SUB_PREFIX + videoCode, JSON.stringify(payload));
    } catch (_) {}
  }

  // ---------- geometry helpers ----------
  function contentBox(v) {
    const r = v.getBoundingClientRect();
    let l = r.left, t = r.top, w = r.width, h = r.height;
    if (v.videoWidth && v.videoHeight && getComputedStyle(v).objectFit !== "cover") {
      const k = Math.min(r.width / v.videoWidth, r.height / v.videoHeight);
      w = v.videoWidth * k; h = v.videoHeight * k;
      l = r.left + (r.width - w) / 2; t = r.top + (r.height - h) / 2;
    }
    return { l, t, w, h };
  }
  function visibleFraction(b) {
    if (b.w <= 0 || b.h <= 0) return 0;
    const iw = Math.max(0, Math.min(b.l + b.w, innerWidth) - Math.max(b.l, 0));
    const ih = Math.max(0, Math.min(b.t + b.h, innerHeight) - Math.max(b.t, 0));
    return (iw * ih) / (b.w * b.h);
  }

  // ---------- server cancel helper ----------
  function cancelServerJob(jobId) {
    if (!jobId) return;
    try {
      fetch(`${SERVER}/jobs/${jobId}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
      }).catch(() => {});
    } catch (_) {}
  }

  // ---------- per-video buttons ----------
  const buttons = new Map(); // video -> button element
  const sourceOf = (v) => v.currentSrc || v.src || v.querySelector("source")?.src || "";
  const isPreviewVideo = (v) => {
    if (!v) return false;
    if (v.closest && v.closest("ytd-thumbnail, #preview, ytd-moving-thumbnail-renderer, [id*='preview']")) return true;
    return false;
  };
  const isVerticalFeed = () => {
    return location.hostname.includes("douyin.com") ||
           location.hostname.includes("tiktok.com") ||
           location.pathname.includes("/shorts/");
  };

  const BTN_IDLE_HTML = `<span style="margin-right:5px;font-size:14px;">🇻🇳</span><span>VI · Phụ đề</span>`;
  function activeLabel(s) {
    const n = s.info?.chunks?.length;
    const progress = n ? ` ${s.ready.size}/${n}` : "…";
    return `<span class="rtdub-pulse"></span><span>Phụ đề VI${progress}</span><span style="margin-left:7px;opacity:0.85;font-size:11px;">✕</span>`;
  }

  function makeButton(v) {
    const b = document.createElement("button");
    b.className = "rtdub-btn rtdub-btn-idle";
    b.__currentHtml = BTN_IDLE_HTML;
    b.innerHTML = BTN_IDLE_HTML;
    b.title = "Bật phụ đề tiếng Việt realtime cho video này";
    for (const ev of ["pointerdown", "mousedown", "mouseup", "dblclick"]) {
      b.addEventListener(ev, (e) => e.stopPropagation());
    }
    b.addEventListener("click", (e) => {
      e.stopPropagation(); e.preventDefault();
      if (act && act.video === v) {
        if (performance.now() - (act.startedAt || 0) < 1200) return;
        stop(act);
      } else {
        start(v);
      }
    });
    v.addEventListener("play", () => {
      if (!siteEnabled) return;
      if (act && act.video !== v && !isPreviewVideo(v)) {
        if (performance.now() - (act.startedAt || 0) < 2000) return;
        const bRect = v.getBoundingClientRect();
        if (bRect.width > 250 && bRect.height > 180) {
          stop(act, "Đã chuyển video");
        }
      }
    });
    root.append(b);
    buttons.set(v, b);
  }

  document.addEventListener("play", (e) => {
    if (!siteEnabled) return;
    const v = e.target;
    if (v && v.tagName === "VIDEO" && act && act.video !== v && !isPreviewVideo(v)) {
      if (performance.now() - (act.startedAt || 0) < 2000) return;
      const r = v.getBoundingClientRect();
      if (r.width < 250 || r.height < 180) return;
      stop(act, "Đã chuyển video");
    }
  }, true);

  function videoPageKey(urlStr) {
    try {
      const u = new URL(urlStr, location.href);
      if (u.hostname.includes("youtube.com") || u.hostname.includes("youtu.be")) {
        const v = u.searchParams.get("v");
        if (v) return "yt:" + v;
        const m = u.pathname.match(/\/shorts\/([a-zA-Z0-9_-]+)/);
        if (m) return "yt-shorts:" + m[1];
      }
      if (u.hostname.includes("douyin.com")) {
        const mid = u.searchParams.get("modal_id");
        if (mid) return "douyin:" + mid;
        const m = u.pathname.match(/\/video\/(\d+)/);
        if (m) return "douyin:" + m[1];
      }
      if (u.hostname.includes("bilibili.com")) {
        const m = u.pathname.match(/\/(BV[a-zA-Z0-9]+)/);
        if (m) return "bili:" + m[1];
      }
      if (u.hostname.includes("tiktok.com")) {
        const m = u.pathname.match(/\/video\/(\d+)/);
        if (m) return "tiktok:" + m[1];
      }
      return u.origin + u.pathname;
    } catch (_) {
      return (urlStr || "").split("?")[0];
    }
  }
  let lastPageKey = videoPageKey(location.href);
  function checkUrlChange() {
    const currentKey = videoPageKey(location.href);
    if (currentKey !== lastPageKey) {
      lastPageKey = currentKey;
      if (act) {
        stop(act, "Đã chuyển video");
      }
    }
  }
  window.addEventListener("popstate", checkUrlChange);
  window.addEventListener("hashchange", checkUrlChange);
  const origPushState = history.pushState;
  if (origPushState) {
    history.pushState = function () {
      origPushState.apply(this, arguments);
      checkUrlChange();
    };
  }
  const origReplaceState = history.replaceState;
  if (origReplaceState) {
    history.replaceState = function () {
      origReplaceState.apply(this, arguments);
      checkUrlChange();
    };
  }
  window.addEventListener("pagehide", () => { if (act) stop(act); });
  window.addEventListener("beforeunload", () => { if (act) stop(act); });

  function syncButtons() {
    if (!siteEnabled) return;
    mount();
    checkUrlChange();
    for (const v of document.querySelectorAll("video")) {
      if (isPreviewVideo(v)) continue;
      if (!buttons.has(v)) makeButton(v);
    }

    if (act && act.video.isConnected) {
      const age = performance.now() - (act.startedAt || 0);
      if (age > 2500) {
        const actBox = contentBox(act.video);
        const actVis = visibleFraction(actBox);
        const isOffScreen = actBox.t + actBox.h <= 0 || actBox.t >= innerHeight ||
                            actBox.l + actBox.w <= 0 || actBox.l >= innerWidth;

        if (isVerticalFeed()) {
          if (isOffScreen || actVis < 0.05) {
            stop(act, "Đã cuộn qua video khác");
          } else if (actVis < 0.2) {
            act.offScreenSince = act.offScreenSince || performance.now();
            if (performance.now() - act.offScreenSince > 300) {
              stop(act, "Đã cuộn qua video khác");
            }
          } else {
            act.offScreenSince = 0;
          }
        }

        if (act) {
          for (const other of document.querySelectorAll("video")) {
            if (other !== act.video && other.isConnected && !isPreviewVideo(other)) {
              const obox = contentBox(other);
              if (obox.w < 250 || obox.h < 180) continue;
              const ovis = visibleFraction(obox);
              if ((!other.paused && ovis > 0.4) || (ovis > 0.65 && actVis < 0.3)) {
                stop(act, "Đã chuyển video");
                break;
              }
            }
          }
        }
      }
    }

    for (const [v, b] of buttons) {
      if (!v.isConnected) {
        b.remove(); buttons.delete(v);
        if (act && act.video === v) stop(act);
        continue;
      }
      const box = contentBox(v);
      const isAct = act && act.video === v;
      if (!isAct && (box.w < MIN_VIDEO_W || visibleFraction(box) < MIN_VISIBLE)) {
        b.style.display = "none";
        continue;
      }
      b.style.display = "inline-flex";
      const pad = 12;
      b.style.left = Math.min(Math.max(pad, box.l + pad), innerWidth - 170) + "px";
      b.style.top = Math.min(Math.max(pad, box.t + pad), innerHeight - 48) + "px";

      const targetHtml = isAct ? activeLabel(act) : BTN_IDLE_HTML;
      if (b.__currentHtml !== targetHtml) {
        b.__currentHtml = targetHtml;
        b.innerHTML = targetHtml;
      }
      const targetClass = isAct ? "rtdub-btn rtdub-btn-active" : "rtdub-btn rtdub-btn-idle";
      if (b.className !== targetClass) b.className = targetClass;
      b.title = isAct ? "Bấm để tắt phụ đề" : "Bật phụ đề tiếng Việt cho video này";
    }
    layoutFrame();
    placeStatus();
  }

  function layoutFrame() {
    if (!act || !act.video.isConnected) { frame.style.display = "none"; return; }
    const { l, t, w, h } = contentBox(act.video);
    if (w < 10) { frame.style.display = "none"; return; }
    frame.style.display = "block";
    frame.style.left = l + "px"; frame.style.top = t + "px";
    frame.style.width = w + "px"; frame.style.height = h + "px";
    const info = act.info;
    const key = info && info.chunks ? act.ready.size + "/" + info.chunks.length : "";
    if (key !== act.stripKey) {
      act.stripKey = key;
      if (!info || !info.duration || !info.chunks) { strip.style.background = "rgba(255,255,255,.25)"; return; }
      const stops = info.chunks.map((c) => {
        const a = (c.start / info.duration) * 100, b = (c.end / info.duration) * 100;
        const col = act.ready.has(c.i) ? "#10b981" : "rgba(255,255,255,.25)";
        return `${col} ${a}% ${b}%`;
      });
      strip.style.background = `linear-gradient(to right, ${stops.join(",")})`;
    }
  }

  // ---------- status text ----------
  let statusTimer = null;
  function setStatus(msg, ttl = 0) {
    status.textContent = msg || "";
    status.style.display = msg ? "block" : "none";
    clearTimeout(statusTimer);
    if (msg && ttl) statusTimer = setTimeout(() => setStatus(""), ttl);
    placeStatus();
  }
  function placeStatus() {
    if (status.style.display === "none") return;
    if (act && act.video.isConnected) {
      const { l, t, w } = contentBox(act.video);
      status.style.left = Math.round(l + w / 2 - status.offsetWidth / 2) + "px";
      status.style.top = Math.round(t + 16) + "px";
      return;
    }
    const b = lastVideo && buttons.get(lastVideo);
    if (!b || b.style.display === "none") return;
    const r = b.getBoundingClientRect();
    status.style.left = r.left + "px";
    status.style.top = r.bottom + 8 + "px";
  }
  let lastVideo = null;

  // ---------- subtitle bar (shared position preference) ----------
  let barCfg = { cx: 0.5, cy: 0.86, w: 0.84, fs: 0.036 };
  try { Object.assign(barCfg, JSON.parse(localStorage.getItem(LS_BAR) || "{}")); } catch (_) {}
  const saveBar = () => localStorage.setItem(LS_BAR, JSON.stringify(barCfg));

  function layoutBar() {
    if (!act || !act.video.isConnected) { bar.style.display = "none"; return; }
    const { l, t, w: cw, h: ch } = contentBox(act.video);
    if (cw < 10) return;
    const maxW = cw * (barCfg.w || 0.84);
    bar.style.maxWidth = maxW + "px";
    bar.style.width = "auto";
    bar.style.fontSize = Math.max(14, ch * barCfg.fs) + "px";
    const barW = bar.offsetWidth || 160;
    const barH = bar.offsetHeight || 32;
    bar.style.left = Math.round(l + cw * barCfg.cx - barW / 2) + "px";
    bar.style.top = Math.round(t + ch * barCfg.cy - barH / 2) + "px";
    bar.dataset.box = [l, t, cw, ch].join(",");
  }
  let drag = null;
  bar.addEventListener("pointerdown", (e) => {
    const [l, t, cw, ch] = bar.dataset.box.split(",").map(Number);
    drag = { box: [l, t, cw, ch], dx: e.clientX - (l + cw * barCfg.cx), dy: e.clientY - (t + ch * barCfg.cy) };
    bar.setPointerCapture(e.pointerId);
    e.preventDefault(); e.stopPropagation();
  });
  bar.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const [l, t, cw, ch] = drag.box;
    barCfg.cx = Math.min(1, Math.max(0, (e.clientX - drag.dx - l) / cw));
    barCfg.cy = Math.min(1, Math.max(0, (e.clientY - drag.dy - t) / ch));
    layoutBar();
  });
  bar.addEventListener("pointerup", () => { if (drag) { drag = null; saveBar(); } });
  bar.addEventListener("dblclick", () => { barCfg = { cx: 0.5, cy: 0.86, w: 0.84, fs: 0.036 }; saveBar(); layoutBar(); });
  bar.addEventListener("wheel", (e) => {
    e.preventDefault(); e.stopPropagation();
    barCfg.fs = Math.min(0.08, Math.max(0.018, barCfg.fs * (e.deltaY < 0 ? 1.08 : 0.93)));
    saveBar(); layoutBar();
  }, { passive: false });

  function renderSub() {
    if (!siteEnabled || !act || !act.info || !Array.isArray(act.info.subs)) { bar.style.display = "none"; return; }
    const t = act.video.currentTime;
    const sub = act.info.subs.find((s) => t >= s.start - 0.1 && t <= s.end + 0.45);
    if (sub) {
      if (bar.textContent !== sub.vi) bar.textContent = sub.vi;
      bar.style.display = "inline-block";
      layoutBar();
    } else {
      bar.style.display = "none";
    }
  }

  // ---------- media URL discovery & transcript extraction ----------
  const MEDIA_RE = /\.m3u8|\.mp4|douyinvod|bytevod|zjcdn|\.m4s|\.ts|\.webm|mime_type=video|video_mp4|\/video\/tos\/|googlevideo|videoplayback|cloudfront|coursera|hls|manifest|master/i;
  const NOT_MEDIA_RE = /\.(js|css|jpe?g|png|webp|gif|svg|woff2?|json)(\?|$)|\/(image|img)\//i;

  let resolveSeq = 0;
  let lastInterceptedSubtitles = {};

  function parseVtt(text) {
    if (!text || typeof text !== "string") return [];
    const cues = [];
    const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const timeRegex = /(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})/;
    const parseSec = (h, m, s, ms) => (parseInt(h || "0", 10) * 3600) + (parseInt(m, 10) * 60) + parseInt(s, 10) + (parseInt(ms, 10) / 1000);
    let currentCue = null;
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) {
        if (currentCue && currentCue.text) { cues.push(currentCue); currentCue = null; }
        continue;
      }
      const match = trimmed.match(timeRegex);
      if (match) {
        if (currentCue && currentCue.text) cues.push(currentCue);
        currentCue = { start: parseSec(match[1], match[2], match[3], match[4]), end: parseSec(match[5], match[6], match[7], match[8]), text: "" };
      } else if (currentCue && !trimmed.startsWith("NOTE") && !trimmed.startsWith("WEBVTT")) {
        if (/^\d+$/.test(trimmed)) continue;
        const cleanText = trimmed.replace(/<[^>]+>/g, "").trim();
        if (cleanText) currentCue.text = currentCue.text ? currentCue.text + " " + cleanText : cleanText;
      }
    }
    if (currentCue && currentCue.text) cues.push(currentCue);
    return cues;
  }

  function fetchPageSubtitles() {
    return new Promise((resolve) => {
      const id = ++resolveSeq;
      const done = (e) => {
        if (e.source !== window || !e.data || e.data.rtdub !== "subtitles_resolved" || e.data.id !== id) return;
        window.removeEventListener("message", done);
        if (e.data.subtitles) lastInterceptedSubtitles = e.data.subtitles;
        resolve(e.data.subtitles || {});
      };
      window.addEventListener("message", done);
      window.postMessage({ rtdub: "get_subtitles", id }, "*");
      setTimeout(() => { window.removeEventListener("message", done); resolve(lastInterceptedSubtitles); }, 400);
    });
  }

  async function extractVideoTranscript(v) {
    try {
      if (v.textTracks && v.textTracks.length > 0) {
        const tracks = Array.from(v.textTracks);
        const viTrack = tracks.find((t) => (t.language || "").toLowerCase().startsWith("vi") || /vi[eệ]/i.test(t.label || ""));
        const jaTrack = tracks.find((t) => (t.language || "").toLowerCase().startsWith("ja") || /jap|nhật/i.test(t.label || ""));
        const enTrack = tracks.find((t) => (t.language || "").toLowerCase().startsWith("en") || /eng/i.test(t.label || ""));
        const targetTrack = viTrack || jaTrack || enTrack || tracks[0];
        if (targetTrack) {
          if (targetTrack.mode === "disabled") {
            try { targetTrack.mode = "hidden"; } catch (_) {}
          }
          if (!targetTrack.cues || targetTrack.cues.length === 0) {
            await new Promise((r) => setTimeout(r, 200));
          }
          if (targetTrack.cues && targetTrack.cues.length > 0) {
            const lang = (targetTrack === viTrack ? "vi" : (targetTrack === jaTrack ? "ja" : (targetTrack.language || "ja"))).toLowerCase().slice(0, 2);
            const cues = Array.from(targetTrack.cues).map((c) => ({
              start: Number(c.startTime.toFixed(2)),
              end: Number(c.endTime.toFixed(2)),
              text: (c.text || "").replace(/<[^>]+>/g, "").trim()
            })).filter((c) => c.text);
            if (cues.length > 0) {
              return { lang, cues };
            }
          }
        }
      }

      const trackEls = Array.from(v.querySelectorAll("track"));
      if (trackEls.length > 0) {
        const viEl = trackEls.find((el) => (el.srclang || "").toLowerCase().startsWith("vi") || /vi[eệ]/i.test(el.label || ""));
        const jaEl = trackEls.find((el) => (el.srclang || "").toLowerCase().startsWith("ja") || /jap|nhật/i.test(el.label || ""));
        const targetEl = viEl || jaEl || trackEls[0];
        if (targetEl && targetEl.src) {
          try {
            const res = await fetch(targetEl.src);
            if (res.ok) {
              const text = await res.text();
              const cues = parseVtt(text);
              if (cues.length > 0) {
                const lang = (targetEl === viEl ? "vi" : (targetEl === jaEl ? "ja" : (targetEl.srclang || "ja"))).toLowerCase().slice(0, 2);
                return { lang, cues };
              }
            }
          } catch (_) {}
        }
      }

      const intercepted = await fetchPageSubtitles();
      const subEntries = Object.entries(intercepted);
      if (subEntries.length > 0) {
        const viEntry = subEntries.find(([url]) => /vi[-_]?(vn)?\.(vtt|srt)/i.test(url) || /vietnamese/i.test(url));
        const [, chosenText] = viEntry || subEntries[0];
        const cues = parseVtt(chosenText);
        if (cues.length > 0) {
          const lang = viEntry ? "vi" : "auto";
          return { lang, cues };
        }
      }
    } catch (_) {}
    return null;
  }

  function resolveBlob(blob) {
    return new Promise((resolve) => {
      const id = ++resolveSeq;
      const done = (e) => {
        if (e.source !== window || !e.data || e.data.rtdub !== "resolved" || e.data.id !== id) return;
        window.removeEventListener("message", done);
        if (e.data.subtitles) lastInterceptedSubtitles = e.data.subtitles;
        resolve(e.data);
      };
      window.addEventListener("message", done);
      window.postMessage({ rtdub: "resolve", id, blob }, "*");
      setTimeout(() => { window.removeEventListener("message", done); resolve({ urls: [], manifests: [] }); }, 800);
    });
  }

  async function mediaCandidates(v) {
    if (location.hostname.includes("youtube.com") || location.hostname.includes("youtu.be")) {
      const vidMatch = location.href.match(/[?&]v=([a-zA-Z0-9_-]+)/) || location.pathname.match(/\/shorts\/([a-zA-Z0-9_-]+)/);
      const url = vidMatch ? (location.origin + (vidMatch[0].startsWith("/shorts") ? vidMatch[0] : `/watch?v=${vidMatch[1]}`)) : location.href;
      return { urls: [url], exact: true };
    }

    const direct = [v.currentSrc, v.src, ...[...v.querySelectorAll("source")].map((s) => s.src)]
      .filter((u) => u && !u.startsWith("blob:"));
    if (direct.length) return { urls: [...new Set(direct)], exact: true };

    const blob = v.currentSrc || v.src || "";
    const r = await resolveBlob(blob);
    const hookUrls = r.urls || [];

    const seen = new Set(hookUrls);
    const manifests = [];
    const others = [];
    const entries = performance.getEntriesByType("resource")
      .filter((e) => MEDIA_RE.test(e.name) && !NOT_MEDIA_RE.test(e.name));
    for (const e of entries.reverse()) {
      const u = e.name.startsWith("//") ? location.protocol + e.name : e.name;
      const key = u.split("?")[0];
      if (seen.has(u) || seen.has(key)) continue;
      seen.add(key);
      if (/\.m3u8($|\?)|mpegurl/i.test(u)) manifests.push(u);
      else if (others.length < 6) others.push(u);
    }

    const allUrls = [...new Set([...(r.manifests || []), ...manifests, ...hookUrls, ...others])];
    return { urls: allUrls, exact: hookUrls.length > 0 && manifests.length === 0 };
  }

  // ---------- active subtitle session (never pauses or mutes the video!) ----------
  let act = null;

  function start(v) {
    if (!siteEnabled) return;
    if (act) stop(act);
    const ctx = extractPageContext();
    const localCache = loadLocalSubCache(ctx.videoCode);

    const s = {
      video: v,
      srcKey: sourceOf(v),
      videoCode: ctx.videoCode,
      pageTitle: ctx.pageTitle,
      pageSummary: ctx.pageSummary,
      localCache,
      jobId: null,
      creating: false,
      info: null,
      ready: new Set(),
      lastPoll: 0,
      lastTry: 0,
      lastSeekSent: -1,
      stripKey: "",
      offScreenSince: 0,
      startedAt: performance.now(),
    };

    // Immediately restore any cached subtitles from localStorage so already-translated timestamps show at 0ms!
    if (localCache && Array.isArray(localCache.subs) && localCache.subs.length > 0) {
      s.ready = new Set(localCache.doneChunks || []);
      s.info = {
        state: "running",
        error: "",
        duration: localCache.duration || (isFinite(v.duration) ? v.duration : 0),
        chunks: localCache.chunks || [],
        subs: localCache.subs,
      };
      console.log(
        `[rtdub] Restored ${localCache.subs.length} cached subtitles (${s.ready.size} chunks) from localStorage for ${ctx.videoCode}`
      );
      // If all chunks are already translated in localStorage, we don't even need Whisper or server!
      if (s.info.chunks.length > 0 && s.ready.size >= s.info.chunks.length) {
        s.info.state = "done";
        act = s;
        lastVideo = v;
        setStatus(`Đã tải 100% phụ đề đã lưu cho ${ctx.videoCode}`, 3000);
        syncButtons();
        return;
      }
      setStatus(`Đã tải ${localCache.subs.length} câu phụ đề đã lưu cho ${ctx.videoCode} · Đang dịch tiếp phần còn lại…`);
    } else {
      setStatus(`Đang khởi tạo dịch phụ đề ${ctx.videoCode ? "(" + ctx.videoCode + ") " : ""}…`);
    }

    s.onSeeking = () => {
      maybeSeek(s, v.currentTime);
    };
    v.addEventListener("seeking", s.onSeeking);
    act = s;
    lastVideo = v;
    createJob(s);
    syncButtons();
  }

  function stop(s, msg = "") {
    if (!s) return;
    if (s.videoCode && s.info) {
      saveLocalSubCache(s.videoCode, s.info, s.ready);
    }
    const jobId = s.jobId;
    const v = s.video;
    try {
      v.removeEventListener("seeking", s.onSeeking);
    } catch (_) {}
    if (act === s) act = null;
    s.jobId = null;
    bar.style.display = "none";
    frame.style.display = "none";
    setStatus(msg, msg ? 2500 : 0);
    syncButtons();

    if (jobId) {
      cancelServerJob(jobId);
    }
  }

  async function createJob(s) {
    s.lastTry = performance.now();
    const v = s.video;
    s.creating = true;
    const { urls, exact } = await mediaCandidates(v);
    if (act !== s) { s.creating = false; return; }

    // Refresh page context in case SPA DOM finished rendering title/summary
    const ctx = extractPageContext();
    if (ctx.videoCode) s.videoCode = ctx.videoCode;
    if (ctx.pageTitle) s.pageTitle = ctx.pageTitle;
    if (ctx.pageSummary) s.pageSummary = ctx.pageSummary;

    console.log("[rtdub] code:", s.videoCode, "candidates:", urls, "summary len:", (s.pageSummary || "").length);
    if (!urls.length) {
      s.creating = false;
      if (act === s) setStatus("Đang tìm luồng âm thanh (.m3u8 / media URL)…");
      return;
    }

    const transcriptData = await extractVideoTranscript(v);
    if (act !== s) { s.creating = false; return; }

    try {
      const startSec = v.currentTime > 1.5 ? v.currentTime : 0;
      s.lastSeekSent = startSec;
      const res = await fetch(`${SERVER}/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          urls,
          exact,
          duration: isFinite(v.duration) ? v.duration : 0,
          referer: location.href,
          cookie: document.cookie || "",
          user_agent: navigator.userAgent,
          start: startSec,
          transcript: transcriptData ? transcriptData.cues : null,
          transcript_lang: transcriptData ? transcriptData.lang : null,
          video_code: s.videoCode || null,
          page_title: s.pageTitle || null,
          page_summary: s.pageSummary || null,
          cached_subs: s.localCache ? s.localCache.subs : null,
          cached_done_chunks: s.localCache ? s.localCache.doneChunks : null,
        }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      if (act === s) {
        s.jobId = data.job_id;
        if (data.video_code) s.videoCode = data.video_code;
      } else {
        cancelServerJob(data.job_id);
      }
    } catch (e) {
      console.warn("[rtdub] create job failed", e);
      if (act === s) setStatus("Không kết nối được server local (127.0.0.1:8765)");
    } finally {
      s.creating = false;
    }
  }

  async function poll(s) {
    if (!s.jobId || act !== s) return;
    try {
      const info = await (await fetch(`${SERVER}/jobs/${s.jobId}`)).json();
      if (act !== s) return;
      s.info = info;
      if (info.video_code) s.videoCode = info.video_code;
      s.ready = new Set((info.chunks || []).filter((c) => c.ready).map((c) => c.i));
      if (s.videoCode && info.subs && info.subs.length > 0) {
        saveLocalSubCache(s.videoCode, info, s.ready);
      }
    } catch (_) {
      if (act === s) setStatus("Không kết nối được server local (127.0.0.1:8765)");
    }
  }

  function maybeSeek(s, t) {
    s.lastSeekSent = t;
    if (s.jobId) {
      fetch(`${SERVER}/jobs/${s.jobId}/seek`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t }),
      }).catch(() => {});
    }
  }

  const chunkAt = (s, t) => s.info?.chunks?.find((c) => t >= c.start && t < c.end) || null;

  // ---------- main loop (never pauses or mutes video; catches up automatically) ----------
  function tick() {
    if (!siteEnabled) return;
    const s = act;
    if (!s) return;
    const v = s.video;
    if (!v.isConnected) { stop(s); return; }

    const cur = sourceOf(v);
    if (!s.srcKey && cur) s.srcKey = cur;
    else if (cur && cur !== s.srcKey) {
      const isYt = location.hostname.includes("youtube.com") || location.hostname.includes("youtu.be");
      if (isYt) {
        s.srcKey = cur;
      } else {
        stop(s, "Đã đổi nguồn video");
        return;
      }
    }

    if (isVerticalFeed()) {
      const box = contentBox(v);
      if (box.t + box.h <= 0 || box.t >= innerHeight || box.l + box.w <= 0 || box.l >= innerWidth) {
        stop(s, "Đã cuộn qua video khác");
        return;
      }
    }

    layoutBar();
    layoutFrame();

    const now = performance.now();
    const allDone = s.info && s.info.chunks && s.info.chunks.length > 0 && s.ready.size >= s.info.chunks.length;
    if (!allDone) {
      if (!s.jobId && !s.creating && now - s.lastTry > 1500) createJob(s);
      if (s.jobId && now - s.lastPoll > 600) { s.lastPoll = now; poll(s); }
    }
    const info = s.info;
    if (!info) return;
    if (info.state === "error") { stop(s, "Lỗi server: " + info.error); return; }

    const t = v.currentTime;
    const c = chunkAt(s, t);
    if (!c) return;

    if (!s.ready.has(c.i)) {
      const codePrefix = s.videoCode ? `[${s.videoCode}] ` : "";
      setStatus(`${codePrefix}Đang dịch đuổi theo đoạn ${c.i + 1}/${info.chunks.length}…`);
      if (Math.abs(t - s.lastSeekSent) > 3.0) {
        maybeSeek(s, t);
      }
    } else {
      setStatus("");
    }
  }

  setInterval(syncButtons, 200);
  setInterval(() => { tick(); renderSub(); }, TICK_MS);
  window.addEventListener("resize", () => { if (siteEnabled) { syncButtons(); layoutBar(); } });
})();
