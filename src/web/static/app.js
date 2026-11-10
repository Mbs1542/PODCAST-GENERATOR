"use strict";

/* M.B.S Studio browser UI. Plain JS, hash routes:
   #/ projects · #/new · #/p/<id>[/<tab>] · #/settings
   Everything that comes from the model or from files is escaped with esc() before it
   reaches innerHTML. */

let TOKEN = document.querySelector('meta[name="mbs-token"]').content;
const app = document.getElementById("app");

// Each server launch makes a new token. A tab opened before a restart would get 401 on every save
// (and lose what was typed), so on 401 the page picks up the running server's token from its own page
// (same origin only) and retries once.
async function refreshToken() {
  try {
    const html = await (await fetch("/", { cache: "no-store" })).text();
    const fresh = new DOMParser().parseFromString(html, "text/html").querySelector('meta[name="mbs-token"]')?.content;
    if (fresh && fresh !== TOKEN) { TOKEN = fresh; return true; }
  } catch (_) { /* server down */ }
  return false;
}

async function authFetch(url, init = {}) {
  const send = () => fetch(url, { ...init, headers: { ...(init.headers || {}), "X-MBS-Token": TOKEN } });
  const res = await send();
  return res.status === 401 && await refreshToken() ? send() : res;
}

const FORMAT_LABELS = {
  auto: "אוטומטי (לפי השיעור)", deep_dive: "צלילה לעומק", beginner_explainer: "למתחילים",
  exam_prep: "הכנה למבחן", story: "סיפור", debate: "דיון", brief: "תקציר קצר", critique: "ביקורת",
};
// The episode's direction: icon and one line on what the listener gets
const DIRECTIONS = {
  auto: ["✨", "המערכת בוחרת לפי סוג השיעור"],
  deep_dive: ["🔍", "מהיסודות ועד הרעיון הקשה, עם הדוגמאות של המרצה"],
  beginner_explainer: ["🌱", "לאט, עם אנלוגיות מהיומיום ובדיקות הבנה"],
  exam_prep: ["🎯", "הגדרות מדויקות, הבחנות וטעויות נפוצות"],
  story: ["📖", "כסיפור: רקע, מתח, נקודת מפנה, השלכות"],
  debate: ["⚖️", "שתי זוויות על כל שאלה פתוחה"],
  brief: ["⚡", "רק העיקר, בכ-5 דקות"],
  critique: ["🧐", "מה משכנע בשיעור, על מה הוא נשען ומה נשאר פתוח"],
};
const ACCENT_NAMES = { teal: "טורקיז", blue: "כחול", purple: "סגול", orange: "כתום", rose: "ורוד", gold: "זהב" };
const THEME_ICONS = { auto: "◐", light: "☀", dark: "☾" };
const THEME_NAMES = { auto: "לפי המערכת", light: "בהיר", dark: "כהה" };
const STATUS_LABELS = {
  new: "בתור", transcribed: "תמלול מוכן", plan_ready: "תוכנית לבדיקה",
  script_ready: "תסריט מוכן", audio_ready: "פודקאסט מוכן", video_ready: "וידאו מוכן",
};
const ORDER = ["new", "transcribed", "plan_ready", "script_ready", "audio_ready", "video_ready"];
const LEVELS = { intro: "מבוא", intermediate: "בינוני", advanced: "מתקדם" };
const LESSON_TYPES = { lecture: "הרצאה", tutorial: "תרגול", workshop: "סדנה", discussion: "דיון", story: "סיפור" };
const CHOICE_LABELS = {
  ...FORMAT_LABELS, auto: "אוטומטי", on: "תמיד", off: "כבוי",
  elevenlabs: "ElevenLabs: שיחה טבעית (מומלץ)", gemini: "Gemini: שיחה טבעית", azure: "Azure Speech: שורה אחר שורה",
  eleven_v4: "Eleven v4 (מומלץ, עברית)", eleven_v3: "Eleven v3",
  he: "עברית (ברירת מחדל)", en: "English",
  balanced: "מאוזן (ברירת מחדל): מהיר ומדויק", fast: "מהיר: כפול מהר, מעט פחות מדויק",
  accurate: "מדויק: המודל המלא, איטי פי 2-3 (הורדה של 3.1GB בפעם הראשונה)",
  1: "1 (ברירת מחדל)", 2: "2", 3: "3", 4: "4",
};
const QUALITY_SHORT = { fast: "מהיר", balanced: "מאוזן", accurate: "מדויק" };
const SPEAKERS = { Roee: "רועי", Noa: "נועה" };

const state = { status: null, timer: null };

// ------------------------------------------------------------------ helpers

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const rank = (status) => ORDER.indexOf(status);

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await authFetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  // Network mode: the sign-in expired or the server restarted; the page shows the sign-in form
  if (res.status === 401 && data && data.detail === "sign in first") { location.reload(); return new Promise(() => {}); }
  if (!res.ok) throw new Error((data && data.detail) || `שגיאה ${res.status}`);
  return data;
}

function toast(message, bad = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = bad ? "toast bad" : "toast";
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 2800);
}

function schedule(fn, ms) {
  clearTimeout(state.timer);
  state.timer = setTimeout(fn, ms);
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(r).padStart(2, "0")}`;
}

function fmtDate(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("he-IL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const fileUrl = (id, name, download = false) => `/api/projects/${enc(id)}/files/${name}?t=${enc(TOKEN)}${download ? "&download=1" : ""}`;
const guideLink = (anchor, text = "מה זה?") => `<a href="/guide#${esc(anchor)}" target="_blank" rel="noopener">${text}</a>`;

function statusBadge(p) {
  if (p.running) return `<span class="badge run"><span class="dot"></span>${esc(p.stage || "בעבודה")}</span>`;
  if (p.error) return `<span class="badge bad">נעצר בשגיאה</span>`;
  const cls = p.status === "audio_ready" || p.status === "video_ready" ? "ok" : p.status === "plan_ready" ? "warn" : "";
  return `<span class="badge ${cls}">${esc(STATUS_LABELS[p.status] || p.status)}</span>`;
}

function progressBar(p) {
  if (!p.running) return "";
  const known = typeof p.progress === "number";
  return `<div class="progress ${known ? "" : "indeterminate"}"><div style="width:${known ? Math.round(p.progress * 100) : 30}%"></div></div>`;
}

function render(html) { app.innerHTML = html; }

async function refreshStatus() {
  try {
    state.status = await api("/status");
    document.getElementById("setup-pill").hidden = state.status.settings_ok;
  } catch (_) { /* server restarting */ }
  return state.status || { settings_ok: false };
}

// ------------------------------------------------------------------ router

async function route() {
  clearTimeout(state.timer);
  const hash = location.hash || "#/";
  const section = hash.startsWith("#/settings") ? "settings" : hash.startsWith("#/look") ? "look"
    : hash.startsWith("#/tools") ? "tools" : hash.startsWith("#/new") ? "" : "projects";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
  try {
    if (hash.startsWith("#/settings")) await viewSettings();
    else if (hash.startsWith("#/look")) await viewLook();
    else if (hash.startsWith("#/tools/podcast")) await viewChainPodcast(new URLSearchParams(hash.split("?")[1] || "").get("items"));
    else if (hash.startsWith("#/tools")) await viewTools(hash.split("/")[2] || "transcribe");
    else if (hash.startsWith("#/new")) await viewNew();
    else if (hash.startsWith("#/p/")) {
      const [id, tab] = hash.slice(4).split("/").map(decodeURIComponent);
      await viewProject(id, tab);
    } else await viewProjects();
  } catch (err) {
    render(`<div class="banner bad"><p>${esc(err.message)}</p><button class="btn" onclick="route()">נסה שוב</button></div>`);
  }
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ projects

function setupBanner() {
  return `<div class="banner warn"><p><b>כמעט מוכן.</b> כדי לכתוב פודקאסטים צריך לבחור מי כותב את התסריט (מומלץ: Gemini) ולמלא את המפתח שלו. תמלול עובד גם בלי זה.</p>
    <a class="btn" href="#/settings">להגדרות</a></div>`;
}

async function viewProjects() {
  const [{ projects }, status] = await Promise.all([api("/projects"), refreshStatus()]);
  const cards = projects.map((p) => `
    <a class="card project-card" href="#/p/${enc(p.id)}">
      <div class="row" style="justify-content:space-between"><h3>${esc(p.title)}</h3></div>
      <div class="project-meta">
        <span>${esc(fmtDate(p.created))}</span>
        ${p.minutes ? `<span>${esc(p.minutes)} דק'</span>` : ""}
        ${p.format ? `<span>${esc(FORMAT_LABELS[p.format] || p.format)}</span>` : ""}
      </div>
      <div>${statusBadge(p)}</div>
      ${progressBar(p)}
    </a>`).join("");
  render(`
    <div class="page-head">
      <div><h1>הפרויקטים שלי</h1><div class="sub">כל הרצאה הופכת לפרויקט: תמלול, תוכנית, תסריט ופודקאסט.</div></div>
      <a class="btn primary lg" href="#/new">+ פודקאסט חדש</a>
    </div>
    <div class="stack">
      ${status.settings_ok ? "" : setupBanner()}
      ${projects.length ? `<div class="projects">${cards}</div>` : `
        <div class="card empty">
          <h2>עוד אין פרויקטים</h2>
          <p>מעלים סרטון של הרצאה, קובץ שמע או תמלול. המערכת מתמללת, מנתחת את השיעור ובונה תוכנית לפרק שאפשר לערוך לפני הכתיבה.</p>
          <a class="btn primary lg" href="#/new">ליצור פודקאסט ראשון</a>
        </div>`}
    </div>`);
  if (projects.some((p) => p.running)) schedule(viewProjects, 3000);
}

// ------------------------------------------------------------------ new project

function directionCards(selected, attr = "data-direction") {
  return Object.entries(DIRECTIONS).map(([key, [icon, text]]) => `
    <button type="button" class="style-card ${key === selected ? "active" : ""}" ${attr}="${key}" aria-pressed="${key === selected}">
      <b><span class="icon" aria-hidden="true">${icon}</span> ${esc(FORMAT_LABELS[key].replace(" (לפי השיעור)", ""))}</b><span>${esc(text)}</span></button>`).join("");
}

function bindDirectionCards(root, onPick, attr = "data-direction") {
  root.querySelectorAll(`[${attr}]`).forEach((b) => {
    b.onclick = () => {
      root.querySelectorAll(`[${attr}]`).forEach((x) => { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", String(x === b)); });
      onPick(b.getAttribute(attr));
    };
  });
}

function optionSelects() {
  return `
    <div class="field"><span>כיוון הפרק ${guideLink("formats")}</span>
      <div class="style-cards" id="directions">${directionCards("auto")}</div></div>
    <label class="field"><span>כיוון ומיקוד במילים שלך (אופציונלי)</span>
      <textarea id="opt-focus" rows="2" maxlength="500" placeholder="למשל: אני מתכונן למבחן, תתמקדו בהבדלים בין NSG ל-Firewall ותנו הרבה דוגמאות"></textarea></label>
    <div class="form-group">
      <label class="field"><span>אורך</span>
        <select id="opt-minutes">
          <option value="0">אוטומטי (כ-30% מההרצאה)</option>
          <option value="5">כ-5 דקות</option><option value="10">כ-10 דקות</option>
          <option value="15">כ-15 דקות</option><option value="20">כ-20 דקות</option><option value="30">כ-30 דקות</option>
        </select></label>
      <label class="field"><span>שפת הפודקאסט</span>
        <select id="opt-language"><option value="he">עברית</option><option value="en">English</option></select></label>
      <label class="field"><span>קולות</span>
        <select id="opt-voice"><option value="auto">לפי ההגדרות</option><option value="elevenlabs">ElevenLabs</option>
          <option value="gemini">Gemini</option><option value="azure">Azure Speech</option></select></label>
    </div>`;
}

async function viewNew() {
  const status = await refreshStatus();
  let mode = "file";
  let chosen = null;
  let direction = "auto";
  render(`
    <div class="page-head"><div><h1>פודקאסט חדש</h1><div class="sub">אחרי ההעלאה המערכת מתמללת ובונה תוכנית לפרק. תוכל לבדוק ולערוך אותה לפני שהתסריט נכתב.</div></div></div>
    <div class="stack">
      ${status.settings_ok ? "" : setupBanner()}
      <div class="card stack">
        <div class="tabs" role="tablist">
          <button class="active" data-mode="file">קובץ</button>
          <button data-mode="url">קישור</button>
        </div>
        <div id="src-file">
          <div class="dropzone" id="drop" tabindex="0" role="button">
            <strong>גרור לכאן סרטון, הקלטה או תמלול</strong>
            <span class="muted">או לחץ לבחירה · MP4, MKV, MOV, MP3, M4A, WAV, TXT, SRT, VTT</span>
          </div>
          <input type="file" id="file" hidden accept="video/*,audio/*,.txt,.srt,.vtt,.md">
          <div class="file-chosen card card-tight" id="chosen" hidden></div>
        </div>
        <div id="src-url" hidden>
          <label class="field"><span>קישור להרצאה (למשל YouTube)</span><input type="url" id="url" class="ltr" placeholder="https://"></label>
          <p class="help">ההקלטה תירד ותתומלל במחשב שלך. השתמש בקישורים להרצאות שמותר לך להוריד.</p>
        </div>
        <label class="field"><span>שם הפרויקט (אופציונלי)</span><input type="text" id="title" maxlength="80" placeholder="למשל: רשתות בענן, שיעור 3"></label>
        ${optionSelects()}
        <div class="row">
          <button class="btn primary lg" id="start">התחל</button>
          <a class="btn ghost" href="#/">ביטול</a>
        </div>
        <div id="upload" hidden class="stack"><div class="muted" id="upload-label">מעלה...</div><div class="progress"><div id="upload-bar" style="width:0%"></div></div></div>
      </div>
    </div>`);

  bindDirectionCards(document.getElementById("directions"), (key) => {
    direction = key;
    // A brief is short by definition; pre-select the matching length so the choice is visible
    if (key === "brief" && document.getElementById("opt-minutes").value === "0") document.getElementById("opt-minutes").value = "5";
  });
  const fileInput = document.getElementById("file");
  const drop = document.getElementById("drop");
  const showChosen = (file) => {
    chosen = file;
    const box = document.getElementById("chosen");
    box.hidden = false;
    box.innerHTML = `<div><b><bdi>${esc(file.name)}</bdi></b> <span class="muted small">${fmtSize(file.size)}</span></div>
      <button class="btn ghost" id="clear">החלף</button>`;
    document.getElementById("clear").onclick = () => fileInput.click();
    drop.hidden = true;
  };
  drop.onclick = () => fileInput.click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") fileInput.click(); };
  fileInput.onchange = () => fileInput.files[0] && showChosen(fileInput.files[0]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) showChosen(e.dataTransfer.files[0]); };

  document.querySelectorAll("[data-mode]").forEach((b) => {
    b.onclick = () => {
      mode = b.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("active", x === b));
      document.getElementById("src-file").hidden = mode !== "file";
      document.getElementById("src-url").hidden = mode !== "url";
    };
  });

  document.getElementById("start").onclick = async () => {
    const options = {
      title: document.getElementById("title").value.trim(),
      format: direction,
      minutes: document.getElementById("opt-minutes").value,
      voice: document.getElementById("opt-voice").value,
      focus: document.getElementById("opt-focus").value.trim(),
      language: document.getElementById("opt-language").value,
    };
    const startBtn = document.getElementById("start");
    try {
      let project;
      if (mode === "url") {
        const url = document.getElementById("url").value.trim();
        if (!/^https?:\/\//.test(url)) return toast("הדבק קישור שמתחיל ב-https://", true);
        startBtn.disabled = true;
        project = await api("/projects/url", { method: "POST", body: { url, ...options } });
      } else {
        if (!chosen) return toast("בחר קובץ קודם", true);
        startBtn.disabled = true;
        document.getElementById("upload").hidden = false;
        project = await uploadFile(chosen, options, (fraction) => {
          document.getElementById("upload-bar").style.width = `${Math.round(fraction * 100)}%`;
          document.getElementById("upload-label").textContent = fraction < 1 ? `מעלה... ${Math.round(fraction * 100)}%` : "מתחיל לעבד...";
        });
      }
      location.hash = `#/p/${enc(project.id)}`;
    } catch (err) {
      startBtn.disabled = false;
      toast(err.message, true);
    }
  };
}

function uploadFile(file, options, onProgress, retried = false) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/upload?${new URLSearchParams({ ...options, filename: file.name })}`);
    xhr.setRequestHeader("X-MBS-Token", TOKEN);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = async () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
      if (xhr.status === 401 && !retried && await refreshToken()) {
        uploadFile(file, options, onProgress, true).then(resolve, reject);
        return;
      }
      if (xhr.status < 300) resolve(data); else reject(new Error(data.detail || "ההעלאה נכשלה"));
    };
    xhr.onerror = () => reject(new Error("ההעלאה נכשלה"));
    xhr.send(file);
  });
}

// ------------------------------------------------------------------ project

const NEXT_JOB = { new: "prepare", transcribed: "prepare", plan_ready: "write", script_ready: "audio", audio_ready: "audio", video_ready: "video" };

function stepper(p) {
  const r = rank(p.status);
  // [label, status rank at which the step is done]; the plan counts as done only once a script was written from it
  const steps = [["תמלול", 1], ["תוכנית", 3], ["תסריט", 3], ["פודקאסט", 4], ["סיכום שיעור", 4]];
  const current = steps.findIndex(([, doneAt]) => r < doneAt);
  return `<div class="stepper">${steps.map(([label, doneAt], i) => {
    const cls = r >= doneAt ? "done" : i === current ? "current" : "";
    return `<div class="step ${cls}"><div class="bar"></div>${label}</div>`;
  }).join("")}</div>`;
}

function fmtSize(bytes) {
  return bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))}KB` : `${(bytes / 1048576).toFixed(1)}MB`;
}

function runPanel(p) {
  if (p.running) {
    return `<div class="card stack">
      <div class="row" style="justify-content:space-between"><b>${esc(p.stage || "בעבודה...")}</b>
        ${typeof p.progress === "number" ? `<span class="muted">${Math.round(p.progress * 100)}%</span>` : ""}</div>
      ${progressBar(p)}
      <p class="help" style="margin:0">אפשר לסגור את הדף ולחזור אחר כך. העבודה ממשיכה ברקע כל עוד האפליקציה פתוחה.</p>
      ${logBlock(p)}
    </div>`;
  }
  if (p.error) {
    return `<div class="banner bad"><p>${esc(p.error)}</p>
      <div class="row"><a class="btn" href="#/settings">הגדרות</a><button class="btn primary" data-job="${NEXT_JOB[p.status]}">נסה שוב</button></div></div>`;
  }
  if (p.notice) {
    return `<div class="banner info"><p>${esc(p.notice)}</p>
      <div class="row"><a class="btn" href="#/settings">הגדרות</a><button class="btn primary" data-job="prepare">המשך</button></div></div>`;
  }
  return "";
}

function logBlock(p) {
  if (!p.log || !p.log.length) return "";
  return `<details class="log"><summary>יומן עבודה</summary><pre>${esc(p.log.join("\n"))}</pre></details>`;
}

// The video tab is a workshop: stay on it while its jobs run and when they finish
const stickyTab = () => (location.hash.match(/^#\/p\/[^/]+\/(video)$/) || [])[1];

async function startJob(id, kind, force = false) {
  try {
    await api(`/projects/${enc(id)}/jobs/${kind}`, { method: "POST", body: { force } });
    await viewProject(id, stickyTab());
  } catch (err) {
    toast(err.message, true);
  }
}

function bindJobs(p) {
  app.querySelectorAll("[data-job]").forEach((b) => {
    b.onclick = () => { b.disabled = true; startJob(p.id, b.dataset.job, b.dataset.force === "1"); };
  });
}

async function viewProject(id, tab) {
  const p = await api(`/projects/${enc(id)}`);
  const r = rank(p.status);
  const tabs = [
    ["plan", "תוכנית", r >= 2],
    ["script", "תסריט", r >= 3],
    ["listen", "האזנה והורדה", r >= 4 || p.files.length > 0],
    ["video", "וידאו", r >= 3],
  ];
  const fallback = r >= 4 ? "listen" : r >= 3 ? "script" : r >= 2 ? "plan" : p.files.length ? "listen" : "";
  const active = tabs.some(([k, , ok]) => k === tab && ok) ? tab : fallback;

  render(`
    <div class="page-head">
      <div><a href="#/" class="small">→ כל הפרויקטים</a><h1>${esc(p.title)}</h1>
        <div class="sub">${esc(fmtDate(p.created))} · <bdi>${esc(p.source_kind === "url" ? "מקישור" : p.source_name)}</bdi></div></div>
      <div class="row">${statusBadge(p)}<button class="btn ghost danger" id="delete" ${p.running ? "disabled" : ""}>מחק</button></div>
    </div>
    <div class="stack">
      ${stepper(p)}
      <div id="run-panel">${runPanel(p)}</div>
      ${active ? `<div><div class="subtabs">${tabs.map(([k, label, ok]) =>
        `<button data-tab="${k}" class="${k === active ? "active" : ""}" ${ok ? "" : "disabled"}>${label}</button>`).join("")}</div>
        <div id="tab-body"><div class="muted">טוען...</div></div></div>` : ""}
      ${!p.running && !active && !p.error && !p.notice ? `<div class="card muted">ממתין לתחילת העבודה...</div>` : ""}
    </div>`);

  bindJobs(p);
  document.getElementById("delete").onclick = async () => {
    if (!confirm(`למחוק את "${p.title}" וכל הקבצים שלו?`)) return;
    try { await api(`/projects/${enc(p.id)}`, { method: "DELETE" }); location.hash = "#/"; } catch (err) { toast(err.message, true); }
  };
  app.querySelectorAll("[data-tab]").forEach((b) => {
    b.onclick = () => { if (!b.disabled) location.hash = `#/p/${enc(p.id)}/${b.dataset.tab}`; };
  });

  const body = document.getElementById("tab-body");
  if (body) {
    const renderers = { plan: renderPlan, script: renderScript, listen: renderListen, video: renderVideo };
    await renderers[active](p, body);
  }
  if (p.running) schedule(() => pollProject(p.id), 1500);
}

async function pollProject(id) {
  if (!location.hash.startsWith(`#/p/${enc(id)}`)) return;
  let p;
  try { p = await api(`/projects/${enc(id)}`); } catch (_) { return schedule(() => pollProject(id), 3000); }
  if (p.running) {
    // Refresh only the progress panel so edits elsewhere on the page are not lost
    const panel = document.getElementById("run-panel");
    const open = panel.querySelector("details")?.open;
    panel.innerHTML = runPanel(p);
    if (open) panel.querySelector("details").open = true;
    schedule(() => pollProject(id), 1500);
  } else {
    // The job finished: show the tab for the new stage (plan, script or listening)
    await viewProject(id, stickyTab());
  }
}

// ------------------------------------------------------------------ plan editor

async function renderPlan(p, el) {
  const { plan, profile } = await api(`/projects/${enc(p.id)}/plan`);
  const draft = JSON.parse(JSON.stringify(plan));
  const hasScript = rank(p.status) >= 3;
  const locked = Boolean(p.running);
  const kindLabel = { intro: "פתיחה", body: "מקטע", closing: "סיום" };

  const conceptChips = (profile.concepts || []).map((c) =>
    `<span class="chip ${c.importance === 3 ? "core" : ""}" title="${esc(c.definition)}">${esc(c.name)}</span>`).join("");

  el.innerHTML = `
    <div class="stack">
      <div class="card understanding">
        <h2>מה המערכת הבינה מהשיעור</h2>
        <p class="muted">${esc(profile.domain)} · רמה: ${esc(LEVELS[profile.level] || profile.level)} · ${esc(LESSON_TYPES[profile.lesson_type] || profile.lesson_type)}${profile.audience ? ` · ${esc(profile.audience)}` : ""}</p>
        <p>${esc(profile.summary)}</p>
        <div class="chips">${conceptChips}</div>
        <p class="help" style="margin-top:10px">מודגש = מושג מרכזי, שהתוכנית מבטיחה לכסות. אם משהו כאן לא נכון, כדאי לבדוק את התמלול.</p>
      </div>
      <div class="card stack">
        <h2>הפרק</h2>
        <div class="form-group">
          <label class="field"><span>שם הפרק</span><input type="text" data-f="title" value="${esc(draft.title)}"></label>
          <label class="field"><span>סגנון</span><select data-f="format">${Object.entries(FORMAT_LABELS).filter(([k]) => k !== "auto")
            .map(([k, v]) => `<option value="${k}" ${k === draft.format ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
          <label class="field"><span>אורך (דקות)</span><input type="number" min="2" max="60" step="1" data-f="target_minutes" value="${esc(draft.target_minutes)}"></label>
        </div>
        <label class="field"><span>פתיח: מה יגרום למאזין להמשיך להקשיב</span><textarea data-f="hook" rows="2">${esc(draft.hook)}</textarea></label>
        ${draft.focus ? `<p class="help" style="margin:0">המיקוד שביקשת: ${esc(draft.focus)}</p>` : ""}
      </div>
      <div id="segments" class="stack"></div>
      <div class="card stack">
        <h2>מסקנות לסיום</h2>
        ${[0, 1, 2].map((i) => `<input type="text" data-takeaway="${i}" value="${esc((draft.takeaways || [])[i] || "")}" placeholder="מסקנה ${i + 1}">`).join("")}
      </div>
      <div class="sticky-actions">
        <button class="btn primary lg" id="save-write" ${locked ? "disabled" : ""}>${hasScript ? "שמור וכתוב את התסריט מחדש" : "שמור וכתוב את התסריט"}</button>
        <button class="btn lg" id="save" ${locked ? "disabled" : ""}>שמור בלבד</button>
      </div>
    </div>`;

  const renderSegments = () => {
    const bodyCount = draft.segments.filter((s) => s.kind === "body").length;
    document.getElementById("segments").innerHTML = draft.segments.map((s, i) => {
      const isBody = s.kind === "body";
      const weights = isBody ? `<div class="weight" role="group" aria-label="אורך המקטע">${[[1, "קצר"], [2, "רגיל"], [3, "ארוך"]]
        .map(([w, label]) => `<button data-weight="${i}:${w}" class="${s.weight === w ? "active" : ""}">${label}</button>`).join("")}</div>` : "";
      const chips = (s.concepts || []).map((c, ci) =>
        `<span class="chip">${esc(c)}${isBody ? `<button data-uncon="${i}:${ci}" aria-label="הסר">×</button>` : ""}</span>`).join("");
      return `<div class="card segment">
        <div class="stack" style="gap:10px">
          <div class="segment-head"><span class="kind">${kindLabel[s.kind]}</span>${weights}
            <span class="muted small">כ-${esc(s.target_words)} מילים</span></div>
          <input type="text" data-seg="${i}:title" value="${esc(s.title)}" aria-label="כותרת">
          <textarea data-seg="${i}:goal" rows="2" aria-label="מטרה" placeholder="מה המאזין יבין בסוף המקטע">${esc(s.goal)}</textarea>
          ${chips ? `<div class="chips">${chips}</div>` : ""}
          ${(s.beats || []).length ? `<div class="help">רגעי מפתח: ${esc(s.beats.join(" · "))}</div>` : ""}
          ${(s.examples || []).length ? `<div class="help">דוגמאות מהשיעור: ${esc(s.examples.join(" · "))}</div>` : ""}
        </div>
        ${isBody ? `<div class="controls">
          <button class="btn icon" data-move="${i}:-1" ${i <= 1 ? "disabled" : ""} aria-label="הזז למעלה">↑</button>
          <button class="btn icon" data-move="${i}:1" ${i >= draft.segments.length - 2 ? "disabled" : ""} aria-label="הזז למטה">↓</button>
          <button class="btn icon danger" data-remove="${i}" ${bodyCount <= 1 ? "disabled" : ""} aria-label="מחק מקטע">✕</button>
        </div>` : ""}
      </div>`;
    }).join("");

    el.querySelectorAll("[data-seg]").forEach((input) => {
      input.oninput = () => { const [i, f] = input.dataset.seg.split(":"); draft.segments[+i][f] = input.value; };
    });
    el.querySelectorAll("[data-weight]").forEach((b) => {
      b.onclick = () => { const [i, w] = b.dataset.weight.split(":").map(Number); draft.segments[i].weight = w; renderSegments(); };
    });
    el.querySelectorAll("[data-uncon]").forEach((b) => {
      b.onclick = () => { const [i, c] = b.dataset.uncon.split(":").map(Number); draft.segments[i].concepts.splice(c, 1); renderSegments(); };
    });
    el.querySelectorAll("[data-move]").forEach((b) => {
      b.onclick = () => {
        const [i, d] = b.dataset.move.split(":").map(Number);
        [draft.segments[i], draft.segments[i + d]] = [draft.segments[i + d], draft.segments[i]];
        renderSegments();
      };
    });
    el.querySelectorAll("[data-remove]").forEach((b) => {
      b.onclick = () => {
        const i = +b.dataset.remove;
        if (confirm(`להסיר את המקטע "${draft.segments[i].title}"?`)) { draft.segments.splice(i, 1); renderSegments(); }
      };
    });
    if (locked) el.querySelectorAll("#segments input, #segments textarea, #segments button").forEach((x) => { x.disabled = true; });
  };
  renderSegments();

  el.querySelectorAll("[data-f]").forEach((input) => {
    input.oninput = () => { draft[input.dataset.f] = input.type === "number" ? Number(input.value) : input.value; };
    input.disabled = locked;
  });
  el.querySelectorAll("[data-takeaway]").forEach((input) => {
    input.disabled = locked;
    input.oninput = () => {
      draft.takeaways = [...el.querySelectorAll("[data-takeaway]")].map((x) => x.value.trim()).filter(Boolean);
    };
  });

  const save = async () => {
    const result = await api(`/projects/${enc(p.id)}/plan`, { method: "PUT", body: { plan: draft } });
    Object.assign(draft, result.plan);
    renderSegments();
  };
  document.getElementById("save").onclick = async () => {
    try { await save(); toast("התוכנית נשמרה"); } catch (err) { toast(err.message, true); }
  };
  document.getElementById("save-write").onclick = async (e) => {
    e.target.disabled = true;
    try { await save(); await startJob(p.id, "write", hasScript); } catch (err) { e.target.disabled = false; toast(err.message, true); }
  };
}

// ------------------------------------------------------------------ script

function qualityMetrics(report) {
  if (!report || report.total_words === undefined) return "";
  const coverage = Math.round((report.core_concept_coverage ?? 1) * 100);
  const open = (report.segments_with_open_issues || []).length;
  return `<div class="quality">
    <div class="metric"><div class="value">${esc(report.estimated_minutes)} דק'</div><div class="label">אורך משוער (יעד: ${esc(report.target_minutes)})</div></div>
    <div class="metric"><div class="value">${coverage}%</div><div class="label">כיסוי המושגים המרכזיים</div></div>
    <div class="metric"><div class="value">${esc(report.unsupported_high)}</div><div class="label">טענות שלא נמצאו בהרצאה</div></div>
    <div class="metric"><div class="value">${open}</div><div class="label">מקטעים עם הערות פתוחות</div></div>
  </div>`;
}

function issuesBlock(report) {
  const open = (report.segments || []).filter((s) => !s.passed);
  if (!open.length) return "";
  return `<details class="card card-tight"><summary><b>הערות הבודק</b> <span class="muted small">(הבודק לא הצליח לסגור אותן גם אחרי תיקון)</span></summary>
    ${open.map((s) => `<p><b>${esc(s.title)}</b><br>${s.issues.map(esc).join("<br>")}</p>`).join("")}</details>`;
}

function dialogueHtml(dialogue) {
  const chapters = new Map((dialogue.metadata?.chapters || []).map((c) => [c.start_index, c.title]));
  return dialogue.dialogue.map((t, i) => `
    ${chapters.has(i) ? `<h3 class="chapter-title" id="ch-${i}">${esc(chapters.get(i))}</h3>` : ""}
    <div class="turn ${t.speaker === "Noa" ? "noa" : "roee"}" id="turn-${i}">
      <div class="who">${esc(SPEAKERS[t.speaker] || t.speaker)}${t.delivery ? ` <span class="delivery">${esc(t.delivery)}</span>` : ""}</div>${esc(t.text)}
    </div>`).join("");
}

async function renderScript(p, el) {
  const { dialogue, report } = await api(`/projects/${enc(p.id)}/script`);
  const chapters = dialogue.metadata?.chapters || [];
  const hasAudio = p.files.includes("audio");
  el.innerHTML = `
    <div class="stack">
      ${qualityMetrics(report)}
      ${issuesBlock(report)}
      <div class="row">
        ${hasAudio ? "" : `<button class="btn primary lg" data-job="audio" ${p.running ? "disabled" : ""}>הפק את הפודקאסט</button>`}
        <button class="btn" data-job="write" data-force="1" ${p.running ? "disabled" : ""}>כתוב מחדש</button>
        <a class="btn ghost" href="/api/projects/${enc(p.id)}/script.txt?t=${enc(TOKEN)}">הורד תסריט</a>
      </div>
      <div class="script-layout">
        <nav class="chapters" aria-label="פרקים">${chapters.map((c) => `<button data-ch="${c.start_index}"><bdi>${esc(c.title)}</bdi></button>`).join("")}</nav>
        <div class="dialogue">${dialogueHtml(dialogue)}</div>
      </div>
    </div>`;
  bindJobs(p);
  el.querySelectorAll("[data-ch]").forEach((b) => {
    b.onclick = () => document.getElementById(`ch-${b.dataset.ch}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

// ------------------------------------------------------------------ listen

/* The podcast's own folder (lecture, transcripts, podcast, video, summary) and its copy in Google Drive. */
async function paintDeliveryCard(p) {
  const box = document.getElementById("delivery-card");
  if (!box) return;
  let delivery = null;
  try { delivery = await api("/delivery"); } catch (_) { return; }
  const folder = p.options.output_folder || "";
  const drive = delivery.drive;
  box.innerHTML = `<div class="card stack">
    <div><h2>תיקיית הפרק</h2>
      <div class="help">הרצאת המקור, התמלול, הפודקאסט, הווידאו (כשיש) וסיכום השיעור, יחד בתיקייה אחת.</div></div>
    ${folder ? `<div class="muted small folder-line"><bdi dir="ltr">${esc(folder)}</bdi></div>` : `<div class="muted small">התיקייה עוד לא נוצרה.</div>`}
    <div class="row" style="flex-wrap:wrap">
      ${folder && delivery.local ? `<button class="btn" data-deliver-reveal>הצג בתיקייה</button>` : ""}
      <button class="btn" data-deliver="0" ${p.running ? "disabled" : ""}>${folder ? "עדכן את התיקייה" : "צור תיקייה"}</button>
      ${drive.connected ? `<button class="btn primary" data-deliver="1" ${p.running || p.drive_uploading ? "disabled" : ""}>
          ${p.drive_uploading ? `<span class="dot"></span> מעלה ל-Drive...` : "העלה ל-Google Drive"}</button>`
        : `<a class="btn ghost" href="#/settings">חבר את Google Drive</a>`}
      ${p.options.drive_url ? `<a class="btn" href="${esc(p.options.drive_url)}" target="_blank" rel="noopener">פתח ב-Drive</a>` : ""}
    </div>
    ${drive.connected && delivery.prefs.drive_auto ? `<div class="help">העלאה אוטומטית פעילה: כל פרק שמסתיים עולה ל-Drive לתיקייה <b>${esc(delivery.prefs.drive_folder)}</b>.</div>` : ""}
  </div>`;
  box.querySelector("[data-deliver-reveal]")?.addEventListener("click", async () => {
    try { await api(`/projects/${enc(p.id)}/reveal`, { method: "POST" }); } catch (err) { toast(err.message, true); }
  });
  box.querySelectorAll("[data-deliver]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      const label = b.textContent;
      b.textContent = b.dataset.deliver === "1" ? "מתחיל העלאה..." : "יוצר את התיקייה...";
      try {
        const upload = b.dataset.deliver === "1";
        const updated = await api(`/projects/${enc(p.id)}/deliver`, { method: "POST", body: { upload } });
        toast(upload ? "התיקייה עודכנה, ההעלאה ל-Drive התחילה" : "התיקייה עודכנה");
        await paintDeliveryCard(updated);
      } catch (err) { b.disabled = false; b.textContent = label; toast(err.message, true); }
    };
  });
  if (p.drive_uploading) {  // follow the upload until it finishes
    const hash = location.hash;
    setTimeout(async () => {
      if (location.hash !== hash) return;
      try { await paintDeliveryCard(await api(`/projects/${enc(p.id)}`)); } catch (_) { /* next time */ }
    }, 3000);
  }
}

function summaryCard(p) {
  const url = (format, extra = "") => `/api/projects/${enc(p.id)}/summary?format=${format}&t=${enc(TOKEN)}${extra}`;
  return `<div class="card stack summary-card">
    <div class="row" style="justify-content:space-between;align-items:flex-start;flex-wrap:wrap">
      <div><h2>סיכום השיעור</h2>
        <div class="help">מושגי מפתח, עובדות, טעויות נפוצות, פרקי הפודקאסט ושאלות לבדיקה עצמית. נבנה מניתוח השיעור, בלי עלות נוספת.</div></div>
      <a class="btn primary" href="${url("html")}" target="_blank" rel="noopener">פתח את הסיכום</a></div>
    <div class="files">
      <a class="btn" href="${url("html", "&print=1")}" target="_blank" rel="noopener">PDF (הדפסה)</a>
      <a class="btn" href="${url("docx", "&download=1")}">Word</a>
      <a class="btn" href="${url("html", "&download=1")}">HTML</a>
      <a class="btn" href="${url("md", "&download=1")}">Markdown</a>
    </div>
  </div>`;
}

async function renderListen(p, el) {
  let script = null;
  try { script = await api(`/projects/${enc(p.id)}/script`); } catch (_) { /* transcript-only project */ }
  const has = (name) => p.files.includes(name);
  const timing = script?.timing || {};
  const chapters = timing.chapters || [];
  const turns = script?.dialogue?.dialogue || [];
  const starts = timing.turn_starts || [];
  const downloads = [
    ["audio", "פודקאסט (MP3)"], ["video", "וידאו (MP4)"], ["transcript_srt", "תמלול (SRT)"], ["transcript_txt", "תמלול (טקסט)"],
  ].filter(([name]) => has(name)).map(([name, label]) => `<a class="btn" href="${fileUrl(p.id, name, true)}">${label}</a>`).join("");

  el.innerHTML = `
    <div class="stack">
      ${has("audio") ? `<div class="card player">
        <audio id="audio" controls preload="metadata" src="${fileUrl(p.id, "audio")}"></audio>
        <div class="now-playing muted" id="now"></div>
        ${chapters.length ? `<div class="chapters" style="position:static">${chapters.map((c) =>
          `<button data-seek="${c.start}"><bdi>${esc(c.title)}</bdi><span class="time ltr">${fmtTime(c.start)}</span></button>`).join("")}</div>` : ""}
      </div>` : rank(p.status) >= 3 ? `<div class="card row" style="justify-content:space-between"><span>התסריט מוכן. הפודקאסט עוד לא הופק.</span>
        <button class="btn primary" data-job="audio" ${p.running ? "disabled" : ""}>הפק את הפודקאסט</button></div>` : ""}
      ${has("audio") && !has("video") ? `<div class="card row" style="justify-content:space-between"><div><b>וידאו</b><div class="help">הפודקאסט על רקע תמונות שאתה בוחר, עם הלוגו והגופנים שלך.</div></div>
          <a class="btn" href="#/p/${enc(p.id)}/video">לבניית הווידאו</a></div>` : ""}
      ${has("audio") || has("summary_html") ? summaryCard(p) : ""}
      ${has("audio") ? `<div id="delivery-card"></div>` : ""}
      <div class="card stack"><h2>הורדות</h2>
        <div class="files">${downloads}${script ? `<a class="btn" href="/api/projects/${enc(p.id)}/script.txt?t=${enc(TOKEN)}">תסריט (טקסט)</a>` : ""}</div>
      </div>
      ${logBlock(p)}
    </div>`;
  bindJobs(p);
  paintDeliveryCard(p);

  const audio = document.getElementById("audio");
  if (!audio) return;
  el.querySelectorAll("[data-seek]").forEach((b) => { b.onclick = () => { audio.currentTime = Number(b.dataset.seek); audio.play(); }; });
  const now = document.getElementById("now");
  let shown = -1;
  audio.ontimeupdate = () => {
    let index = -1;
    for (let i = 0; i < starts.length && starts[i] <= audio.currentTime; i++) index = i;
    if (index === shown || index < 0 || !turns[index]) return;
    shown = index;
    now.innerHTML = `<b>${esc(SPEAKERS[turns[index].speaker] || turns[index].speaker)}:</b> ${esc(turns[index].text)}`;
    el.querySelectorAll("[data-seek]").forEach((b) => {
      const start = Number(b.dataset.seek);
      const next = chapters.find((c) => c.start > start);
      b.classList.toggle("active", audio.currentTime >= start && (!next || audio.currentTime < next.start));
    });
  };
}

// ------------------------------------------------------------------ settings

const WRITERS = {
  gemini: ["Gemini", "מומלץ. מפתח אחד מספיק לכתיבה, לקולות ולתמונות. יש מסלול חינמי, אבל בו Google עשויה להשתמש בתוכן כדי לשפר את המוצרים שלה."],
  azure: ["Azure OpenAI", "מתאים אם כבר יש לך משאב Azure OpenAI עם מודל פרוס."],
  local: ["מודל מקומי (Ollama)", "חינמי, עובד בלי אינטרנט ושום תוכן לא יוצא מהמחשב. הכתיבה בעברית חלשה משמעותית מזו של Gemini."],
};
const READERS = {
  elevenlabs: ["ElevenLabs", "מומלץ. שני המנחים מוקלטים יחד, כשיחה אחת. הכי טבעי, עם קולות מהחשבון שלך."],
  gemini: ["Gemini", "שיחה טבעית, עם אותו מפתח Gemini. 30 קולות מוכנים לבחירה."],
  azure: ["Azure Speech", "כל שורה מוקלטת לבד ולכן פחות טבעי. יש מסלול חינמי."],
};
const GROUP_INFO = {
  gemini: ["Gemini", "יוצרים מפתח ב-aistudio.google.com/apikey"],
  elevenlabs: ["ElevenLabs", "מפתח מהחשבון ב-elevenlabs.io, וקול לכל מנחה"],
  openai: ["Azure OpenAI", "את הפרטים מעתיקים מהדף Keys and Endpoint של המשאב"],
  azure: ["Azure Speech", "הקולות העבריים של Microsoft"],
  local: ["המודל המקומי (Ollama)", "בודק עובדות ומנקה את התמלול בלי עלות. אפשר גם לבחור בו ככותב."],
  transcribe: ["תמלול", "האיכות שנבחרת מראש לכל תמלול חדש (אפשר לשנות לכל קובץ), וכמה תמלולים רצים יחד ברקע. יותר במקביל = יותר זיכרון ומעבד; במחשב בלי כרטיס מסך, 1 בדרך כלל הכי מהיר בסך הכול."],
  podcast: ["ברירות מחדל לפודקאסט", ""],
};
const STYLE_HE = {
  Bright: "בהיר", Upbeat: "אנרגטי", Informative: "מסביר", Firm: "יציב", Excitable: "נלהב", Youthful: "צעיר",
  Breezy: "קליל", "Easy-going": "נינוח", Breathy: "רך ונושם", Clear: "צלול", Smooth: "חלק", Gravelly: "צרוד",
  Soft: "רך", Even: "מאוזן", Mature: "בוגר", Forward: "ישיר", Friendly: "ידידותי", Casual: "יומיומי",
  Gentle: "עדין", Lively: "חי", Knowledgeable: "בקיא", Warm: "חם",
};

function statusItems(s) {
  const item = (title, ok, text, neutral = false) => `<div class="card card-tight status-item">
    <div class="title">${title}<span class="badge ${ok ? "ok" : neutral ? "" : "bad"}">${ok ? "תקין" : neutral ? "לא פעיל" : "חסר"}</span></div>
    <div class="help">${text}</div></div>`;
  const w = s.writer || {};
  const gpu = s.gpu?.name ? `${esc(s.gpu.name)} (${esc(s.gpu.memory_gb)}GB)` : "לא נמצא כרטיס NVIDIA. התמלול ירוץ על המעבד.";
  const local = s.local_llm.available ? `${esc(s.local_llm.model)} פעיל`
    : s.local_llm.ollama_installed ? `Ollama מותקן, אבל המודל ${esc(s.local_llm.model)} לא הורד` : "Ollama לא מותקן. הבדיקות ירוצו אצל הכותב";
  return `<div class="status-grid">
    ${item("כתיבת התסריט", w.ready, w.ready ? `${esc(WRITERS[w.provider]?.[0] || w.provider)} · ${esc(w.model)}` : `חסר מפתח ל-${esc(WRITERS[w.provider]?.[0] || w.provider)}`)}
    ${item("קולות", s.voices.ready, s.voices.ready ? esc(READERS[s.voices.provider]?.[0] || s.voices.provider)
      : s.voices.missing_voices ? "בחר קול לרועי ולנועה" : "חסר מפתח לשירות הקולות")}
    ${item("מתמלל עברית", s.transcriber.installed, s.transcriber.installed ? (s.transcriber.hebrew_model ? "המודל העברי מותקן" : "המודל העברי יירד בתמלול הראשון (כ-1.6GB)") : "לא הותקן. הרץ את 'תיקון התקנה'")}
    ${item("מודל מקומי", s.local_llm.available, local, true)}
    ${item("ffmpeg", s.ffmpeg, s.ffmpeg ? "עיבוד שמע ווידאו" : "חסר. הרץ את 'תיקון התקנה'")}
    ${item("כרטיס מסך", Boolean(s.gpu?.name), gpu, true)}
  </div>`;
}

function choiceCards(kind, options, selected) {
  return Object.entries(options).map(([value, [title, text]]) => `
    <button class="choice ${value === selected ? "active" : ""}" data-choice="${kind}" data-value="${value}" aria-pressed="${value === selected}">
      <b>${esc(title)}</b><span>${esc(text)}</span></button>`).join("");
}

/* Settings card: where each podcast's folder goes, and the Google Drive connection. */
async function paintDeliverySettings() {
  const box = document.getElementById("delivery-settings");
  if (!box) return;
  // Back from Google's sign-in page: say how it went, then drop the note from the address
  const query = new URLSearchParams(location.hash.split("?")[1] || "");
  if (query.get("drive") === "connected") toast("Google Drive מחובר");
  if (query.get("drive_error")) toast(query.get("drive_error"), true);
  if (query.toString()) history.replaceState(null, "", "#/settings");

  let view;
  try { view = await api("/delivery"); } catch (err) { box.innerHTML = ""; return; }
  const { prefs, drive } = view;
  box.innerHTML = `<div class="card stack">
    <div><h2>תיקיית התוצרים</h2>
      <div class="help">לכל פודקאסט נוצרת תיקייה משלו: הרצאת המקור, התמלול, הפודקאסט, הווידאו וסיכום השיעור.
        פודקאסט מהורדה לתיקייה שבחרת נשמר שם, ליד השיעורים. הווידאו המקורי מקושר ולא מועתק, כך שאינו תופס מקום נוסף.</div></div>
    ${view.local ? `<div class="folder-row"><input type="text" id="out-root" class="ltr" spellcheck="false" value="${esc(prefs.output_root)}" placeholder="${esc(view.default_root)}">
      <button class="btn" id="out-pick" type="button">בחר תיקייה…</button>
      <button class="btn ghost" id="out-default" type="button">ברירת מחדל</button></div>`
      : `<div class="muted small"><bdi dir="ltr">${esc(prefs.output_root || view.default_root)}</bdi></div>`}
  </div>
  <div class="card stack">
    <div><h2>Google Drive</h2>
      <div class="help">כל תיקיית פרק עולה ל-Drive, לתיקייה בשם שתבחר. ההרשאה מצומצמת: האפליקציה רואה רק קבצים שהיא עצמה יצרה, ולא את שאר ה-Drive שלך.
        פרטי ההתחברות נשמרים רק במחשב הזה.</div></div>
    ${!drive.client ? `
      <label class="field"><span>קובץ OAuth client (JSON) מ-Google Cloud Console, מסוג Desktop app</span>
        <input type="file" id="g-client" accept=".json,application/json"></label>
      <p class="help" style="margin:0">ב-console.cloud.google.com: מפעילים את Google Drive API, יוצרים OAuth client ID מסוג Desktop app ומורידים את קובץ ה-JSON.</p>`
    : !drive.connected ? `
      <div class="row" style="flex-wrap:wrap"><button class="btn primary" id="g-connect">התחבר ל-Google Drive</button>
        <span class="muted small">קובץ ה-client נטען${drive.project ? ` (<bdi dir="ltr">${esc(drive.project)}</bdi>)` : ""}.</span></div>
      <p class="help" style="margin:0">ייפתח דף ההתחברות של Google. אם מופיעה אזהרה ש"Google לא אימתה את האפליקציה", זו האפליקציה שלך: לוחצים "מתקדם" ו"המשך".
        אם החשבון לא מורשה, מוסיפים אותו כ-Test user במסך ה-OAuth consent.</p>`
    : `
      <div class="row" style="justify-content:space-between;flex-wrap:wrap">
        <span>מחובר${drive.email ? ` כ-<b><bdi dir="ltr">${esc(drive.email)}</bdi></b>` : ""} <span class="badge ok">פעיל</span></span>
        <button class="btn ghost danger" id="g-disconnect">נתק</button></div>
      <label class="row"><input type="checkbox" id="g-auto" ${prefs.drive_auto ? "checked" : ""}> להעלות אוטומטית כל פרק שמסתיים (וגם את הווידאו כשהוא מוכן)</label>
      <label class="field"><span>שם התיקייה ב-Drive</span><input type="text" id="g-folder" maxlength="60" value="${esc(prefs.drive_folder)}"></label>`}
  </div>`;

  const save = async (changes, message) => {
    try { await api("/delivery", { method: "PUT", body: changes }); if (message) toast(message); } catch (err) { toast(err.message, true); }
    paintDeliverySettings();
  };
  const root = document.getElementById("out-root");
  if (root) {
    root.onchange = () => save({ output_root: root.value.trim() }, "תיקיית התוצרים נשמרה");
    document.getElementById("out-default").onclick = () => save({ output_root: "" }, "חזרה לברירת המחדל");
    document.getElementById("out-pick").onclick = async (e) => {
      e.target.disabled = true;
      try {
        const { folder } = await api("/delivery/pick-folder", { method: "POST" });
        if (folder) await save({ output_root: folder }, "תיקיית התוצרים נשמרה");
      } catch (err) { toast(err.message, true); } finally { e.target.disabled = false; }
    };
  }
  document.getElementById("g-client")?.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const res = await authFetch("/api/delivery/google/client", { method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text() });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "הקובץ לא נשמר");
      toast("קובץ ה-client נשמר");
    } catch (err) { toast(err.message, true); }
    paintDeliverySettings();
  });
  document.getElementById("g-connect")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { location.href = (await api("/delivery/google/connect", { method: "POST" })).url; }
    catch (err) { e.target.disabled = false; toast(err.message, true); }
  });
  document.getElementById("g-disconnect")?.addEventListener("click", () =>
    confirm("לנתק את Google Drive? ההרשאה תבוטל גם בחשבון Google. הקבצים שכבר הועלו נשארים ב-Drive.")
      && api("/delivery/google/disconnect", { method: "POST" }).then(() => { toast("Google Drive נותק"); paintDeliverySettings(); }, (err) => toast(err.message, true)));
  document.getElementById("g-auto")?.addEventListener("change", (e) => save({ drive_auto: e.target.checked }, e.target.checked ? "העלאה אוטומטית הופעלה" : "העלאה אוטומטית כובתה"));
  document.getElementById("g-folder")?.addEventListener("change", (e) => save({ drive_folder: e.target.value }, "שם התיקייה ב-Drive נשמר"));
}

async function viewSettings() {
  const [status, { fields }] = await Promise.all([refreshStatus(), api("/settings")]);
  const byKey = Object.fromEntries(fields.map((f) => [f.key, f]));
  const draft = Object.fromEntries(fields.map((f) => [f.key, f.secret ? "" : (f.value || "")]));
  const sel = {
    // Nothing chosen and nothing working yet: start from the recommended writer
    writer: draft.LLM_PROVIDER || (status.writer?.ready ? status.writer.provider : "gemini"),
    voices: draft.DEFAULT_TTS_PROVIDER && draft.DEFAULT_TTS_PROVIDER !== "auto" ? draft.DEFAULT_TTS_PROVIDER : status.voices.provider,
  };
  const voiceNames = {};       // ElevenLabs voice id -> name, once the account's voices are loaded
  let elevenVoices = null;
  let geminiVoices = null;

  render(`
    <div class="page-head"><div><h1>הגדרות</h1><div class="sub">המפתחות נשמרים רק במחשב שלך, בקובץ <bdi dir="ltr">.env</bdi> שבתיקיית ההתקנה.</div></div></div>
    <div class="stack">
      ${statusItems(status)}
      <div class="card stack">
        <div><h2>מי כותב את התסריט</h2><div class="help">המודל שמנתח את השיעור, בונה תוכנית וכותב את השיחה. ${guideLink("writer", "?")}</div></div>
        <div class="choices">${choiceCards("writer", WRITERS, sel.writer)}</div>
        <div><h2>מי מקריא</h2><div class="help">השירות שהופך את התסריט לקולות של רועי ונועה. ${guideLink("voices", "?")}</div></div>
        <div class="choices">${choiceCards("voices", READERS, sel.voices)}</div>
      </div>
      <div id="groups" class="stack"></div>
      <div class="row">
        <button class="btn primary lg" id="save">שמור</button>
        <button class="btn lg" id="test">בדוק חיבור</button>
      </div>
      <div id="test-results" class="test-results"></div>
      <div id="delivery-settings" class="stack"></div>
    </div>
    <audio id="voice-player" hidden></audio>`);
  paintDeliverySettings();

  const player = document.getElementById("voice-player");
  const play = (src, button) => {
    document.querySelectorAll("[data-play].playing").forEach((b) => b.classList.remove("playing"));
    player.src = src;
    button?.classList.add("playing");
    player.play().catch((err) => toast(err.message, true));
    player.onended = () => button?.classList.remove("playing");
  };
  const save = () => api("/settings", { method: "PUT", body: { values: collect() } });
  const collect = () => {
    const values = { ...draft, LLM_PROVIDER: sel.writer, DEFAULT_TTS_PROVIDER: sel.voices };
    fields.filter((f) => f.secret && !values[f.key]).forEach((f) => delete values[f.key]);
    return values;
  };

  const visible = (group) => ({
    gemini: sel.writer === "gemini" || sel.voices === "gemini",
    openai: sel.writer === "azure",
    elevenlabs: sel.voices === "elevenlabs",
    azure: sel.voices === "azure",
    local: true, podcast: true, transcribe: true,
  }[group]);
  const fieldVisible = (key) => {
    if (key === "GEMINI_TEXT_MODEL") return sel.writer === "gemini";
    if (key.startsWith("GEMINI_VOICE_") || key.startsWith("ELEVENLABS_VOICE_")) return false;  // shown by the voice picker
    return true;
  };

  const control = (f) => {
    if (f.choices.length) {
      return `<select data-key="${f.key}">${f.choices.map((c) =>
        `<option value="${c}" ${(draft[f.key] || f.choices[0]) === c ? "selected" : ""}>${esc(CHOICE_LABELS[c] || c)}</option>`).join("")}</select>`;
    }
    if (f.secret) {
      return `<input type="password" data-key="${f.key}" autocomplete="off" class="ltr" value="${esc(draft[f.key])}"
        placeholder="${byKey[f.key].is_set ? `מוגדר ${esc(byKey[f.key].hint)}. השאר ריק כדי לא לשנות` : "הדבק כאן"}">`;
    }
    return `<input type="text" data-key="${f.key}" class="ltr" value="${esc(draft[f.key])}" placeholder="${esc(f.placeholder)}">`;
  };

  const voicePicker = (provider) => {
    const keys = provider === "elevenlabs" ? ["ELEVENLABS_VOICE_ROEE", "ELEVENLABS_VOICE_NOA"] : ["GEMINI_VOICE_ROEE", "GEMINI_VOICE_NOA"];
    const label = (id) => provider === "elevenlabs" ? (voiceNames[id] || id || "") : id;
    const chosen = keys.map((k, i) => `<div class="chosen-voice"><span>${i ? "נועה" : "רועי"}:</span>
      <b>${esc(label(draft[k])) || "<span class='muted'>לא נבחר</span>"}</b></div>`).join("");
    const list = provider === "elevenlabs" ? elevenVoices : geminiVoices;
    const rows = (list || []).map((v) => {
      const id = provider === "elevenlabs" ? v.voice_id : v.name;
      const details = provider === "elevenlabs" ? [v.gender, v.accent].filter(Boolean).join(" · ") : (STYLE_HE[v.style] || v.style);
      const canPlay = provider === "gemini" || v.preview_url;
      return `<div class="voice-row">
        <button class="btn icon" data-play="${provider}" data-id="${esc(id)}" ${canPlay ? "" : "disabled"} aria-label="השמע את ${esc(v.name)}">▶</button>
        <div class="voice-name"><b>${esc(v.name)}</b> <span class="muted small">${esc(details)}</span></div>
        <button class="btn ${draft[keys[0]] === id ? "primary" : ""}" data-assign="${keys[0]}" data-id="${esc(id)}">לרועי</button>
        <button class="btn ${draft[keys[1]] === id ? "primary" : ""}" data-assign="${keys[1]}" data-id="${esc(id)}">לנועה</button>
      </div>`;
    }).join("");
    const loader = provider === "elevenlabs" && !elevenVoices
      ? `<button class="btn" data-load-voices>טען את הקולות מהחשבון שלך</button>` : "";
    const hint = provider === "gemini" ? `<p class="help">לחיצה על ▶ מפיקה משפט קצר בעברית בקול הזה. בפעם הראשונה זה לוקח כמה שניות, ואחר כך הדוגמה נשמרת.</p>` : "";
    return `<div class="voice-picker">
      <div class="row">${chosen}<button class="btn" data-preview="${provider}">▶ השמע את שניהם בשיחה</button></div>
      ${loader}${hint}${rows ? `<div class="voice-list">${rows}</div>` : ""}</div>`;
  };

  const renderGroups = () => {
    document.getElementById("groups").innerHTML = Object.entries(GROUP_INFO).filter(([group]) => visible(group)).map(([group, [title, sub]]) => {
      const inputs = fields.filter((f) => f.group === group && fieldVisible(f.key)).map((f) =>
        `<label class="field"><span>${esc(f.label)} ${f.help ? guideLink(f.help, "?") : ""}</span>${control(f)}</label>`).join("");
      const picker = (group === "elevenlabs") || (group === "gemini" && sel.voices === "gemini") ? voicePicker(group) : "";
      return `<div class="card stack"><div><h2>${title}</h2>${sub ? `<div class="help">${sub}</div>` : ""}</div>
        ${inputs ? `<div class="form-group">${inputs}</div>` : ""}${picker}</div>`;
    }).join("");
    bindGroups();
  };

  const bindGroups = () => {
    app.querySelectorAll("#groups [data-key]").forEach((input) => {
      input.oninput = input.onchange = () => { draft[input.dataset.key] = input.value.trim(); };
    });
    app.querySelectorAll("[data-assign]").forEach((b) => {
      b.onclick = () => { draft[b.dataset.assign] = b.dataset.id; renderGroups(); };
    });
    app.querySelectorAll("[data-play]").forEach((b) => {
      b.onclick = async () => {
        if (b.dataset.play === "elevenlabs") {
          const voice = elevenVoices.find((v) => v.voice_id === b.dataset.id);
          return play(voice.preview_url, b);
        }
        b.disabled = true;
        try {
          await save();
          play(`/api/voices/gemini/${enc(b.dataset.id)}/sample?t=${enc(TOKEN)}`, b);
        } catch (err) { toast(err.message, true); }
        b.disabled = false;
      };
    });
    const loader = app.querySelector("[data-load-voices]");
    if (loader) loader.onclick = async () => {
      loader.disabled = true;
      try {
        await save();
        elevenVoices = (await api("/voices/elevenlabs")).voices;
        elevenVoices.forEach((v) => { voiceNames[v.voice_id] = v.name; });
        renderGroups();
      } catch (err) { toast(err.message, true); loader.disabled = false; }
    };
    app.querySelectorAll("[data-preview]").forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        try {
          await save();
          const res = await authFetch("/api/voices/preview", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider: button.dataset.preview }),
          });
          if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "הדוגמה נכשלה");
          play(URL.createObjectURL(await res.blob()), button);
        } catch (err) { toast(err.message, true); }
        button.disabled = false;
      };
    });
  };

  app.querySelectorAll("[data-choice]").forEach((b) => {
    b.onclick = async () => {
      sel[b.dataset.choice] = b.dataset.value;
      app.querySelectorAll(`[data-choice="${b.dataset.choice}"]`).forEach((x) => {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", String(x === b));
      });
      if (sel.voices === "gemini" && !geminiVoices) geminiVoices = (await api("/voices/gemini")).voices;
      renderGroups();
    };
  });

  if (sel.voices === "gemini") geminiVoices = (await api("/voices/gemini")).voices;
  renderGroups();

  document.getElementById("save").onclick = async () => {
    try {
      const result = await save();
      toast(result.settings_ok ? "ההגדרות נשמרו" : `נשמר. ${result.settings_error}`, !result.settings_ok);
      await viewSettings();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById("test").onclick = async (e) => {
    const box = document.getElementById("test-results");
    e.target.disabled = true;
    box.innerHTML = `<div class="muted">בודק...</div>`;
    try {
      await save();
      const results = await api("/settings/test", { method: "POST" });
      const names = { writer_gemini: "Gemini (כתיבה)", writer_local: "מודל מקומי (כתיבה)", openai: "Azure OpenAI", azure_speech: "Azure Speech", elevenlabs: "ElevenLabs" };
      box.innerHTML = Object.entries(results).map(([k, r]) =>
        `<div class="row"><span class="badge ${r.ok ? "ok" : "bad"}">${r.ok ? "תקין" : "נכשל"}</span><b>${names[k] || k}</b><span class="muted">${esc(r.message)}</span></div>`).join("");
      refreshStatus();
    } catch (err) {
      box.innerHTML = "";
      toast(err.message, true);
    }
    e.target.disabled = false;
  };
}


// ------------------------------------------------------------------ tools: transcription and downloads

const MEDIA_STATUS = {
  uploading: ["מעלה", "run"], queued: ["בתור", ""], running: ["בעבודה", "run"], done: ["מוכן", "ok"],
  error: ["נכשל", "bad"], cancelled: ["בוטל", ""],
};
const FILE_KIND = { video: "וידאו", audio: "שמע", subtitles: "כתוביות (SRT)", text: "טקסט" };

// The chain the Tools screen walks through: each finished step offers the next one
const CHAIN = [["download", "הורדה"], ["transcribe", "תמלול"], ["podcast", "פודקאסט"], ["summary", "סיכום שיעור"]];

function chainSteps(active) {
  const at = CHAIN.findIndex(([key]) => key === active);
  return `<div class="stepper chain" aria-label="שלבי העבודה">${CHAIN.map(([, label], i) =>
    `<div class="step ${i < at ? "done" : i === at ? "current" : ""}"><div class="bar"></div>${i + 1}. ${label}</div>`).join("")}</div>`;
}

function openDialog(html) {
  const dialog = document.createElement("dialog");
  dialog.className = "modal";
  dialog.innerHTML = html;
  document.body.appendChild(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  return dialog;
}

function mediaRow(item, local = false) {
  const [label, cls] = MEDIA_STATUS[item.status] || [item.status, ""];
  const running = item.status === "running" || item.status === "queued" || item.status === "uploading";
  const known = typeof item.progress === "number";
  const files = item.files.map((f, i) => `<a class="btn" href="/api/media/${enc(item.id)}/files/${i}?download=1&t=${enc(TOKEN)}"
      title="${esc(f.name)}">${esc(FILE_KIND[f.kind] || f.kind)} <span class="muted small">${fmtSize(f.size)}</span></a>`).join("");
  const done = item.status === "done";
  const actions = [
    running && item.kind === "download" ? `<button class="btn ghost" data-media-cancel="${esc(item.id)}">בטל</button>` : "",
    item.status === "error" || item.status === "cancelled" ? `<button class="btn" data-media-retry="${esc(item.id)}">נסה שוב</button>` : "",
    item.kind === "download" && done && item.files.some((f) => f.kind === "video" || f.kind === "audio")
      ? `<button class="btn primary" data-media-transcribe="${esc(item.id)}">העבר לתמלול ←</button>` : "",
    item.kind === "transcription" && done ? (item.project
      ? `<a class="btn" href="#/p/${enc(item.project)}">לפודקאסט שנוצר</a>`
      : `<a class="btn primary" href="#/tools/podcast?items=${enc(item.id)}">המשך ליצירת פודקאסט ←</a>`) : "",
    !running ? `<button class="btn icon ghost danger" data-media-delete="${esc(item.id)}" aria-label="מחק">✕</button>` : "",
  ].join("");
  const folder = item.folder ? `<div class="muted small folder-line">נשמר בתיקייה: <bdi dir="ltr">${esc(item.folder)}</bdi>
    ${local ? `<button class="link" data-media-reveal="${esc(item.id)}">הצג בתיקייה</button>` : ""}</div>` : "";
  return `<div class="card card-tight stack" style="gap:8px">
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div style="min-width:0"><b><bdi>${esc(item.title)}</bdi></b>
        <div class="muted small" style="overflow-wrap:anywhere"><bdi>${esc(item.kind === "download" ? item.source : `${item.source} · ${item.options.language || "he"} · ${QUALITY_SHORT[item.options.quality] || QUALITY_SHORT.balanced}`)}</bdi></div></div>
      <span class="badge ${cls}">${running ? `<span class="dot"></span>` : ""}${esc(item.stage && running ? item.stage : label)}</span>
    </div>
    ${running ? `<div class="progress ${known ? "" : "indeterminate"}"><div style="width:${known ? Math.round(item.progress * 100) : 30}%"></div></div>` : ""}
    ${item.error ? `<div class="help" style="color:var(--bad)">${esc(item.error)}</div>` : ""}
    ${folder}
    ${files || actions ? `<div class="row" style="flex-wrap:wrap">${files}${actions}</div>` : ""}
  </div>`;
}

/* Step 2 of the chain: confirm which downloaded files to transcribe, and in which language.
   Resolves to "started" (sent to transcription), "view" (everything is already transcribing or done,
   so the user goes to watch it) or false (cancelled). */
function confirmTranscribe(item, languageOptions, allItems = [], qualityOptions = "") {
  const media = item.files.filter((f) => f.kind === "video" || f.kind === "audio");
  const baseName = (name) => name.split("/").pop();
  // A file that already has a transcription (done or on its way) is not sent again by default
  const existing = (f) => allItems.find((i) => i.kind === "transcription" && i.parent === item.id
    && i.source === baseName(f.name) && i.status !== "error" && i.status !== "cancelled");
  const states = media.map(existing);
  const allCovered = media.length > 0 && states.every(Boolean);
  const allDone = allCovered && states.every((s) => s.status === "done");
  const where = item.folder
    ? `התמלול יישמר ליד הווידאו, בתיקייה <bdi dir="ltr">${esc(item.folder)}</bdi>`
    : "התמלול יישמר ליד הווידאו, בתיקיית ההורדה של האפליקציה";
  const startLabel = "אשר והתחל תמלול";
  const viewLabel = allDone ? "לתמלול שנוצר ←" : "להתקדמות התמלול ←";
  const dialog = openDialog(`
    <form method="dialog" class="stack">
      <h2>העברה לתמלול</h2>
      ${allCovered ? `<div class="banner warn"><p>${media.length > 1 ? "כל הקבצים כאן כבר" : "הקובץ הזה כבר"} ${allDone ? "תומלל" : "בתמלול"}.
        אפשר לעבור ${allDone ? "לתמלול" : "להתקדמות שלו"}, או לסמן ולתמלל שוב.</p></div>` : ""}
      <div class="stack" style="gap:6px">${media.map((f, i) => {
        const already = states[i];
        return `<label class="row"><input type="checkbox" name="file" value="${i}" ${already ? "" : "checked"}>
          <bdi class="ellipsis" dir="auto">${esc(baseName(f.name))}</bdi> <span class="muted small">${fmtSize(f.size)}</span>
          ${already ? `<span class="badge ${already.status === "done" ? "ok" : "run"}">${already.status === "done" ? "כבר תומלל" : "כבר בתמלול"}</span>` : ""}</label>`;
      }).join("")}</div>
      <label class="field"><span>שפת ההקלטה</span><select name="language">${languageOptions}</select></label>
      ${qualityOptions ? `<label class="field"><span>איכות תמלול</span><select name="quality">${qualityOptions}</select></label>` : ""}
      <p class="help" style="margin:0">${where}. אפשר להמשיך לעבוד בזמן שהוא רץ.</p>
      <p class="help" id="dialog-message" style="margin:0;color:var(--bad)" hidden>סמן לפחות קובץ אחד לתמלול.</p>
      <div class="row"><button class="btn primary" value="ok" id="dialog-ok">${allCovered ? viewLabel : startLabel}</button>
        <button class="btn ghost" value="cancel" formnovalidate>ביטול</button></div>
    </form>`);
  const form = dialog.querySelector("form");
  const okButton = dialog.querySelector("#dialog-ok");
  const message = dialog.querySelector("#dialog-message");
  const checked = () => [...form.querySelectorAll("[name=file]:checked")].map((c) => Number(c.value));
  form.querySelectorAll("[name=file]").forEach((box) => {
    box.onchange = () => {
      message.hidden = true;
      okButton.textContent = !checked().length && allCovered ? viewLabel : startLabel;
    };
  });
  form.addEventListener("submit", (e) => {
    // Nothing ticked and nothing to watch: say so inside the dialog instead of closing it
    if (e.submitter?.value === "ok" && !checked().length && !allCovered) {
      e.preventDefault();
      message.hidden = false;
    }
  });
  return new Promise((resolve, reject) => {
    dialog.addEventListener("close", async () => {
      if (dialog.returnValue !== "ok") return resolve(false);
      const files = checked();
      if (!files.length) return resolve("view");
      try {
        await api(`/media/${enc(item.id)}/transcribe`, { method: "POST", body: { language: form.language.value, files,
          quality: form.quality ? form.quality.value : undefined } });
        resolve("started");
      } catch (err) { reject(err); }
    });
  });
}

/* Step 3 of the chain: one podcast from one or more finished transcriptions, with a length recommendation. */
async function viewChainPodcast(initial) {
  const status = await refreshStatus();
  const data = await api("/media");
  const done = data.items.filter((i) => i.kind === "transcription" && i.status === "done");
  let selected = (initial || "").split(",").filter((id) => done.some((i) => i.id === id));
  if (!selected.length && done.length) selected = [done[0].id];
  let direction = "auto";
  let estimate = null;
  let titleTouched = false;

  render(`
    <div class="page-head"><div><a href="#/tools/transcribe" class="small">→ לתמלולים</a><h1>יצירת פודקאסט</h1>
      <div class="sub">בחר אילו תמלולים ייכנסו לפרק ובאיזה סדר, ואת הכיוון והאורך. אחר כך תקבל תוכנית לעריכה, ובסוף גם סיכום שיעור.</div></div></div>
    ${chainSteps("podcast")}
    <div class="stack">
      ${status.settings_ok ? "" : setupBanner()}
      <div class="card stack"><div><h2>התמלולים שייכנסו לפרק</h2>
        <div class="help">הפודקאסט נכתב מהטקסט של התמלול, לא מהסרטון עצמו. ליד כל תמלול: אורך ההקלטה המקורית וכמה מילים יש בו.
          אפשר לבחור כמה שיעורים, והם יחוברו לפרק אחד לפי הסדר שכאן.</div></div>
        <div id="sources" class="stack" style="gap:6px"></div></div>
      <div class="card stack" id="recommend"></div>
      <div class="card stack">
        <label class="field"><span>שם הפרק</span><input type="text" id="title" maxlength="80"></label>
        ${optionSelects()}
        <div class="row"><button class="btn primary lg" id="start">צור פודקאסט</button><a class="btn ghost" href="#/tools/transcribe">ביטול</a></div>
      </div>
    </div>`);

  const titleInput = document.getElementById("title");
  const minutesSelect = document.getElementById("opt-minutes");
  titleInput.oninput = () => { titleTouched = true; };
  const defaultTitle = () => {
    const first = done.find((i) => i.id === selected[0]);
    return !first ? "" : selected.length === 1 ? first.title : `${first.title} ועוד ${selected.length - 1}`;
  };
  const setMinutes = (minutes) => {
    let option = [...minutesSelect.options].find((o) => Number(o.value) === minutes);
    if (!option) {
      option = new Option(`כ-${minutes} דקות`, String(minutes));
      minutesSelect.add(option);
    }
    minutesSelect.value = String(minutes);
    document.querySelectorAll("[data-len]").forEach((b) => b.classList.toggle("active", Number(b.dataset.len) === minutes));
  };

  const paintSources = () => {
    const box = document.getElementById("sources");
    const ordered = [...selected.map((id) => done.find((i) => i.id === id)), ...done.filter((i) => !selected.includes(i.id))];
    box.innerHTML = ordered.length ? ordered.map((item) => {
      const position = selected.indexOf(item.id);
      const part = estimate?.parts?.find((p) => p.id === item.id);
      const length = part ? `${part.minutes < 1 ? "פחות מדקה" : `${Math.round(part.minutes)} דק׳`} הקלטה · ${part.words.toLocaleString("he-IL")} מילים` : "";
      return `<div class="source-row ${position >= 0 ? "on" : ""}">
        <label class="row" style="flex:1;min-width:0;align-items:flex-start"><input type="checkbox" data-source="${esc(item.id)}" ${position >= 0 ? "checked" : ""}>
          ${position >= 0 ? `<span class="badge ok">${position + 1}</span>` : ""}
          <span style="min-width:0"><span class="badge">תמלול</span> <bdi class="ellipsis">${esc(item.title)}</bdi>
            <span class="muted small" style="display:block;overflow-wrap:anywhere">מהקובץ: <bdi dir="auto">${esc(item.source)}</bdi>${length ? ` · ${length}` : ""}</span></span></label>
        ${position >= 0 && selected.length > 1 ? `
          <button class="btn icon ghost" data-move="-1" data-id="${esc(item.id)}" ${position === 0 ? "disabled" : ""} aria-label="הקדם">↑</button>
          <button class="btn icon ghost" data-move="1" data-id="${esc(item.id)}" ${position === selected.length - 1 ? "disabled" : ""} aria-label="אחר">↓</button>` : ""}
      </div>`;
    }).join("") : `<div class="muted">עוד אין תמלולים שהסתיימו. <a href="#/tools/transcribe">לתמלול</a></div>`;
    box.querySelectorAll("[data-source]").forEach((c) => {
      c.onchange = () => {
        selected = c.checked ? [...selected, c.dataset.source] : selected.filter((id) => id !== c.dataset.source);
        changed();
      };
    });
    box.querySelectorAll("[data-move]").forEach((b) => {
      b.onclick = () => {
        const from = selected.indexOf(b.dataset.id);
        const to = from + Number(b.dataset.move);
        [selected[from], selected[to]] = [selected[to], selected[from]];
        paintSources();
      };
    });
  };

  const paintRecommend = async () => {
    const box = document.getElementById("recommend");
    if (!selected.length) {
      estimate = null;
      box.innerHTML = `<div class="muted">בחר לפחות תמלול אחד כדי לקבל המלצת אורך.</div>`;
      return;
    }
    box.innerHTML = `<div class="muted">מחשב המלצת אורך...</div>`;
    try {
      estimate = await api(`/media/estimate?items=${selected.map(enc).join(",")}`);
    } catch (err) {
      box.innerHTML = `<div class="help" style="color:var(--bad)">${esc(err.message)}</div>`;
      return;
    }
    const recommended = Number(estimate.recommended);
    box.innerHTML = `
      <div><h2>המלצת אורך: כ-${recommended} דקות</h2>
        <div class="help">${esc(estimate.reason)}</div>
        <div class="muted small">${estimate.files > 1 ? `${estimate.files} תמלולים, ` : ""}סך הכול ${estimate.lecture_minutes < 1 ? "פחות מדקה" : `${Math.round(estimate.lecture_minutes)} דקות`} הקלטה,
          ${estimate.parts.reduce((sum, part) => sum + part.words, 0).toLocaleString("he-IL")} מילים בתמלול</div></div>
      <div class="row length-choices">${estimate.choices.map((m) => `<button class="btn" data-len="${Number(m)}">
        ${Number(m) === Math.round(recommended) ? "מומלץ · " : Number(m) < recommended ? "קצר · " : "מקיף · "}${Number(m)} דק׳</button>`).join("")}</div>`;
    minutesSelect.options[0].textContent = `אוטומטי (כ-${recommended} דקות, מומלץ)`;
    box.querySelectorAll("[data-len]").forEach((b) => { b.onclick = () => setMinutes(Number(b.dataset.len)); });
    paintSources();
  };

  const changed = () => {
    if (!titleTouched) titleInput.value = defaultTitle();
    paintSources();
    paintRecommend();
  };
  minutesSelect.onchange = () => document.querySelectorAll("[data-len]").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.len) === Number(minutesSelect.value)));
  bindDirectionCards(document.getElementById("directions"), (key) => {
    direction = key;
    if (key === "brief" && minutesSelect.value === "0") setMinutes(5);
  });

  document.getElementById("start").onclick = async (e) => {
    if (!selected.length) return toast("בחר לפחות תמלול אחד", true);
    e.target.disabled = true;
    try {
      const { project } = await api("/media/podcast", { method: "POST", body: {
        items: selected, title: titleInput.value.trim(), format: direction, minutes: minutesSelect.value,
        voice: document.getElementById("opt-voice").value, focus: document.getElementById("opt-focus").value.trim(),
        language: document.getElementById("opt-language").value,
      } });
      location.hash = `#/p/${enc(project)}`;
    } catch (err) {
      e.target.disabled = false;
      toast(err.message, true);
    }
  };
  changed();
}

async function viewTools(tab) {
  tab = tab === "download" ? "download" : "transcribe";
  let data = await api("/media");
  const languages = (selected = "he") => Object.entries(data.languages)
    .map(([k, v]) => `<option value="${k}" ${k === selected ? "selected" : ""}>${esc(v)}</option>`).join("");
  const qualities = (selected = data.default_quality) => data.transcribe_qualities
    .map(([k, label, note]) => `<option value="${k}" ${k === selected ? "selected" : ""}>${esc(label)} · ${esc(note)}</option>`).join("");

  render(`
    <div class="page-head"><div><h1>כלים</h1>
      <div class="sub">תמלול עברית מקבצי וידאו ושמע, והורדת סרטונים מהרשת. כל תמלול אפשר להפוך לפודקאסט בלחיצה.</div></div></div>
    ${chainSteps(tab === "download" ? "download" : "transcribe")}
    <div class="subtabs">
      <button data-tools-tab="transcribe" class="${tab === "transcribe" ? "active" : ""}">תמלול</button>
      <button data-tools-tab="download" class="${tab === "download" ? "active" : ""}">הורדה מהרשת</button>
    </div>
    <div class="stack">
      ${tab === "transcribe" ? `
      <div class="card stack">
        <div class="dropzone" id="t-drop" tabindex="0" role="button">
          <strong>גרור לכאן קבצי וידאו או שמע, כמה שרוצים</strong>
          <span class="muted">או לחץ לבחירה · MP4, MKV, MOV, MP3, M4A, WAV · הם יתומללו אחד אחרי השני</span>
        </div>
        <input type="file" id="t-files" hidden multiple accept="video/*,audio/*">
        <div class="form-group">
          <label class="field"><span>שפת ההקלטה</span><select id="t-lang">${languages()}</select></label>
          <label class="field"><span>איכות תמלול</span><select id="t-quality">${qualities()}</select></label>
        </div>
        <p class="help" style="margin:0">עברית: המודל של ivrit.ai. שפה אחרת או זיהוי אוטומטי: מודל Whisper הכללי (הורדה חד-פעמית של כ-1.6GB).
          התמלול רץ על כרטיס המסך כשיש, המחשב לא נכנס לשינה באמצע, והתוצאה היא קובץ כתוביות (SRT) וטקסט.</p>
        <p class="help" style="margin:0">רצים עד ${data.parallel} ${data.parallel > 1 ? "תמלולים" : "תמלול"} במקביל; השאר ממתינים בתור. <a href="#/settings">משנים בהגדרות</a>.</p>
        <div id="t-upload" class="muted small"></div>
      </div>` : `
      <div class="card stack">
        <label class="field"><span>קישורים (אחד בכל שורה, עד 50)</span>
          <textarea id="d-urls" rows="3" class="ltr" placeholder="https://www.youtube.com/watch?v=...&#10;https://johnbryce.sylla.academy/..."></textarea></label>
        <div class="form-group">
          <label class="field"><span>איכות</span><select id="d-quality">${data.qualities
            .map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
          <label class="field"><span>עוגיות התחברות (לאתרי קורסים)</span><select id="d-cookies"><option value="">בלי</option>${data.cookies
            .map((c) => `<option value="${esc(c.id)}">${esc(c.id)}${c.expired ? " (פג תוקף)" : ""}</option>`).join("")}</select></label>
        </div>
        ${data.local ? `<div class="field"><span>איפה לשמור</span>
          <div class="folder-row"><input type="text" id="d-folder" class="ltr" placeholder="בתוך האפליקציה (ברירת מחדל)" spellcheck="false" autocomplete="off">
            <button class="btn" id="d-folder-pick" type="button">בחר תיקייה…</button>
            <button class="btn ghost" id="d-folder-clear" type="button">ברירת מחדל</button></div>
          <span class="help">הסרטון יישמר כאן, והתמלול שלו יישמר לידו. בסוף גם הפודקאסט וסיכום השיעור יועתקו לכאן.</span></div>` : ""}
        <div class="stack" style="gap:6px">
          <label class="row"><input type="checkbox" id="d-playlist"> כל הפלייליסט או הקורס בקישור (ממוספר לפי הסדר)</label>
          <label class="row"><input type="checkbox" id="d-subs" checked> כתוביות בעברית ובאנגלית, כשיש</label>
          <label class="row" style="flex-wrap:wrap"><input type="checkbox" id="d-transcribe"> לתמלל אוטומטית אחרי ההורדה
            <select id="d-lang" style="width:auto">${languages()}</select>
            <select id="d-tquality" style="width:auto" aria-label="איכות תמלול">${data.transcribe_qualities
              .map(([k, label]) => `<option value="${k}" ${k === data.default_quality ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
        </div>
        <details><summary class="small">מתקדם: כתובת מפנה (Referer)</summary>
          <label class="field"><span>לסרטון מוטמע שמתנגן רק מתוך אתר מסוים</span>
            <input type="url" id="d-referer" class="ltr" placeholder="https://"></label></details>
        <div class="row"><button class="btn primary lg" id="d-start">הורד</button>
          <span class="help">מורידים רק תוכן שמותר לך להוריד, למשל קורסים שאתה רשום אליהם.</span></div>
      </div>
      <details class="card stack" id="cookie-box"><summary><b>עוגיות התחברות</b> <span class="muted small">(${data.cookies.length} שמורות)</span></summary>
        <p class="help">לאתרים שדורשים התחברות (כמו Sylla): מתקינים בדפדפן תוסף כמו "Get cookies.txt LOCALLY", נכנסים לאתר מחוברים,
          מייצאים את קובץ העוגיות ומעלים אותו כאן. הקובץ נשמר רק במחשב שלך ולא מוצג שוב. כשהוא פג, מייצאים מחדש.</p>
        <div class="stack" style="gap:6px">${data.cookies.map((c) => `<div class="row" style="justify-content:space-between">
          <div><b>${esc(c.id)}</b> <span class="muted small"><bdi>${esc(c.domains.slice(0, 3).join(", "))}</bdi> · עודכן ${esc(fmtDate(c.updated))}</span>
            ${c.login_expires ? `<span class="badge ${c.expired ? "bad" : "ok"}">${c.expired ? "פג תוקף" : `בתוקף עד ${esc(fmtDate(c.login_expires))}`}</span>` : ""}</div>
          <button class="btn icon ghost danger" data-cookie-delete="${esc(c.id)}" aria-label="מחק">✕</button></div>`).join("")}</div>
        <div class="form-group">
          <label class="field"><span>שם</span><input type="text" id="c-name" maxlength="40" placeholder="למשל sylla"></label>
          <label class="field"><span>קובץ cookies.txt</span><input type="file" id="c-file" accept=".txt"></label>
        </div>
        <label class="field"><span>או הדבק את התוכן</span><textarea id="c-text" rows="3" class="ltr"></textarea></label>
        <div><button class="btn" id="c-save">שמור עוגיות</button></div>
      </details>`}
      <div id="media-list" class="stack"></div>
    </div>`);

  const paintList = () => {
    const kind = tab === "download" ? "download" : "transcription";
    const items = data.items.filter((i) => i.kind === kind);
    const box = document.getElementById("media-list");
    if (!box) return;
    box.innerHTML = items.length ? items.map((item) => mediaRow(item, data.local)).join("")
      : `<div class="muted">${tab === "download" ? "עוד אין הורדות." : "עוד אין תמלולים."}</div>`;
    bindRows();
    if (data.items.some((i) => ["queued", "running", "uploading"].includes(i.status))) schedule(refresh, 1500);
  };
  const refresh = async () => {
    if (!location.hash.startsWith("#/tools")) return;
    try { data = await api("/media"); paintList(); } catch (_) { schedule(refresh, 3000); }
  };
  const act = async (fn, message) => {
    try { await fn(); if (message) toast(message); await refresh(); } catch (err) { toast(err.message, true); }
  };
  const bindRows = () => {
    app.querySelectorAll("[data-media-cancel]").forEach((b) => { b.onclick = () => act(() => api(`/media/${enc(b.dataset.mediaCancel)}/cancel`, { method: "POST" })); });
    app.querySelectorAll("[data-media-retry]").forEach((b) => { b.onclick = () => act(() => api(`/media/${enc(b.dataset.mediaRetry)}/retry`, { method: "POST" })); });
    app.querySelectorAll("[data-media-delete]").forEach((b) => {
      b.onclick = () => confirm("למחוק את הפריט ואת הקבצים שלו?") && act(() => api(`/media/${enc(b.dataset.mediaDelete)}`, { method: "DELETE" }));
    });
    app.querySelectorAll("[data-media-transcribe]").forEach((b) => {
      b.onclick = async () => {
        const item = data.items.find((i) => i.id === b.dataset.mediaTranscribe);
        try {
          const result = await confirmTranscribe(item, languages(), data.items, qualities());
          if (result) {
            toast(result === "view" ? "הקובץ כבר בתמלול. הנה ההתקדמות" : "נשלח לתמלול");
            location.hash = "#/tools/transcribe";
          }
        } catch (err) { toast(err.message, true); }
      };
    });
    app.querySelectorAll("[data-media-reveal]").forEach((b) => {
      b.onclick = () => act(() => api(`/media/${enc(b.dataset.mediaReveal)}/reveal`, { method: "POST" }));
    });
  };

  app.querySelectorAll("[data-tools-tab]").forEach((b) => { b.onclick = () => { location.hash = `#/tools/${b.dataset.toolsTab}`; }; });

  if (tab === "transcribe") {
    const input = document.getElementById("t-files");
    const drop = document.getElementById("t-drop");
    const send = async (files) => {
      const status = document.getElementById("t-upload");
      for (const [i, file] of [...files].entries()) {
        status.textContent = `מעלה ${i + 1} מתוך ${files.length}: ${file.name}`;
        try {
          await uploadRaw(`/media/upload?language=${enc(document.getElementById("t-lang").value)}&quality=${enc(document.getElementById("t-quality").value)}`, file);
        } catch (err) { toast(`${file.name}: ${err.message}`, true); }
        await refresh();
      }
      status.textContent = "";
      input.value = "";
    };
    drop.onclick = () => input.click();
    drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") input.click(); };
    input.onchange = () => input.files.length && send(input.files);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files.length) send(e.dataTransfer.files); };
  } else {
    // The cookies choice is kept between visits, and a pasted link from a site that has saved cookies
    // selects them (the list itself is on disk; only the picker used to start empty every time)
    const cookieSelect = document.getElementById("d-cookies");
    const cookieIds = new Set(data.cookies.map((c) => c.id));
    try {
      const last = localStorage.getItem("mbs-download-cookies");
      if (last && cookieIds.has(last)) cookieSelect.value = last;
      else if (data.cookies.length === 1 && !data.cookies[0].expired) cookieSelect.value = data.cookies[0].id;
    } catch (_) { /* storage blocked */ }
    cookieSelect.addEventListener("change", () => {
      try { localStorage.setItem("mbs-download-cookies", cookieSelect.value); } catch (_) { /* storage blocked */ }
    });
    document.getElementById("d-urls").addEventListener("input", (e) => {
      const hosts = e.target.value.split(/\s+/).map((u) => { try { return new URL(u).hostname; } catch (_) { return ""; } }).filter(Boolean);
      const match = data.cookies.find((c) => !c.expired && (c.domains || []).some((d) =>
        hosts.some((h) => h === d.replace(/^\./, "") || h.endsWith(`.${d.replace(/^\./, "")}`))));
      if (match) cookieSelect.value = match.id;
    });
    // Where downloads go: the app's own folder, or a folder the user picks (remembered between visits)
    const folderInput = document.getElementById("d-folder");
    if (folderInput) {
      try { folderInput.value = localStorage.getItem("mbs-download-folder") || ""; } catch (_) { /* storage blocked */ }
      const remember = () => { try { localStorage.setItem("mbs-download-folder", folderInput.value.trim()); } catch (_) { /* storage blocked */ } };
      folderInput.addEventListener("change", remember);
      document.getElementById("d-folder-pick").onclick = async (e) => {
        e.target.disabled = true;
        try {
          const { folder } = await api("/media/pick-folder", { method: "POST", body: { start: folderInput.value.trim() } });
          if (folder) { folderInput.value = folder; remember(); toast("התיקייה נבחרה"); }
        } catch (err) { toast(err.message, true); } finally { e.target.disabled = false; }
      };
      document.getElementById("d-folder-clear").onclick = () => { folderInput.value = ""; remember(); };
    }
    document.getElementById("d-start").onclick = (e) => act(async () => {
      e.target.disabled = true;
      try {
        await api("/media/download", { method: "POST", body: {
          folder: folderInput ? folderInput.value.trim() : "",
          urls: document.getElementById("d-urls").value, quality: document.getElementById("d-quality").value,
          cookies: document.getElementById("d-cookies").value, playlist: document.getElementById("d-playlist").checked,
          subtitles: document.getElementById("d-subs").checked, transcribe: document.getElementById("d-transcribe").checked,
          language: document.getElementById("d-lang").value, transcribe_quality: document.getElementById("d-tquality").value, referer: document.getElementById("d-referer").value.trim(),
        } });
        document.getElementById("d-urls").value = "";
      } finally { e.target.disabled = false; }
    }, "ההורדה התחילה");
    document.getElementById("c-save").onclick = () => act(async () => {
      const file = document.getElementById("c-file").files[0];
      const text = file ? await file.text() : document.getElementById("c-text").value;
      if (!text.trim()) throw new Error("בחר קובץ או הדבק את התוכן");
      const res = await authFetch(`/api/media/cookies?name=${enc(document.getElementById("c-name").value.trim())}`, {
        method: "POST", headers: { "Content-Type": "text/plain" }, body: text,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "השמירה נכשלה");
      await viewTools("download");
      document.getElementById("cookie-box").open = true;
    }, "העוגיות נשמרו");
    app.querySelectorAll("[data-cookie-delete]").forEach((b) => {
      b.onclick = () => confirm("למחוק את קובץ העוגיות?") && act(async () => {
        await api(`/media/cookies/${enc(b.dataset.cookieDelete)}`, { method: "DELETE" });
        await viewTools("download");
      });
    });
  }
  paintList();
}

// ------------------------------------------------------------------ theme

function applyLook(prefs) {
  const root = document.documentElement;
  if (prefs.theme && prefs.theme !== "auto") root.dataset.theme = prefs.theme; else delete root.dataset.theme;
  root.dataset.accent = prefs.accent || "teal";
  try { localStorage.setItem("mbs-look", JSON.stringify({ theme: prefs.theme, accent: prefs.accent })); } catch (_) { /* blocked */ }
  const toggle = document.getElementById("theme-toggle");
  toggle.textContent = THEME_ICONS[prefs.theme] || THEME_ICONS.auto;
  toggle.title = `ערכת נושא: ${THEME_NAMES[prefs.theme] || THEME_NAMES.auto}`;
  // "No logo" hides it in the header too; /logo still serves the default so the tab keeps an icon
  document.getElementById("brand-logo").hidden = prefs.logo === "none";
  state.look = prefs;
}

async function saveLook(changes) {
  const view = await api("/brand", { method: "PUT", body: { prefs: changes } });
  applyLook(view.prefs);
  if ("logo" in changes) document.getElementById("brand-logo").src = `/logo?v=${Date.now()}`;
  return view;
}

document.getElementById("theme-toggle").onclick = async () => {
  const order = ["auto", "light", "dark"];
  const next = order[(order.indexOf(state.look?.theme || "auto") + 1) % order.length];
  applyLook({ ...(state.look || {}), theme: next });  // instant, then persist
  try { await saveLook({ theme: next }); toast(`ערכת נושא: ${THEME_NAMES[next]}`); } catch (err) { toast(err.message, true); }
  if (location.hash.startsWith("#/look")) viewLook();
};

function uploadRaw(path, file) {
  return authFetch(`/api${path}${path.includes("?") ? "&" : "?"}filename=${enc(file.name)}`, {
    method: "POST", body: file,
  }).then(async (res) => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "ההעלאה נכשלה");
    return data;
  });
}

const loadedFonts = new Set();
function loadFontFace(font) {
  const family = `mbs-${font.id.replace(/[^a-z0-9]/gi, "_")}`;
  if (font.installed && !loadedFonts.has(family)) {
    loadedFonts.add(family);
    const face = new FontFace(family, `url(/api/brand/fonts/${enc(font.id)}/file?t=${enc(TOKEN)})`);
    face.load().then((f) => document.fonts.add(f)).catch(() => {});
  }
  return family;
}

// ------------------------------------------------------------------ look & branding page

async function viewLook() {
  let view = await api("/brand");
  let fontTab = "he";
  let previewKind = "chapter";
  let previewTimer = null;

  render(`
    <div class="page-head"><div><h1>עיצוב ומיתוג</h1><div class="sub">איך האפליקציה נראית, ואיך הווידאו של כל פרק נראה: לוגו, גופנים וצבע.</div></div></div>
    <div class="look-layout">
      <div class="stack">
        <div class="card stack">
          <h2>ערכת נושא לאפליקציה</h2>
          <div class="segmented" role="group" aria-label="ערכת נושא">${Object.entries(THEME_NAMES).map(([k, v]) =>
            `<button data-theme-pick="${k}">${THEME_ICONS[k]} ${v}</button>`).join("")}</div>
          <div><b>צבע מוביל</b> <span class="help">משמש באפליקציה ובפס הצבע של הווידאו</span></div>
          <div class="swatches">${Object.entries(view.accents).map(([k, hex]) =>
            `<button class="swatch" data-accent-pick="${k}" style="background:${hex}" title="${ACCENT_NAMES[k] || k}" aria-label="${ACCENT_NAMES[k] || k}"></button>`).join("")}</div>
        </div>
        <div class="card stack">
          <div class="row" style="justify-content:space-between"><h2>לוגו</h2>
            <button class="btn" id="logo-upload">+ העלה לוגו</button><input type="file" id="logo-file" hidden accept=".png,.jpg,.jpeg,.webp"></div>
          <div class="help">מופיע בפינת הווידאו, בכרטיס הפתיחה ובראש האפליקציה. PNG שקוף נראה הכי טוב.</div>
          <div class="logo-grid" id="logos"></div>
        </div>
        <div class="card stack">
          <div class="row" style="justify-content:space-between"><h2>גופנים לווידאו</h2>
            <button class="btn" id="font-upload">+ העלה גופן</button><input type="file" id="font-file" hidden accept=".ttf,.otf"></div>
          <div class="segmented" role="group" aria-label="שפה">
            <button data-font-tab="he">לפרקים בעברית</button><button data-font-tab="en">לפרקים באנגלית</button></div>
          <div class="help">גופנים חינמיים מ-Google Fonts יורדים בלחיצה. אפשר גם להעלות קובץ TTF או OTF משלך.</div>
          <div class="font-list" id="fonts"></div>
        </div>
      </div>
      <div class="card stack preview-box">
        <div class="row" style="justify-content:space-between"><h2>תצוגה מקדימה</h2>
          <div class="segmented"><button data-preview-kind="intro">פתיחה</button><button data-preview-kind="chapter">פרק</button></div></div>
        <img id="preview" alt="תצוגה מקדימה של שקופית בווידאו">
        <div class="help">כך ייראו השקופיות בווידאו. על תמונות הגלריה של הפרויקט הכותרת יושבת על התמונה עצמה.</div>
      </div>
    </div>`);

  const refreshPreview = () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const res = await authFetch("/api/brand/preview", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: previewKind, language: fontTab }),
        });
        if (!res.ok) throw new Error("התצוגה המקדימה נכשלה");
        const img = document.getElementById("preview");
        if (img) { URL.revokeObjectURL(img.src); img.src = URL.createObjectURL(await res.blob()); }
      } catch (err) { toast(err.message, true); }
    }, 150);
  };

  const paint = () => {
    const prefs = view.prefs;
    app.querySelectorAll("[data-theme-pick]").forEach((b) => b.classList.toggle("active", b.dataset.themePick === prefs.theme));
    app.querySelectorAll("[data-accent-pick]").forEach((b) => b.classList.toggle("active", b.dataset.accentPick === prefs.accent));
    app.querySelectorAll("[data-font-tab]").forEach((b) => b.classList.toggle("active", b.dataset.fontTab === fontTab));
    app.querySelectorAll("[data-preview-kind]").forEach((b) => b.classList.toggle("active", b.dataset.previewKind === previewKind));

    const logoTile = (id, name, src, removable) => `
      <div class="logo-tile ${prefs.logo === id ? "active" : ""}" data-logo="${esc(id)}" role="button" tabindex="0" aria-pressed="${prefs.logo === id}">
        ${src ? `<img src="${src}" alt="">` : `<div class="none">∅</div>`}<span>${esc(name)}</span>
        ${removable ? `<button class="tile-remove" data-logo-remove="${esc(id)}" aria-label="מחק">×</button>` : ""}</div>`;
    document.getElementById("logos").innerHTML = view.logos.map((l) =>
      logoTile(l.id, l.name, `/api/brand/logos/${enc(l.id)}?t=${enc(TOKEN)}`, l.id !== "default")).join("") + logoTile("none", "בלי לוגו", "", false);

    const key = fontTab === "en" ? "font_en" : "font_he";
    const sample = fontTab === "en" ? "Why the model memorizes · 0123" : "המיטוכונדריה: תחנת הכוח של התא";
    const fonts = view.fonts.filter((f) => f.scripts.includes(fontTab));
    const sourceName = { google: "Google Fonts", system: "מותקן במחשב", upload: "הועלה" };
    const row = (f) => {
      const family = loadFontFace(f);
      const active = prefs[key] === f.id;
      const action = !f.installed ? `<button class="btn" data-font-download="${esc(f.id.slice(2))}">הורד</button>`
        : active ? `<span class="badge ok">נבחר</span>` : `<button class="btn" data-font-pick="${esc(f.id)}">בחר</button>`;
      const remove = f.installed && f.source !== "system" ? `<button class="btn icon ghost" data-font-remove="${esc(f.id)}" aria-label="מחק">×</button>` : "";
      return `<div class="font-row ${active ? "active" : ""}">
        <div style="min-width:0"><div class="font-sample" style="font-family:'${family}', var(--fallback, sans-serif)">${f.installed ? esc(sample) : esc(f.name)}</div>
          <div class="font-meta">${esc(f.name)} · ${esc(sourceName[f.source] || f.source)}${f.note ? ` · ${esc(f.note)}` : ""}</div></div>
        <div class="row">${action}${remove}</div></div>`;
    };
    document.getElementById("fonts").innerHTML = `
      <div class="font-row ${prefs[key] === "auto" ? "active" : ""}"><div><b>אוטומטי</b><div class="font-meta">Heebo אם הורד, אחרת Arial מודגש</div></div>
        <div>${prefs[key] === "auto" ? `<span class="badge ok">נבחר</span>` : `<button class="btn" data-font-pick="auto">בחר</button>`}</div></div>
      ${fonts.filter((f) => f.installed).map(row).join("")}
      ${fonts.filter((f) => !f.installed).map(row).join("")}`;
    bind();
  };

  const act = async (fn, okMessage) => {
    try { view = await fn(); paint(); refreshPreview(); if (okMessage) toast(okMessage); } catch (err) { toast(err.message, true); }
  };

  const bind = () => {
    app.querySelectorAll("[data-theme-pick]").forEach((b) => { b.onclick = () => act(() => saveLook({ theme: b.dataset.themePick })); });
    app.querySelectorAll("[data-accent-pick]").forEach((b) => { b.onclick = () => act(() => saveLook({ accent: b.dataset.accentPick })); });
    app.querySelectorAll("[data-font-tab]").forEach((b) => { b.onclick = () => { fontTab = b.dataset.fontTab; paint(); refreshPreview(); }; });
    app.querySelectorAll("[data-preview-kind]").forEach((b) => { b.onclick = () => { previewKind = b.dataset.previewKind; paint(); refreshPreview(); }; });
    app.querySelectorAll("[data-logo]").forEach((tile) => {
      tile.onclick = (e) => { if (!e.target.closest("[data-logo-remove]")) act(() => saveLook({ logo: tile.dataset.logo })); };
      tile.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") tile.click(); };
    });
    app.querySelectorAll("[data-logo-remove]").forEach((b) => {
      b.onclick = () => confirm("למחוק את הלוגו?") && act(async () => {
        const result = await api(`/brand/logos/${enc(b.dataset.logoRemove)}`, { method: "DELETE" });
        document.getElementById("brand-logo").src = `/logo?v=${Date.now()}`;
        return result;
      });
    });
    const key = fontTab === "en" ? "font_en" : "font_he";
    app.querySelectorAll("[data-font-pick]").forEach((b) => { b.onclick = () => act(() => saveLook({ [key]: b.dataset.fontPick })); });
    app.querySelectorAll("[data-font-download]").forEach((b) => {
      b.onclick = () => {
        b.disabled = true; b.textContent = "מוריד...";
        act(async () => {
          await api(`/brand/fonts/download/${enc(b.dataset.fontDownload)}`, { method: "POST" });
          return saveLook({ [key]: `g:${b.dataset.fontDownload}` });
        }, "הגופן הורד ונבחר");
      };
    });
    app.querySelectorAll("[data-font-remove]").forEach((b) => {
      b.onclick = () => confirm("למחוק את הגופן מהמחשב?") && act(() => api(`/brand/fonts/${enc(b.dataset.fontRemove)}`, { method: "DELETE" }));
    });
  };

  const logoFile = document.getElementById("logo-file");
  document.getElementById("logo-upload").onclick = () => logoFile.click();
  logoFile.onchange = () => logoFile.files[0] && act(async () => {
    const result = await uploadRaw("/brand/logos", logoFile.files[0]);
    applyLook(result.prefs);
    document.getElementById("brand-logo").src = `/logo?v=${Date.now()}`;
    return result;
  }, "הלוגו נוסף ונבחר");
  const fontFile = document.getElementById("font-file");
  document.getElementById("font-upload").onclick = () => fontFile.click();
  fontFile.onchange = () => fontFile.files[0] && act(() => uploadRaw("/brand/fonts", fontFile.files[0]), "הגופן נוסף. בחר אותו ברשימה.");

  paint();
  refreshPreview();
}

// ------------------------------------------------------------------ project video

const IMAGE_STYLES = { illustration: "איור", photo: "צילום", "3d": "תלת-ממד", watercolor: "צבעי מים" };

async function renderVideo(p, el) {
  let gallery = await api(`/projects/${enc(p.id)}/gallery`);
  const hasAudio = p.files.includes("audio");
  const hasVideo = p.files.includes("video");
  let previewItem = null;
  let saveTimer = null;

  el.innerHTML = `
    <div class="stack">
      ${hasVideo ? `<div class="card"><video controls style="width:100%;border-radius:8px" src="${fileUrl(p.id, "video")}"></video>
        <div class="row" style="margin-top:10px"><a class="btn" href="${fileUrl(p.id, "video", true)}">הורד וידאו (MP4)</a></div></div>` : ""}
      <div class="look-layout">
        <div class="card stack">
          <div class="row" style="justify-content:space-between"><h2>גלריית תמונות</h2>
            <div class="row"><button class="btn" id="img-upload">+ העלה תמונות</button>
              <input type="file" id="img-file" hidden multiple accept=".png,.jpg,.jpeg,.webp"></div></div>
          <div class="help">לחיצה על תמונה מכניסה או מוציאה אותה מהווידאו. המספר הוא הסדר שבו התמונות יופיעו, ואחרי האחרונה חוזרים לראשונה.</div>
          <div class="gallery-grid" id="gallery"></div>
          <div class="card card-tight stack" style="background:var(--surface-2)">
            <b>תמונות AI לפי תוכן הפרק</b>
            <div class="row">
              <label class="field" style="flex:1"><span>כמה</span><select id="ai-count">${[2, 4, 6, 8, 12].map((n) => `<option ${n === 4 ? "selected" : ""}>${n}</option>`).join("")}</select></label>
              <label class="field" style="flex:1"><span>סגנון</span><select id="ai-style">${Object.entries(IMAGE_STYLES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
            </div>
            <button class="btn" data-job-images ${p.running ? "disabled" : ""}>צור תמונות עם Gemini</button>
            <div class="help">כל תמונה נבנית ממקטע בתוכנית הפרק, בלי טקסט בתוך התמונה. כל תמונה היא בקשה בתשלום או ממכסת Gemini שלך.</div>
          </div>
        </div>
        <div class="card stack preview-box">
          <h2>הווידאו</h2>
          <img id="slide-preview" alt="תצוגה מקדימה של שקופית">
          <label class="field"><span>החלפת תמונה כל <b id="rotate-label"></b> שניות</span>
            <div class="range-row"><input type="range" id="rotate" min="3" max="30" step="1"></div></label>
          <div class="help" id="video-summary"></div>
          <div class="help">הלוגו, הגופן והצבע נקבעים ב<a href="#/look">עיצוב ומיתוג</a>.</div>
          ${hasAudio ? `<button class="btn primary lg" data-job="video" ${p.running ? "disabled" : ""}>${hasVideo ? "צור את הווידאו מחדש" : "צור וידאו"}</button>`
            : `<div class="banner warn"><p>קודם מפיקים את הפודקאסט (שמע), ואז את הווידאו.</p></div>`}
        </div>
      </div>
    </div>`;

  const persist = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        gallery = await api(`/projects/${enc(p.id)}/gallery`, { method: "PUT", body: {
          items: gallery.items.map((i) => ({ id: i.id, selected: i.selected !== false })), rotate_seconds: gallery.rotate_seconds } });
      } catch (err) { toast(err.message, true); }
    }, 300);
  };

  const preview = async () => {
    const chosen = gallery.items.filter((i) => i.selected !== false);
    const item = previewItem && gallery.items.find((i) => i.id === previewItem) || chosen[0];
    try {
      const res = await authFetch("/api/brand/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "chapter", project: p.id, image: item?.id || "", title: p.title, language: p.options?.language || "he" }),
      });
      const img = document.getElementById("slide-preview");
      if (res.ok && img) { URL.revokeObjectURL(img.src); img.src = URL.createObjectURL(await res.blob()); }
    } catch (_) { /* preview is best effort */ }
  };

  const paint = () => {
    const chosen = gallery.items.filter((i) => i.selected !== false);
    let number = 0;
    document.getElementById("gallery").innerHTML = gallery.items.length ? gallery.items.map((item, index) => {
      const on = item.selected !== false;
      if (on) number += 1;
      return `<div class="gallery-item ${on ? "on" : "off"}">
        <img src="/api/projects/${enc(p.id)}/gallery/${enc(item.id)}/thumb?t=${enc(TOKEN)}" data-toggle="${esc(item.id)}" alt="" title="${esc(item.prompt || "")}">
        ${on ? `<span class="order">${number}</span>` : ""}
        ${item.source === "ai" ? `<span class="tag">AI</span>` : ""}
        <div class="tools">
          <button data-move="${index}:-1" aria-label="הקדם" ${index === 0 ? "disabled" : ""}>→</button>
          <button data-move="${index}:1" aria-label="אחר" ${index === gallery.items.length - 1 ? "disabled" : ""}>←</button>
          <button data-show="${esc(item.id)}" aria-label="תצוגה מקדימה">👁</button>
          <button data-remove="${esc(item.id)}" aria-label="מחק">✕</button>
        </div></div>`;
    }).join("") : `<div class="muted">עוד אין תמונות. העלה תמונות משלך או צור תמונות AI. בלי תמונות הווידאו יציג רקע בצבע המוביל עם כותרות הפרקים.</div>`;

    document.getElementById("rotate").value = gallery.rotate_seconds;
    document.getElementById("rotate-label").textContent = gallery.rotate_seconds;
    const minutes = p.minutes || 0;
    const shows = minutes ? Math.round((minutes * 60) / gallery.rotate_seconds) : 0;
    document.getElementById("video-summary").textContent = chosen.length
      ? `${chosen.length} תמונות בסבב${shows ? `, כ-${shows} החלפות לאורך הפרק` : ""}.` : "אין תמונות בסבב.";

    el.querySelectorAll("[data-toggle]").forEach((img) => {
      img.onclick = () => {
        const item = gallery.items.find((i) => i.id === img.dataset.toggle);
        item.selected = item.selected === false;
        previewItem = item.selected ? item.id : previewItem;
        paint(); persist(); preview();
      };
    });
    el.querySelectorAll("[data-move]").forEach((b) => {
      b.onclick = () => {
        const [i, d] = b.dataset.move.split(":").map(Number);
        [gallery.items[i], gallery.items[i + d]] = [gallery.items[i + d], gallery.items[i]];
        paint(); persist();
      };
    });
    el.querySelectorAll("[data-show]").forEach((b) => { b.onclick = () => { previewItem = b.dataset.show; preview(); }; });
    el.querySelectorAll("[data-remove]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("למחוק את התמונה מהגלריה?")) return;
        try { gallery = await api(`/projects/${enc(p.id)}/gallery/${enc(b.dataset.remove)}`, { method: "DELETE" }); paint(); preview(); }
        catch (err) { toast(err.message, true); }
      };
    });
  };

  document.getElementById("rotate").oninput = (e) => {
    gallery.rotate_seconds = Number(e.target.value);
    document.getElementById("rotate-label").textContent = e.target.value;
    paint(); persist();
  };
  const imgFile = document.getElementById("img-file");
  document.getElementById("img-upload").onclick = () => imgFile.click();
  imgFile.onchange = async () => {
    const files = [...imgFile.files];
    for (const [i, file] of files.entries()) {
      toast(`מעלה ${i + 1} מתוך ${files.length}...`);
      try { gallery = await uploadRaw(`/projects/${enc(p.id)}/gallery/upload`, file); } catch (err) { toast(`${file.name}: ${err.message}`, true); }
    }
    imgFile.value = "";
    paint(); preview();
  };
  const aiButton = el.querySelector("[data-job-images]");
  aiButton.onclick = async () => {
    aiButton.disabled = true;
    try {
      await api(`/projects/${enc(p.id)}/jobs/images`, { method: "POST", body: {
        count: Number(document.getElementById("ai-count").value), style: document.getElementById("ai-style").value } });
      await viewProject(p.id, "video");
    } catch (err) { aiButton.disabled = false; toast(err.message, true); }
  };
  bindJobs(p);
  paint();
  preview();
}

// ------------------------------------------------------------------ start

setInterval(() => api("/ping", { method: "POST" }).catch(() => {}), 20000);
api("/ping", { method: "POST" }).catch(() => {});
api("/brand").then((view) => applyLook(view.prefs)).catch(() => applyLook({ theme: "auto", accent: "teal" }));
route();