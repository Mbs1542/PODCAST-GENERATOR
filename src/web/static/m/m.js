"use strict";

/* M.B.S Studio, phone app (installable PWA at /m/). Plain JS, hash routes:
     #/              podcasts          #/p/<id>[/<tab>]   one podcast: player, summary, script, transcript, files
     #/tx            transcriptions    #/t/<id>           one transcription: text with search, files
     #/new           new transcription or podcast (file, recording, link)
     #/saved         what is saved on this phone for listening and reading offline
   The same /api as the desktop page, with the same per-launch token. Everything from the server
   or from files is escaped with esc() before it reaches innerHTML. */

let TOKEN = document.querySelector('meta[name="mbs-token"]').content;
const VERSION = document.querySelector('meta[name="mbs-version"]').content;
const app = document.getElementById("app");
const audio = document.getElementById("audio");
const OFFLINE = "mbs-m-offline";
const SPEAKERS = { Roee: "רועי", Noa: "נועה" };
const STATUS = {
  new: ["בתור", "run"], transcribed: ["תמלול מוכן", "run"], plan_ready: ["תוכנית מחכה לך", "run"],
  script_ready: ["תסריט מוכן", "run"], audio_ready: ["פודקאסט מוכן", "good"], video_ready: ["פודקאסט ווידאו", "good"],
};
const NEXT_JOB = { new: "prepare", transcribed: "prepare", plan_ready: "write", script_ready: "audio" };
const NEXT_LABEL = { prepare: "המשך: ניתוח ותוכנית", write: "המשך: כתוב את התסריט", audio: "המשך: הקלט את הפודקאסט" };
const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 0.8];
const CF_LIMIT = 100 * 1024 * 1024;  // Cloudflare's cap on one upload request

// ------------------------------------------------------------------ helpers

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const render = (html) => { app.innerHTML = html; window.scrollTo(0, 0); };
let pollTimer = null;
const later = (fn, ms) => { clearTimeout(pollTimer); pollTimer = setTimeout(fn, ms); };

function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = bad ? "toast bad" : "toast";
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, 3200);
}

function clock(sec) {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}
const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString("he-IL", { day: "numeric", month: "short" }); } catch (_) { return ""; } };
const fmtSize = (b) => b >= 1e9 ? `${(b / 1e9).toFixed(1)}GB` : b >= 1e6 ? `${Math.round(b / 1e6)}MB` : `${Math.max(1, Math.round(b / 1e3))}KB`;

// Each server start makes a new token: on 401, read the current one from our own page and retry once
async function refreshToken() {
  try {
    const html = await (await fetch("/m/", { cache: "no-store" })).text();
    const fresh = new DOMParser().parseFromString(html, "text/html").querySelector('meta[name="mbs-token"]')?.content;
    if (fresh && fresh !== "__MBS_TOKEN__" && fresh !== TOKEN) { TOKEN = fresh; return true; }
  } catch (_) { /* offline */ }
  return false;
}

class Offline extends Error {}

async function authFetch(url, init = {}) {
  const send = () => fetch(url, { ...init, headers: { ...(init.headers || {}), "X-MBS-Token": TOKEN } });
  let res;
  try { res = await send(); } catch (_) { throw new Offline("offline"); }  // no network, or the Access sign-in expired
  if (res.status === 401 && await refreshToken()) res = await send();
  return res;
}

async function api(path, { method = "GET", body } = {}) {
  const headers = body === undefined ? {} : { "Content-Type": "application/json" };
  const res = await authFetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  setOnline(true);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) throw new Error((data && data.detail) || `שגיאה ${res.status}`);
  return data;
}

const fileUrl = (id, name, download = false) => `/api/projects/${enc(id)}/files/${enc(name)}?t=${enc(TOKEN)}${download ? "&download=1" : ""}`;
const mediaUrl = (id, index, download = false) => `/api/media/${enc(id)}/files/${index}?t=${enc(TOKEN)}${download ? "&download=1" : ""}`;
const summaryUrl = (id, format, extra = "") => `/api/projects/${enc(id)}/summary?format=${format}&t=${enc(TOKEN)}${extra}`;

function setOnline(on) {
  $("#net").hidden = on;
  document.body.classList.toggle("offline", !on);
}

// ------------------------------------------------------------------ saved on the phone (Cache API)
// Keys: /m/offline/index.json, /m/offline/p/<id>/{meta.json,audio,summary.json,transcript.txt},
//       /m/offline/t/<id>/{meta.json,transcript.txt}. The worker serves them, audio with Range.

const offKey = (kind, id, name) => `/m/offline/${kind}/${encodeURIComponent(id)}/${name}`;
const hasCaches = "caches" in window;

async function offIndex() {
  if (!hasCaches) return {};
  const hit = await (await caches.open(OFFLINE)).match("/m/offline/index.json");
  return hit ? hit.json() : {};
}
async function offWriteIndex(index) {
  await (await caches.open(OFFLINE)).put("/m/offline/index.json", new Response(JSON.stringify(index), { headers: { "Content-Type": "application/json" } }));
}
async function offGet(kind, id, name, as = "json") {
  if (!hasCaches) return null;
  const hit = await (await caches.open(OFFLINE)).match(offKey(kind, id, name));
  return hit ? (as === "json" ? hit.json() : hit.text()) : null;
}
async function offPut(kind, id, name, body, type) {
  await (await caches.open(OFFLINE)).put(offKey(kind, id, name), new Response(body, { headers: { "Content-Type": type } }));
}
async function offRemove(kind, id) {
  const cache = await caches.open(OFFLINE);
  for (const req of await cache.keys()) {
    if (new URL(req.url).pathname.startsWith(`/m/offline/${kind}/${encodeURIComponent(id)}/`)) await cache.delete(req);
  }
  const index = await offIndex();
  delete index[`${kind}:${id}`];
  await offWriteIndex(index);
}
const workerReady = () => !!navigator.serviceWorker?.controller;

async function fetchText(url) {
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`שגיאה ${res.status}`);
  return res.text();
}

// Download with progress, into memory, then into the cache
async function fetchBlob(url, onFraction) {
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`ההורדה נכשלה (${res.status})`);
  const total = Number(res.headers.get("Content-Length")) || 0;
  if (!res.body || !total) return res.blob();
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    onFraction(got / total);
  }
  return new Blob(chunks, { type: res.headers.get("Content-Type") || "audio/mpeg" });
}

async function saveProject(p, script, onFraction) {
  if (!hasCaches) throw new Error("הדפדפן הזה לא מאפשר שמירה בטלפון");
  try { await navigator.storage?.persist?.(); } catch (_) { /* best effort */ }
  let size = 0;
  if (p.files.includes("audio")) {
    const blob = await fetchBlob(fileUrl(p.id, "audio"), onFraction);
    size += blob.size;
    await offPut("p", p.id, "audio", blob, blob.type || "audio/mpeg");
  }
  const summary = await api(`/projects/${enc(p.id)}/summary?format=json`).catch(() => null);
  if (summary) await offPut("p", p.id, "summary.json", JSON.stringify(summary), "application/json");
  const transcript = await projectTranscript(p).catch(() => "");
  if (transcript) await offPut("p", p.id, "transcript.txt", transcript, "text/plain; charset=utf-8");
  await offPut("p", p.id, "meta.json", JSON.stringify({ project: p, script }), "application/json");
  const index = await offIndex();
  index[`p:${p.id}`] = { kind: "p", id: p.id, title: p.title, created: p.created, minutes: p.minutes, size,
    audio: p.files.includes("audio"), summary: !!summary, transcript: !!transcript, saved: new Date().toISOString() };
  await offWriteIndex(index);
}

async function saveTranscription(item, text) {
  if (!hasCaches) throw new Error("הדפדפן הזה לא מאפשר שמירה בטלפון");
  await offPut("t", item.id, "transcript.txt", text, "text/plain; charset=utf-8");
  await offPut("t", item.id, "meta.json", JSON.stringify({ item }), "application/json");
  const index = await offIndex();
  index[`t:${item.id}`] = { kind: "t", id: item.id, title: item.title, created: item.created, size: new Blob([text]).size, saved: new Date().toISOString() };
  await offWriteIndex(index);
}

// ------------------------------------------------------------------ transcripts as plain text

function srtToText(srt) {
  const lines = srt.replace(/\r/g, "").split("\n")
    .filter((l) => l.trim() && !/^\d+$/.test(l.trim()) && !/-->/.test(l));
  // Join the short subtitle lines into paragraphs of about five sentences
  const paragraphs = [];
  let cur = [];
  for (const line of lines) {
    cur.push(line.trim());
    if (cur.length >= 6 && /[.?!:]$/.test(line.trim())) { paragraphs.push(cur.join(" ")); cur = []; }
  }
  if (cur.length) paragraphs.push(cur.join(" "));
  return paragraphs.join("\n\n");
}

async function projectTranscript(p) {
  if (p.files.includes("transcript_txt")) return fetchText(fileUrl(p.id, "transcript_txt"));
  if (p.files.includes("transcript_srt")) return srtToText(await fetchText(fileUrl(p.id, "transcript_srt")));
  return "";
}

async function itemTranscript(item) {
  const text = item.files.findIndex((f) => f.kind === "text");
  if (text >= 0) return fetchText(mediaUrl(item.id, text));
  const subs = item.files.findIndex((f) => f.kind === "subtitles");
  if (subs >= 0) return srtToText(await fetchText(mediaUrl(item.id, subs)));
  return "";
}

// ------------------------------------------------------------------ the player (one <audio>, kept across pages)

const player = { id: null, title: "", chapters: [], starts: [], turns: [], onTick: null };
const posKey = (id) => `mbs-m-pos:${id}`;
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (_) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode */ } },
  del(key) { try { localStorage.removeItem(key); } catch (_) { /* private mode */ } },
};

async function loadEpisode(p, script, { autoplay = false } = {}) {
  if (player.id === p.id) { if (autoplay) audio.play().catch(() => {}); return; }
  const saved = (await offIndex())[`p:${p.id}`];
  const src = saved?.audio && workerReady() ? offKey("p", p.id, "audio") : fileUrl(p.id, "audio");
  savePosition();
  player.id = p.id;
  player.title = p.title;
  player.chapters = script?.timing?.chapters || [];
  player.starts = script?.timing?.turn_starts || [];
  player.turns = script?.dialogue?.dialogue || [];
  audio.src = src;
  audio.playbackRate = store.get("mbs-m-speed", 1);
  const resume = store.get(posKey(p.id), null);
  player.ready = false;  // until the saved position is applied, the 0 of a loading file is not a position
  audio.addEventListener("loadedmetadata", () => {
    if (resume && !resume.done && resume.t > 5 && resume.t < audio.duration - 5) audio.currentTime = resume.t;
    audio.playbackRate = store.get("mbs-m-speed", 1);
    player.ready = true;
  }, { once: true });
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: p.title, artist: "M.B.S Studio", album: "פודקאסט מהשיעור",
      artwork: [{ src: "/static/m/icon-512.png", sizes: "512x512", type: "image/png" }],
    });
  }
  paintMini();
  if (autoplay) audio.play().catch(() => {});
}

function chapterIndex(t = audio.currentTime) {
  let index = -1;
  player.chapters.forEach((c, i) => { if (c.start <= t + 0.25) index = i; });
  return index;
}
function turnIndex(t = audio.currentTime) {
  let index = -1;
  for (let i = 0; i < player.starts.length && player.starts[i] <= t; i++) index = i;
  return index;
}
function skip(seconds) { audio.currentTime = Math.max(0, Math.min((audio.duration || 0) - 0.5, audio.currentTime + seconds)); }
function toChapter(step) {
  const i = chapterIndex();
  // "Previous" in the first 3 seconds of a chapter goes to the one before it
  const target = step < 0 && i >= 0 && audio.currentTime - player.chapters[i].start > 3 ? i : i + step;
  if (target < 0) audio.currentTime = 0;
  else if (player.chapters[target]) audio.currentTime = player.chapters[target].start;
}

if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  const set = (action, fn) => { try { ms.setActionHandler(action, fn); } catch (_) { /* unsupported */ } };
  set("play", () => audio.play());
  set("pause", () => audio.pause());
  set("seekbackward", (d) => skip(-(d.seekOffset || 15)));
  set("seekforward", (d) => skip(d.seekOffset || 30));
  set("seekto", (d) => { audio.currentTime = d.seekTime; });
  set("previoustrack", () => toChapter(-1));
  set("nexttrack", () => toChapter(1));
}

function savePosition() {
  if (player.id && player.ready && audio.duration) store.set(posKey(player.id), { t: audio.currentTime, d: audio.duration });
}

let lastSave = 0;
audio.addEventListener("timeupdate", () => {
  if (!player.id) return;
  const now = Date.now();
  if (now - lastSave > 4000) {
    lastSave = now;
    savePosition();
    if ("mediaSession" in navigator && audio.duration) {
      try { navigator.mediaSession.setPositionState({ duration: audio.duration, position: audio.currentTime, playbackRate: audio.playbackRate }); } catch (_) { /* old iOS */ }
    }
  }
  const fill = $("#mini-fill");
  if (fill && audio.duration) fill.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
  if (player.onTick) player.onTick();
});
audio.addEventListener("pause", () => { savePosition(); paintMini(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) savePosition(); });
audio.addEventListener("play", paintMini);
audio.addEventListener("ended", () => {
  if (player.id) store.set(posKey(player.id), { t: 0, d: audio.duration || 0, done: true });
  paintMini();
});
audio.addEventListener("error", () => { if (player.id) toast("ההשמעה נכשלה. בדוק את החיבור ונסה שוב.", true); });

function paintMini() {
  const mini = $("#mini");
  const onEpisode = location.hash.startsWith(`#/p/${enc(player.id || "")}`);
  mini.hidden = !player.id || onEpisode;
  document.body.classList.toggle("has-mini", !mini.hidden);
  $("#mini-play").textContent = audio.paused ? "▶" : "❚❚";
  $("#mini-play").setAttribute("aria-label", audio.paused ? "נגן" : "השהה");
  $("#mini-title").textContent = player.title;
  $("#mini-title").href = $("#mini-art").href = `#/p/${enc(player.id || "")}`;
  if (player.id) $("#mini-art").style.setProperty("--h", hue(player.id));
  // The equalizer on the cover moves only while this page's own episode is the one playing
  $$(".player").forEach((card) => card.classList.toggle("live", card.dataset.id === player.id && !audio.paused));
  if (player.onTick) player.onTick();
}

// A stable color per podcast or transcription, from its id
function hue(id) {
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.codePointAt(0)) % 360;
  return h;
}
$("#mini-play").onclick = () => (audio.paused ? audio.play() : audio.pause());

// ------------------------------------------------------------------ router

async function route() {
  clearTimeout(pollTimer);
  player.onTick = null;
  player.seekHere = null;
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const [page, id, tab] = parts;
  const section = page === "p" || !page ? "podcasts" : page === "t" ? "tx" : page;
  $$(".tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === section));
  paintMini();
  try {
    if (!page) await viewPodcasts();
    else if (page === "p" && id) await viewEpisode(id, tab);
    else if (page === "tx") await viewTranscriptions();
    else if (page === "t" && id) await viewTranscription(id);
    else if (page === "new") viewNew();
    else if (page === "saved") await viewSaved();
    else location.hash = "#/";
  } catch (err) {
    if (err instanceof Offline) return offlineFallback(page, id, tab);
    render(`<div class="card stack"><p>${esc(err.message)}</p><button class="btn" onclick="route()">נסה שוב</button></div>`);
  }
}
window.addEventListener("hashchange", route);

async function offlineFallback(page, id, tab) {
  setOnline(false);
  if (page === "p" && id && await offGet("p", id, "meta.json")) return viewEpisode(id, tab, true);
  if (page === "t" && id && await offGet("t", id, "meta.json")) return viewTranscription(id, true);
  await viewSaved(true);
}

// ------------------------------------------------------------------ podcasts

function statusBadge(p) {
  if (p.error) return `<span class="badge bad">נעצר</span>`;
  if (p.running) return `<span class="badge run">${esc(p.stage || "עובד")}${p.progress != null ? ` ${Math.round(p.progress * 100)}%` : ""}</span>`;
  const [label, cls] = STATUS[p.status] || [p.status, ""];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function listenedBadge(id) {
  const pos = store.get(posKey(id), null);
  if (!pos) return "";
  if (pos.done) return `<span class="badge good">הושמע</span>`;
  return pos.d ? `<span class="badge">${clock(pos.d - pos.t)} נשארו</span>` : "";
}

async function viewPodcasts() {
  const [{ projects }, index] = await Promise.all([api("/projects"), offIndex()]);
  const rows = projects.map((p) => `
    <a class="card item" href="#/p/${enc(p.id)}">
      <div class="art" style="--h:${hue(p.id)}" aria-hidden="true">${p.files.includes("audio") ? "🎧" : p.running ? "⏳" : "📄"}</div>
      <div class="body">
        <div class="title"><bdi>${esc(p.title)}</bdi></div>
        <div class="meta row">${statusBadge(p)}${p.minutes ? `<span>${Math.round(p.minutes)} דק׳</span>` : ""}
          <span>${esc(fmtDate(p.created))}</span>${index[`p:${p.id}`] ? `<span class="badge good">⬇ בטלפון</span>` : ""}${listenedBadge(p.id)}</div>
        ${p.running && p.progress != null ? `<div class="progress"><div style="width:${Math.round(p.progress * 100)}%"></div></div>` : ""}
      </div>
    </a>`).join("");
  render(`<h1>פודקאסטים</h1>
    ${projects.length ? `<div class="list">${rows}</div>` : `<div class="empty"><div class="big">🎙️</div>
      <p>עוד אין פודקאסטים. העלה הרצאה, הקלטה או קישור, והמערכת תתמלל ותכין ממנה פודקאסט.</p>
      <a class="btn primary" href="#/new">פודקאסט חדש</a></div>`}`);
  if (projects.some((p) => p.running)) later(() => location.hash === "#/" || !location.hash ? viewPodcasts().catch(() => {}) : null, 4000);
}

// ------------------------------------------------------------------ one podcast

async function viewEpisode(id, tab, offline = false) {
  let p, script, saved = null;
  if (offline) {
    saved = await offGet("p", id, "meta.json");
    ({ project: p, script } = saved);
  } else {
    p = await api(`/projects/${enc(id)}`);
    script = await api(`/projects/${enc(id)}/script`).catch(() => null);
  }
  const index = await offIndex();
  const isSaved = !!index[`p:${id}`];
  const hasAudio = p.files.includes("audio") && (!offline || index[`p:${id}`]?.audio);
  const tabs = [
    ["summary", "סיכום", p.files.includes("audio") || p.files.includes("summary_html") || ["plan_ready", "script_ready"].includes(p.status)],
    ["script", "התסריט", !!script?.dialogue?.dialogue?.length],
    ["chapters", "פרקים", !!script?.timing?.chapters?.length],
    ["transcript", "תמלול השיעור", p.files.includes("transcript_txt") || p.files.includes("transcript_srt")],
    ["files", "הורדות", !offline],
  ].filter(([, , ok]) => ok);
  const active = tabs.some(([k]) => k === tab) ? tab : tabs[0]?.[0];
  const next = NEXT_JOB[p.status];

  render(`
    <a href="#/" class="small">→ כל הפודקאסטים</a>
    <h1><bdi>${esc(p.title)}</bdi></h1>
    <div class="row muted small">${statusBadge(p)}<span>${esc(fmtDate(p.created))}</span>${p.minutes ? `<span>${Math.round(p.minutes)} דק׳</span>` : ""}</div>
    <div class="stack" style="margin-top:12px">
      ${hasAudio ? playerCard(p) : ""}
      ${!offline && (p.running || p.error || (next && !p.files.includes("audio"))) ? progressCard(p, next) : ""}
      ${!offline && p.files.includes("audio") ? saveCard(p, isSaved) : ""}
      ${tabs.length ? `<div class="tabs" role="tablist">${tabs.map(([k, label]) =>
        `<button data-pane="${k}" class="${k === active ? "on" : ""}">${label}</button>`).join("")}</div>
        <div id="tab-body"><div class="muted">טוען...</div></div>` : ""}
    </div>`);

  player.seekHere = null;
  if (hasAudio) await bindPlayer(p, script);
  bindProgress(p, next);
  bindSave(p, script);
  $$("[data-pane]").forEach((b) => {
    b.onclick = () => {  // replaceState: switching tabs neither reloads the page nor fills the back button
      history.replaceState(null, "", `#/p/${enc(p.id)}/${b.dataset.pane}`);
      paintTab(p, script, b.dataset.pane, offline);
    };
  });
  if (active) await paintTab(p, script, active, offline);
  if (!offline && p.running) later(() => location.hash.startsWith(`#/p/${enc(p.id)}`) && refreshEpisode(p.id), 3000);
}

async function refreshEpisode(id) {
  try {
    const p = await api(`/projects/${enc(id)}`);
    if (!p.running) return route();  // finished a stage: draw everything again
    const card = $("#progress-card");
    if (card) card.outerHTML = progressCard(p, NEXT_JOB[p.status]);
    bindProgress(p, NEXT_JOB[p.status]);
    later(() => location.hash.startsWith(`#/p/${enc(id)}`) && refreshEpisode(id), 3000);
  } catch (_) { later(() => refreshEpisode(id), 6000); }
}

function progressCard(p, next) {
  const pct = p.progress != null ? Math.round(p.progress * 100) : null;
  return `<div class="card" id="progress-card">
    ${p.error ? `<p style="color:var(--bad);margin-top:0">${esc(p.error)}</p>` : ""}
    ${p.running ? `<div class="row spread"><b>${esc(p.stage || "עובד...")}</b><span class="muted">${pct != null ? `${pct}%` : ""}</span></div>
      <div class="progress"><div style="width:${pct ?? 5}%"></div></div>
      <p class="muted small" style="margin-bottom:0">אפשר לסגור את האפליקציה. העבודה ממשיכה בשרת.</p>`
      : next ? `${p.status === "plan_ready" ? `<p class="muted small" style="margin-top:0">התוכנית לפרק מוכנה. אפשר לערוך אותה במחשב, או להמשיך מכאן כמו שהיא.</p>` : ""}
      <button class="btn primary block" id="next-job">${esc(NEXT_LABEL[next] || "המשך")}</button>` : ""}
  </div>`;
}

function bindProgress(p, next) {
  const button = $("#next-job");
  if (!button) return;
  button.onclick = async () => {
    button.disabled = true;
    try { await api(`/projects/${enc(p.id)}/jobs/${next}`, { method: "POST", body: {} }); route(); } catch (err) { button.disabled = false; toast(err.message, true); }
  };
}

function saveCard(p, isSaved) {
  return `<div class="card row spread" id="save-card">
    <div><b>${isSaved ? "שמור בטלפון" : "האזנה בלי אינטרנט"}</b>
      <div class="muted small" id="save-note">${isSaved ? "הפודקאסט, הסיכום והתמלול זמינים גם בלי חיבור." : "שומר את הפודקאסט, הסיכום והתמלול בטלפון."}</div>
      <div class="progress" id="save-bar" hidden><div style="width:0%"></div></div></div>
    <button class="btn ${isSaved ? "danger" : "primary"}" id="save-btn">${isSaved ? "הסר" : "⬇ שמור"}</button>
  </div>`;
}

function bindSave(p, script) {
  const button = $("#save-btn");
  if (!button) return;
  button.onclick = async () => {
    const index = await offIndex();
    if (index[`p:${p.id}`]) {
      await offRemove("p", p.id);
      if (player.id === p.id) { const t = audio.currentTime, playing = !audio.paused; player.id = null; await loadEpisode(p, script); audio.currentTime = t; if (playing) audio.play(); }
      toast("הוסר מהטלפון");
      return route();
    }
    button.disabled = true;
    const bar = $("#save-bar");
    bar.hidden = false;
    try {
      await saveProject(p, script, (f) => { bar.firstElementChild.style.width = `${Math.round(f * 100)}%`; });
      toast("נשמר בטלפון");
      route();
    } catch (err) {
      button.disabled = false;
      bar.hidden = true;
      toast(err.message, true);
    }
  };
}

function playerCard(p) {
  return `<div class="card player" data-id="${esc(p.id)}" style="--h:${hue(p.id)}">
    <div class="cover" aria-hidden="true">🎧<div class="eq"><i></i><i></i><i></i><i></i></div></div>
    <input type="range" class="scrub" id="scrub" min="0" max="1000" value="0" aria-label="מיקום בפרק">
    <div class="times"><span id="t-now">0:00</span><span id="t-left">-0:00</span></div>
    <div class="controls">
      <button class="skip" id="back" type="button" aria-label="15 שניות אחורה">↺15</button>
      <button class="play" id="play" type="button" aria-label="נגן">▶</button>
      <button class="skip" id="fwd" type="button" aria-label="30 שניות קדימה">30↻</button>
    </div>
    <div class="row" style="justify-content:center">
      <button class="btn speed" id="speed" type="button" aria-label="מהירות"></button>
      <button class="btn" id="prev-ch" type="button" aria-label="פרק קודם" title="פרק קודם">⏮ פרק</button>
      <button class="btn" id="next-ch" type="button" aria-label="פרק הבא" title="פרק הבא">פרק ⏭</button>
    </div>
    <div class="now" id="now"></div>
  </div>`;
}

// This page's episode owns the one <audio> only once it is loaded: opening a page while another
// episode plays leaves that one playing until this page's play button (or a seek) takes over.
const mine = (p) => player.id === p.id;
async function takeOver(p, script) { if (!mine(p)) await loadEpisode(p, script); }

async function bindPlayer(p, script) {
  if (!player.id || audio.paused) await loadEpisode(p, script);
  const chapters = script?.timing?.chapters || [];
  const scrub = $("#scrub");
  let dragging = false;
  scrub.oninput = () => { dragging = true; if (mine(p)) $("#t-now").textContent = clock((scrub.value / 1000) * (audio.duration || 0)); };
  scrub.onchange = async () => {
    dragging = false;
    await takeOver(p, script);
    if (audio.duration) audio.currentTime = (scrub.value / 1000) * audio.duration;
  };
  $("#play").onclick = async () => {
    await takeOver(p, script);
    if (audio.paused) audio.play().catch((e) => toast(e.message, true)); else audio.pause();
  };
  $("#back").onclick = () => mine(p) && skip(-15);
  $("#fwd").onclick = () => mine(p) && skip(30);
  $("#prev-ch").onclick = () => mine(p) && toChapter(-1);
  $("#next-ch").onclick = () => mine(p) && toChapter(1);
  player.seekHere = async (seconds) => {  // a chapter, a line of the script
    await takeOver(p, script);
    audio.currentTime = seconds;
    audio.play().catch(() => {});
  };
  const speed = $("#speed");
  const paintSpeed = () => { speed.textContent = `×${store.get("mbs-m-speed", 1)}`; };
  speed.onclick = () => {
    const now = store.get("mbs-m-speed", 1);
    const next = SPEEDS[(SPEEDS.indexOf(now) + 1) % SPEEDS.length];
    store.set("mbs-m-speed", next);
    audio.playbackRate = next;
    paintSpeed();
  };
  paintSpeed();
  $("#prev-ch").hidden = $("#next-ch").hidden = chapters.length < 2;

  let shownTurn = -2, shownChapter = -2;
  player.onTick = (force = false) => {
    if (!$("#scrub")) return;
    if (force) { shownTurn = -2; shownChapter = -2; }
    if (!mine(p)) {  // another episode is playing: show where this one was left
      const pos = store.get(posKey(p.id), null);
      $("#play").textContent = "▶";
      $("#t-now").textContent = clock(pos?.t || 0);
      $("#t-left").textContent = pos?.d ? `-${clock(pos.d - (pos.t || 0))}` : "";
      if (pos?.d && !dragging) scrub.value = String(Math.round(((pos.t || 0) / pos.d) * 1000));
      return;
    }
    const d = audio.duration || 0, t = audio.currentTime;
    if (!dragging && d) scrub.value = String(Math.round((t / d) * 1000));
    $("#t-now").textContent = clock(t);
    $("#t-left").textContent = `-${clock(d - t)}`;
    $("#play").textContent = audio.paused ? "▶" : "❚❚";
    $("#play").setAttribute("aria-label", audio.paused ? "נגן" : "השהה");
    const ti = turnIndex(t);
    if (ti !== shownTurn) {
      shownTurn = ti;
      const turn = player.turns[ti];
      $("#now").innerHTML = turn ? `<b>${esc(SPEAKERS[turn.speaker] || turn.speaker)}:</b> ${esc(turn.text)}` : "";
      $$(".turn").forEach((el) => el.classList.toggle("on", Number(el.dataset.i) === ti));
      const follow = $("#follow");
      if (follow?.checked && !audio.paused) $(`.turn[data-i="${ti}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    const ci = chapterIndex(t);
    if (ci !== shownChapter) {
      shownChapter = ci;
      $$(".chapters button").forEach((b) => b.classList.toggle("on", Number(b.dataset.i) === ci));
    }
  };
  player.onTick();
}

// ------------------------------------------------------------------ the tabs

async function paintTab(p, script, tab, offline) {
  $$("[data-pane]").forEach((b) => b.classList.toggle("on", b.dataset.pane === tab));
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="muted">טוען...</div>`;
  try {
    if (tab === "summary") body.innerHTML = summaryHtml(offline ? await offGet("p", p.id, "summary.json") : await api(`/projects/${enc(p.id)}/summary?format=json`));
    if (tab === "script") paintScript(body, script);
    if (tab === "chapters") paintChapters(body, script);
    if (tab === "transcript") paintReader(body, offline ? await offGet("p", p.id, "transcript.txt", "text") : await projectTranscript(p));
    if (tab === "files") body.innerHTML = filesHtml(p, script);
    bindSeek(body);
  } catch (err) {
    body.innerHTML = `<div class="card muted">${esc(err instanceof Offline ? "אין חיבור, והחלק הזה לא נשמר בטלפון." : err.message)}</div>`;
  }
  if (player.onTick) player.onTick(true);  // mark the current line and chapter in what was just drawn
}

function bindSeek(root) {
  $$("[data-seek]", root).forEach((el) => {
    el.onclick = () => {
      const seconds = el.dataset.seek === "" ? NaN : Number(el.dataset.seek);
      if (player.seekHere && Number.isFinite(seconds)) player.seekHere(seconds);
    };
  });
}

function summaryHtml(m) {
  if (!m) return `<div class="card muted">הסיכום לא נשמר בטלפון.</div>`;
  const list = (items, cls = "") => `<ul class="${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  const section = (title, inner) => `<section><h2>${title}</h2>${inner}</section>`;
  const imp = { 3: "חשוב מאוד", 2: "חשוב", 1: "רקע" };
  const parts = [];
  if (m.summary) parts.push(section("בקצרה", `<div class="card tldr">${esc(m.summary)}</div>`));
  if (m.objectives?.length) parts.push(section("מה לומדים בשיעור", `<div class="card">${list(m.objectives)}</div>`));
  if (m.concepts?.length) parts.push(section(`מושגי מפתח (${m.concepts.length})`, `<div class="card">${m.concepts.map((c) =>
    `<div class="concept"><b>${esc(c.name)}<span class="imp i${c.importance}">${imp[c.importance] || ""}</span></b><p>${esc(c.definition)}</p></div>`).join("")}</div>`));
  if (m.facts?.length) parts.push(section("עובדות חשובות", `<div class="card">${list(m.facts)}</div>`));
  if (m.examples?.length) parts.push(section("דוגמאות מהשיעור", `<div class="card">${list(m.examples.map((e) => e.description + (e.illustrates ? ` (ממחיש: ${e.illustrates})` : "")))}</div>`));
  if (m.misconceptions?.length) parts.push(section("טעויות נפוצות", `<div class="card">${list(m.misconceptions, "warnlist")}</div>`));
  if (m.arguments?.length) parts.push(section("טיעונים", `<div class="card">${m.arguments.map((a) => `<div class="concept"><b>${esc(a.claim)}</b>
    ${a.support ? `<p>${esc(a.support)}</p>` : ""}${a.objections?.length ? `<p>התנגדויות: ${esc(a.objections.join("; "))}</p>` : ""}</div>`).join("")}</div>`));
  if (m.chapters?.length) parts.push(section("פרקי הפודקאסט", `<div class="card chapters">${m.chapters.map((c) =>
    `<button data-seek="${c.start ?? ""}" ${c.start == null ? "disabled" : ""}><bdi>${esc(c.title)}</bdi><span class="time">${c.start == null ? "" : clock(c.start)}</span></button>`).join("")}</div>`));
  if (m.self_check?.length) parts.push(section("בדיקה עצמית", `<div class="card">${m.self_check.map((q) =>
    `<details><summary>מה זה ${esc(q.name)}?</summary><p>${esc(q.answer)}</p></details>`).join("")}</div>`));
  if (m.open_questions?.length) parts.push(section("שאלות פתוחות", `<div class="card">${list(m.open_questions)}</div>`));
  return `<div class="sum reader">${parts.join("") || `<div class="card muted">הסיכום ריק.</div>`}</div>`;
}

function paintScript(body, script) {
  const turns = script?.dialogue?.dialogue || [];
  const starts = script?.timing?.turn_starts || [];
  const chapters = {};
  (script?.timing?.chapters || []).forEach((c) => { if (c.start_index != null) chapters[c.start_index] = c.title; });
  body.innerHTML = `
    ${player.id ? `<label class="row small muted" style="margin-bottom:8px"><input type="checkbox" id="follow" checked> גלול עם ההשמעה</label>` : ""}
    <div class="reader">${turns.map((t, i) => `${chapters[i] ? `<h2 style="margin-top:16px">${esc(chapters[i])}</h2>` : ""}
      <div class="turn" data-i="${i}" ${starts[i] != null ? `data-seek="${starts[i]}"` : ""}><b>${esc(SPEAKERS[t.speaker] || t.speaker)}:</b> ${esc(t.text)}</div>`).join("")}</div>`;
}

function paintChapters(body, script) {
  body.innerHTML = `<div class="card chapters">${(script?.timing?.chapters || []).map((c, i) =>
    `<button data-i="${i}" data-seek="${c.start}"><bdi>${esc(c.title)}</bdi><span class="time">${clock(c.start)}</span></button>`).join("")}</div>`;
}

// Text with an in-page search: highlights every match and steps between them
function paintReader(body, text) {
  if (!text) { body.innerHTML = `<div class="card muted">אין תמלול.</div>`; return; }
  const paragraphs = text.replace(/\r/g, "").split(/\n\s*\n|\n/).map((s) => s.trim()).filter(Boolean);
  body.innerHTML = `
    <div class="findbar"><input type="search" id="find" placeholder="חיפוש בתמלול" enterkeyhint="search" autocomplete="off">
      <span class="count" id="find-count"></span>
      <button type="button" id="find-prev" aria-label="הקודם">▲</button><button type="button" id="find-next" aria-label="הבא">▼</button></div>
    <div class="row small muted" style="margin:4px 0 10px">גודל טקסט
      <button class="btn" data-size="-1" type="button" aria-label="טקסט קטן יותר">א-</button><button class="btn" data-size="1" type="button" aria-label="טקסט גדול יותר">א+</button></div>
    <div class="reader card" id="reader"></div>`;
  const reader = $("#reader", body);
  const plain = paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  reader.innerHTML = plain;
  let marks = [], at = -1;
  const show = (i) => {
    if (!marks.length) return;
    at = (i + marks.length) % marks.length;
    marks.forEach((m, k) => m.classList.toggle("cur", k === at));
    marks[at].scrollIntoView({ block: "center", behavior: "smooth" });
    $("#find-count").textContent = `${at + 1}/${marks.length}`;
  };
  let timer = null;
  $("#find", body).oninput = (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = e.target.value.trim();
      if (q.length < 2) { reader.innerHTML = plain; marks = []; $("#find-count").textContent = ""; return; }
      const pattern = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      reader.innerHTML = paragraphs.map((p) => `<p>${p.split(pattern).map(esc).join(`<mark>${esc(q)}</mark>`)}</p>`).join("");
      // the split drops the original casing of each match; put the real text back
      let k = 0;
      const found = [...paragraphs.join("\n").matchAll(pattern)];
      marks = $$("mark", reader);
      marks.forEach((m) => { m.textContent = found[k++]?.[0] ?? m.textContent; });
      $("#find-count").textContent = marks.length ? `${marks.length}` : "0";
      if (marks.length) show(0);
    }, 200);
  };
  $("#find", body).onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); show(at + 1); e.target.blur(); } };
  $("#find-next", body).onclick = () => show(at + 1);
  $("#find-prev", body).onclick = () => show(at - 1);
  $$("[data-size]", body).forEach((b) => {
    b.onclick = () => {
      const size = Math.min(26, Math.max(14, store.get("mbs-m-read", 17) + Number(b.dataset.size)));
      store.set("mbs-m-read", size);
      document.documentElement.style.setProperty("--read-size", `${size}px`);
    };
  });
}

function filesHtml(p, script) {
  const link = (href, label) => `<a class="btn" href="${href}" download>${label}</a>`;
  const out = [];
  if (p.files.includes("audio")) out.push(link(fileUrl(p.id, "audio", true), "🎧 פודקאסט MP3"));
  if (p.files.includes("video")) out.push(link(fileUrl(p.id, "video", true), "🎬 וידאו MP4"));
  if (p.files.includes("audio") || p.files.includes("summary_html")) {
    out.push(`<a class="btn" href="${summaryUrl(p.id, "html", "&print=1")}" target="_blank" rel="noopener">📄 סיכום PDF</a>`);
    out.push(link(summaryUrl(p.id, "docx", "&download=1"), "📝 סיכום Word"));
  }
  if (p.files.includes("transcript_txt")) out.push(link(fileUrl(p.id, "transcript_txt", true), "תמלול (טקסט)"));
  if (p.files.includes("transcript_srt")) out.push(link(fileUrl(p.id, "transcript_srt", true), "כתוביות SRT"));
  if (script) out.push(link(`/api/projects/${enc(p.id)}/script.txt?t=${enc(TOKEN)}`, "התסריט (טקסט)"));
  return `<div class="card stack"><div class="files">${out.join("")}</div>
    <p class="muted small" style="margin:0">ההורדה נשמרת בתיקיית ההורדות של הטלפון (ב-iPhone: אפליקציית "קבצים").</p>
    <a class="btn block" href="/#/p/${enc(p.id)}">פתח במסך המלא (עריכה, וידאו)</a></div>`;
}

// ------------------------------------------------------------------ transcriptions (the Tools screen's items)

function itemBadge(item) {
  if (item.status === "done") return `<span class="badge good">מוכן</span>`;
  if (item.status === "error") return `<span class="badge bad">נכשל</span>`;
  if (item.status === "cancelled") return `<span class="badge">בוטל</span>`;
  return `<span class="badge run">${esc(item.stage || "בתור")}${item.progress != null ? ` ${Math.round(item.progress * 100)}%` : ""}</span>`;
}

async function viewTranscriptions() {
  const [{ items }, index] = await Promise.all([api("/media"), offIndex()]);
  const shown = items.filter((i) => i.kind === "transcription" || (i.kind === "download" && i.options?.transcribe && i.status !== "done"));
  const rows = shown.map((i) => `
    <a class="card item" href="#/t/${enc(i.id)}">
      <div class="art" style="--h:${hue(i.id)}" aria-hidden="true">${i.kind === "download" ? "⬇" : i.status === "done" ? "📝" : "⏳"}</div>
      <div class="body"><div class="title"><bdi>${esc(i.title)}</bdi></div>
        <div class="meta row">${itemBadge(i)}<span>${esc(fmtDate(i.created))}</span>${index[`t:${i.id}`] ? `<span class="badge good">⬇ בטלפון</span>` : ""}</div>
        ${["running", "queued", "uploading"].includes(i.status) && i.progress != null ? `<div class="progress"><div style="width:${Math.round(i.progress * 100)}%"></div></div>` : ""}</div>
    </a>`).join("");
  render(`<h1>תמלולים</h1>
    ${shown.length ? `<div class="list">${rows}</div>` : `<div class="empty"><div class="big">📝</div>
      <p>עוד אין תמלולים. העלה הקלטה, הקלט שיעור או הדבק קישור.</p><a class="btn primary" href="#/new">תמלול חדש</a></div>`}`);
  if (shown.some((i) => ["running", "queued", "uploading"].includes(i.status))) later(() => location.hash === "#/tx" && viewTranscriptions().catch(() => {}), 4000);
}

async function viewTranscription(id, offline = false) {
  let item, text;
  if (offline) {
    ({ item } = await offGet("t", id, "meta.json"));
    text = await offGet("t", id, "transcript.txt", "text");
  } else {
    item = await api(`/media/${enc(id)}`);
    text = item.status === "done" ? await itemTranscript(item).catch(() => "") : "";
  }
  const isSaved = !!(await offIndex())[`t:${id}`];
  const running = ["running", "queued", "uploading"].includes(item.status);
  const files = (item.files || []).map((f, i) => [f, i]).filter(([f]) => f.kind === "text" || f.kind === "subtitles");
  render(`
    <a href="#/tx" class="small">→ כל התמלולים</a>
    <h1><bdi>${esc(item.title)}</bdi></h1>
    <div class="row muted small">${itemBadge(item)}<span>${esc(fmtDate(item.created))}</span></div>
    <div class="stack" style="margin-top:12px">
      ${running ? `<div class="card"><div class="row spread"><b>${esc(item.stage || "בתור")}</b><span class="muted">${item.progress != null ? `${Math.round(item.progress * 100)}%` : ""}</span></div>
        <div class="progress"><div style="width:${Math.round((item.progress || 0.03) * 100)}%"></div></div>
        <p class="muted small" style="margin-bottom:0">אפשר לסגור את האפליקציה. התמלול ממשיך בשרת.</p></div>` : ""}
      ${item.status === "error" ? `<div class="card"><p style="color:var(--bad);margin-top:0">${esc(item.error || "התמלול נכשל")}</p><button class="btn" id="retry">נסה שוב</button></div>` : ""}
      ${!offline && item.status === "done" && item.kind === "transcription" ? `<div class="card stack">
        <div class="row">${files.map(([f, i]) => `<a class="btn" href="${mediaUrl(item.id, i, true)}" download>${f.kind === "text" ? "הורד טקסט" : "הורד SRT"}</a>`).join("")}
          ${text ? `<button class="btn" id="copy">העתק</button>` : ""}
          <button class="btn ${isSaved ? "danger" : ""}" id="save-t">${isSaved ? "הסר מהטלפון" : "⬇ שמור בטלפון"}</button></div>
        ${item.project ? `<a class="btn block" href="#/p/${enc(item.project)}">לפודקאסט שנוצר מהתמלול</a>`
          : `<button class="btn primary block" id="to-podcast">🎙️ צור פודקאסט מהתמלול</button>`}
      </div>` : ""}
      <div id="text"></div>
    </div>`);
  if (text) paintReader($("#text"), text);
  $("#retry")?.addEventListener("click", async () => { try { await api(`/media/${enc(id)}/retry`, { method: "POST" }); route(); } catch (err) { toast(err.message, true); } });
  $("#copy")?.addEventListener("click", async () => { try { await navigator.clipboard.writeText(text); toast("הועתק"); } catch (_) { toast("ההעתקה נחסמה", true); } });
  $("#save-t")?.addEventListener("click", async () => {
    try {
      if (isSaved) { await offRemove("t", id); toast("הוסר מהטלפון"); } else { await saveTranscription(item, text); toast("נשמר בטלפון"); }
      route();
    } catch (err) { toast(err.message, true); }
  });
  $("#to-podcast")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { const { project } = await api(`/media/${enc(id)}/podcast`, { method: "POST", body: {} }); location.hash = `#/p/${enc(project)}`; }
    catch (err) { e.target.disabled = false; toast(err.message, true); }
  });
  if (!offline && running) later(() => location.hash === `#/t/${enc(id)}` && route(), 3000);
}

// ------------------------------------------------------------------ new: file, recording or link

let recorder = null;

function viewNew() {
  const s = { what: "transcribe", source: "file", file: null };
  render(`<h1>חדש</h1>
    <div class="stack">
      <div class="seg" id="what">
        <button data-what="transcribe" class="on">תמלול בלבד</button>
        <button data-what="podcast">פודקאסט מלא</button>
      </div>
      <p class="muted small" id="what-note" style="margin:0"></p>
      <div class="seg" id="source">
        <button data-source="file" class="on">קובץ</button>
        <button data-source="record">הקלטה</button>
        <button data-source="url">קישור</button>
      </div>
      <div class="card stack" id="src-file">
        <input type="file" id="file" accept="audio/*,video/*,.m4a,.mp3,.wav,.mp4,.mov,.txt,.srt,.vtt" hidden>
        <button class="btn block" id="pick">בחר קובץ מהטלפון</button>
        <div id="chosen" class="muted small"></div>
      </div>
      <div class="card stack" id="src-record" hidden>
        <div class="rec" id="rec-time">0:00</div>
        <button class="btn primary block" id="rec-btn">● התחל להקליט</button>
        <p class="muted small" style="margin:0">השאר את המסך פתוח ואת האפליקציה בחזית: נעילת מסך עוצרת את ההקלטה.</p>
      </div>
      <div class="card stack" id="src-url" hidden>
        <label class="field"><span>קישור להרצאה (YouTube, Vimeo...)</span><input type="url" id="url" class="ltr" placeholder="https://" inputmode="url"></label>
      </div>
      <label class="field"><span>שם (אופציונלי)</span><input type="text" id="title" maxlength="80" placeholder="למשל: רשתות, שיעור 3"></label>
      <label class="field"><span>שפת ההרצאה</span><select id="lang"><option value="he">עברית</option><option value="en">English</option></select></label>
      <button class="btn primary block" id="go">התחל</button>
      <div id="up" hidden><div class="muted small" id="up-label">מעלה...</div><div class="progress"><div id="up-bar" style="width:0%"></div></div></div>
    </div>`);

  const note = () => {
    $("#what-note").textContent = s.what === "transcribe"
      ? "התמלול רץ על השרת ומופיע בלשונית תמלולים. משם אפשר גם להמשיך לפודקאסט."
      : "המערכת מתמללת ומכינה תוכנית לפרק. כתיבת התסריט והקלטת הקולות מתחילות רק כשתלחץ \"המשך\".";
  };
  note();
  $$("[data-what]").forEach((b) => { b.onclick = () => { s.what = b.dataset.what; $$("[data-what]").forEach((x) => x.classList.toggle("on", x === b)); note(); }; });
  $$("[data-source]").forEach((b) => {
    b.onclick = () => {
      s.source = b.dataset.source;
      $$("[data-source]").forEach((x) => x.classList.toggle("on", x === b));
      ["file", "record", "url"].forEach((k) => { $(`#src-${k}`).hidden = k !== s.source; });
    };
  });
  const showChosen = (file) => {
    s.file = file;
    const big = file.size > CF_LIMIT;
    $("#chosen").innerHTML = `<bdi>${esc(file.name)}</bdi> · ${fmtSize(file.size)}${big
      ? `<div style="color:var(--bad)">גדול מ-100MB: Cloudflare לא יעביר אותו. העלה קישור, או מהמחשב בבית.</div>` : ""}`;
  };
  $("#pick").onclick = () => $("#file").click();
  $("#file").onchange = (e) => e.target.files[0] && showChosen(e.target.files[0]);
  bindRecorder((file) => showChosen(file));

  $("#go").onclick = async () => {
    const go = $("#go");
    const title = $("#title").value.trim();
    const language = $("#lang").value;
    try {
      if (s.source === "url") {
        const url = $("#url").value.trim();
        if (!/^https?:\/\//.test(url)) return toast("הדבק קישור שמתחיל ב-https://", true);
        go.disabled = true;
        if (s.what === "podcast") {
          const p = await api("/projects/url", { method: "POST", body: { url, title, language } });
          location.hash = `#/p/${enc(p.id)}`;
        } else {
          await api("/media/download", { method: "POST", body: { urls: url, quality: "audio", transcribe: true, language, subtitles: false } });
          location.hash = "#/tx";
        }
        return;
      }
      if (!s.file) return toast(s.source === "record" ? "הקלט משהו קודם" : "בחר קובץ קודם", true);
      if (s.file.size > CF_LIMIT) return toast("הקובץ גדול מ-100MB. העלה קישור במקום.", true);
      go.disabled = true;
      $("#up").hidden = false;
      const onProgress = (f) => {
        $("#up-bar").style.width = `${Math.round(f * 100)}%`;
        $("#up-label").textContent = f < 1 ? `מעלה... ${Math.round(f * 100)}%` : "מתחיל לעבד...";
      };
      const name = s.file.name || "recording.m4a";
      if (s.what === "podcast") {
        const p = await upload("/api/projects/upload", { filename: name, title, language }, s.file, onProgress);
        location.hash = `#/p/${enc(p.id)}`;
      } else {
        // The Tools API names a transcription after its file, so a given title becomes the file name
        const named = title ? `${title.replace(/[\\/:*?"<>|]+/g, " ").trim()}.${name.split(".").pop()}` : name;
        const item = await upload("/api/media/upload", { filename: named, language }, s.file, onProgress);
        location.hash = `#/t/${enc(item.id)}`;
      }
    } catch (err) {
      go.disabled = false;
      $("#up").hidden = true;
      toast(err instanceof Offline ? "אין חיבור לשרת" : err.message, true);
    }
  };
}

function upload(path, params, file, onProgress, retried = false) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${path}?${new URLSearchParams(params)}`);
    xhr.setRequestHeader("X-MBS-Token", TOKEN);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = async () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
      if (xhr.status === 401 && !retried && await refreshToken()) return upload(path, params, file, onProgress, true).then(resolve, reject);
      if (xhr.status === 413) return reject(new Error("הקובץ גדול מדי למעבר דרך Cloudflare (100MB)."));
      if (xhr.status < 300) resolve(data); else reject(new Error(data.detail || `ההעלאה נכשלה (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("ההעלאה נכשלה. בדוק את החיבור."));
    xhr.send(file);
  });
}

function bindRecorder(onDone) {
  const button = $("#rec-btn");
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    button.disabled = true;
    button.textContent = "הקלטה לא נתמכת בדפדפן הזה";
    return;
  }
  let started = 0, tick = null, lock = null;
  button.onclick = async () => {
    if (recorder && recorder.state === "recording") { recorder.stop(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true } }); }
    catch (_) { return toast("אין הרשאה למיקרופון", true); }
    const type = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported?.(t)) || "";
    recorder = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 64000 } : undefined);
    const chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(tick);
      lock?.release?.().catch(() => {});
      const mime = recorder.mimeType || type || "audio/mp4";
      const ext = mime.includes("webm") ? "webm" : "m4a";
      const stamp = new Date().toLocaleString("he-IL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(/[/:, ]+/g, "-");
      const file = new File(chunks, `הקלטה-${stamp}.${ext}`, { type: mime });
      button.textContent = "● הקלט מחדש";
      button.classList.add("primary");
      onDone(file);
      toast("ההקלטה מוכנה. לחץ \"התחל\" כדי לתמלל.");
    };
    recorder.start(1000);
    started = Date.now();
    try { lock = await navigator.wakeLock?.request("screen"); } catch (_) { /* not supported */ }
    button.textContent = "■ עצור";
    button.classList.remove("primary");
    $("#rec-time").innerHTML = `<span class="dot"></span>0:00`;
    tick = setInterval(() => { $("#rec-time").innerHTML = `<span class="dot"></span>${clock((Date.now() - started) / 1000)}`; }, 500);
  };
}

// ------------------------------------------------------------------ saved on this phone

async function viewSaved(offline = false) {
  const entries = Object.values(await offIndex()).sort((a, b) => (b.saved || "").localeCompare(a.saved || ""));
  let quota = "";
  try {
    const { usage, quota: q } = await navigator.storage.estimate();
    if (q) quota = `בשימוש ${fmtSize(usage)} מתוך ${fmtSize(q)} שהטלפון מקצה`;
  } catch (_) { /* unsupported */ }
  const total = entries.reduce((n, e) => n + (e.size || 0), 0);
  render(`<h1>שמורים בטלפון</h1>
    ${offline ? `<div class="card muted small" style="margin-bottom:12px">אין חיבור לשרת. אלה הדברים שאפשר לפתוח בלי אינטרנט.
      <button class="btn block" style="margin-top:8px" onclick="location.reload()">נסה להתחבר שוב</button></div>` : ""}
    ${entries.length ? `<p class="muted small">${entries.length} פריטים · ${fmtSize(total)}${quota ? ` · ${quota}` : ""}</p>
      <div class="list">${entries.map((e) => `
        <div class="card item">
          <a class="art" style="--h:${hue(e.id)}" href="#/${e.kind}/${enc(e.id)}" aria-hidden="true">${e.kind === "p" ? "🎧" : "📝"}</a>
          <a class="body" href="#/${e.kind}/${enc(e.id)}" style="color:var(--text)"><div class="title"><bdi>${esc(e.title)}</bdi></div>
            <div class="meta">${e.kind === "p" ? [e.audio && "פודקאסט", e.summary && "סיכום", e.transcript && "תמלול"].filter(Boolean).join(" · ") : "תמלול"} · ${fmtSize(e.size || 0)}</div></a>
          <button class="btn danger" data-del="${e.kind}:${esc(e.id)}" aria-label="הסר">✕</button>
        </div>`).join("")}</div>`
      : `<div class="empty"><div class="big">⬇</div><p>עוד לא שמרת כלום. בעמוד של פודקאסט או תמלול לחץ "שמור", והוא יהיה זמין גם בטיסה או בלי קליטה.</p></div>`}`);
  $$("[data-del]").forEach((b) => {
    b.onclick = async () => {
      const [kind, ...rest] = b.dataset.del.split(":");
      await offRemove(kind, rest.join(":"));
      route();
    };
  });
}

// ------------------------------------------------------------------ look: theme and accent

const THEMES = [["auto", "אוטומטי", "linear-gradient(135deg,#f3f5f9 50%,#0b1119 50%)"], ["dark", "כהה", "#141f2c"],
  ["black", "שחור", "#000"], ["light", "בהיר", "#fff"]];
const ACCENTS = [["teal", "טורקיז", "#2dd4bf", "#38bdf8"], ["blue", "כחול", "#60a5fa", "#818cf8"],
  ["purple", "סגול", "#a78bfa", "#f472b6"], ["orange", "שקיעה", "#fb923c", "#f43f5e"],
  ["rose", "ורוד", "#fb7185", "#c084fc"], ["gold", "זהב", "#fbbf24", "#fb923c"]];
const lightQuery = matchMedia("(prefers-color-scheme: light)");

function applyLook() {
  const look = store.get("mbs-m-look", {});
  const root = document.documentElement;
  root.dataset.theme = look.theme && look.theme !== "auto" ? look.theme : (lightQuery.matches ? "light" : "dark");
  root.dataset.accent = look.accent || "teal";
  // The status bar and the app switcher take the page's background
  $('meta[name="theme-color"]').content = getComputedStyle(root).getPropertyValue("--bg").trim() || "#0b1119";
}
lightQuery.addEventListener?.("change", applyLook);

function openLook() {
  const back = document.createElement("div");
  back.className = "sheet-back";
  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-label", "עיצוב");
  const paint = () => {
    const cur = store.get("mbs-m-look", {});
    sheet.innerHTML = `<h2>ערכת נושא</h2>
      <div class="themes">${THEMES.map(([k, label, swatch]) => `<button type="button" data-theme-pick="${k}"
        class="${(cur.theme || "auto") === k ? "on" : ""}"><i style="background:${swatch}"></i>${label}</button>`).join("")}</div>
      <h2>צבע</h2>
      <div class="swatches">${ACCENTS.map(([k, label, a, b]) => `<button type="button" data-accent-pick="${k}" aria-label="${label}"
        class="${(cur.accent || "teal") === k ? "on" : ""}"><i style="background:linear-gradient(135deg,${a},${b})"></i></button>`).join("")}</div>`;
    $$("[data-theme-pick]", sheet).forEach((b) => { b.onclick = () => { store.set("mbs-m-look", { ...store.get("mbs-m-look", {}), theme: b.dataset.themePick }); applyLook(); paint(); }; });
    $$("[data-accent-pick]", sheet).forEach((b) => { b.onclick = () => { store.set("mbs-m-look", { ...store.get("mbs-m-look", {}), accent: b.dataset.accentPick }); applyLook(); paint(); }; });
  };
  paint();
  const close = () => { back.remove(); sheet.remove(); };
  back.onclick = close;
  document.body.append(back, sheet);

}
$("#look-btn").onclick = openLook;

// iOS 26 home-screen apps: the layout viewport can end above the screen's bottom edge, which leaves a
// band under the fixed tab bar. Measure it and move the bottom bars down by that much.
function fixIosGap() {
  const standalone = navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  let gap = 0;
  if (standalone) {
    // Only when the page itself starts under the status bar (a top inset): then a viewport shorter than
    // the screen can only mean it stops short at the bottom. Without that inset it may simply start lower.
    const probe = document.createElement("div");
    probe.style.cssText = "position:fixed;top:0;height:env(safe-area-inset-top,0px);visibility:hidden";
    document.body.append(probe);
    const topInset = probe.getBoundingClientRect().height;
    probe.remove();
    const screenH = innerHeight > innerWidth ? Math.max(screen.width, screen.height) : Math.min(screen.width, screen.height);
    gap = Math.round(screenH - innerHeight);
    if (topInset < 1 || gap < 0 || gap > 100) gap = 0;  // a keyboard, or anything unexpected: leave the layout alone
  }
  document.documentElement.style.setProperty("--ios-gap", `${gap}px`);
}

// ------------------------------------------------------------------ start

applyLook();
fixIosGap();
addEventListener("resize", fixIosGap);
addEventListener("orientationchange", () => setTimeout(fixIosGap, 300));
document.documentElement.style.setProperty("--read-size", `${store.get("mbs-m-read", 17)}px`);
window.addEventListener("online", () => { setOnline(true); route(); });
window.addEventListener("offline", () => setOnline(false));
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" }).catch(() => { /* offline features off */ });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", route, { once: true }); else route();
