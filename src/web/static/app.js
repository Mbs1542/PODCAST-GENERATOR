"use strict";

/* M.B.S Studio browser UI. Plain JS, hash routes:
   #/ projects · #/new · #/p/<id>[/<tab>] · #/settings
   Everything that comes from the model or from files is escaped with esc() before it
   reaches innerHTML. */

const TOKEN = document.querySelector('meta[name="mbs-token"]').content;
const app = document.getElementById("app");

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
};
const SPEAKERS = { Roee: "רועי", Noa: "נועה" };

const state = { status: null, timer: null };

// ------------------------------------------------------------------ helpers

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const rank = (status) => ORDER.indexOf(status);

async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-MBS-Token": TOKEN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
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
  const section = hash.startsWith("#/settings") ? "settings" : hash.startsWith("#/look") ? "look" : hash.startsWith("#/new") ? "" : "projects";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
  try {
    if (hash.startsWith("#/settings")) await viewSettings();
    else if (hash.startsWith("#/look")) await viewLook();
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

function uploadFile(file, options, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/upload?${new URLSearchParams({ ...options, filename: file.name })}`);
    xhr.setRequestHeader("X-MBS-Token", TOKEN);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
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
  const steps = [["תמלול", 1], ["תוכנית", 3], ["תסריט", 3], ["פודקאסט", 4]];
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
      <div class="card stack"><h2>הורדות</h2>
        <div class="files">${downloads}${script ? `<a class="btn" href="/api/projects/${enc(p.id)}/script.txt?t=${enc(TOKEN)}">תסריט (טקסט)</a>` : ""}</div>
      </div>
      ${logBlock(p)}
    </div>`;
  bindJobs(p);

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
    </div>
    <audio id="voice-player" hidden></audio>`);

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
    local: true, podcast: true,
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
          const res = await fetch("/api/voices/preview", {
            method: "POST", headers: { "X-MBS-Token": TOKEN, "Content-Type": "application/json" },
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


// ------------------------------------------------------------------ theme

function applyLook(prefs) {
  const root = document.documentElement;
  if (prefs.theme && prefs.theme !== "auto") root.dataset.theme = prefs.theme; else delete root.dataset.theme;
  root.dataset.accent = prefs.accent || "teal";
  try { localStorage.setItem("mbs-look", JSON.stringify({ theme: prefs.theme, accent: prefs.accent })); } catch (_) { /* blocked */ }
  const toggle = document.getElementById("theme-toggle");
  toggle.textContent = THEME_ICONS[prefs.theme] || THEME_ICONS.auto;
  toggle.title = `ערכת נושא: ${THEME_NAMES[prefs.theme] || THEME_NAMES.auto}`;
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
  return fetch(`/api${path}${path.includes("?") ? "&" : "?"}filename=${enc(file.name)}`, {
    method: "POST", headers: { "X-MBS-Token": TOKEN }, body: file,
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
        const res = await fetch("/api/brand/preview", {
          method: "POST", headers: { "X-MBS-Token": TOKEN, "Content-Type": "application/json" },
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
    const sourceName = { google: "Google Fonts", system: "מותקן ב-Windows", upload: "הועלה" };
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
      const res = await fetch("/api/brand/preview", {
        method: "POST", headers: { "X-MBS-Token": TOKEN, "Content-Type": "application/json" },
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