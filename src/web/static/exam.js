/* Exam mode screens: setup, progress, practice, results, exports. Uses the helpers in app.js
   (api, render, esc, enc, toast, schedule, fmtDate, etaLine, chainSteps...). Everything shown that
   came from a model or a file is escaped with esc(). */

const EXAM_LEVELS = { easy: "קל", medium: "בינוני", hard: "קשה", mixed: "מעורב", adaptive: "מותאם אליי" };
const RATINGS = { 1: "שוב", 2: "קשה", 3: "טוב", 4: "קל" };
const EXAM_TYPES = { "1": "אמריקאיות בלבד", "0.7": "בעיקר אמריקאיות (70/30)", "0.5": "חצי-חצי", "0": "פתוחות בלבד" };
const EXAM_STYLES = { final: "מבחן סיום אוניברסיטאי", quick: "בדיקה עצמית קצרה", oral: "הכנה למבחן בעל-פה" };
const EXAM_STATUS = { queued: ["בתור", "run"], running: ["בעבודה", "run"], done: ["מוכן", "ok"], error: ["נכשל", "bad"] };
const EXAM_LETTERS = { he: "אבגדה", en: "ABCDE" };
const EXPORTS = [["md", "Markdown"], ["docx", "Word"], ["anki", "Anki (כרטיסיות)"], ["moodle", "Moodle XML"],
  ["gift", "GIFT"], ["qti", "QTI (Canvas)"], ["json", "JSON"]];
const pct = (share) => (share === null || share === undefined ? "–" : `${Math.round(share * 100)}%`);

/* Keyboard: one handler for whichever exam screen is open (practice or review). Keys typed into a text box,
   and Enter on a focused button or link, are left alone. */
let examKeys = null;  // { box: element id, handle(event) -> true when the key was used }
document.addEventListener("keydown", (e) => {
  if (!examKeys || e.altKey) return;
  if (!document.getElementById(examKeys.box)) { examKeys = null; return; }
  const ctrlEnter = e.key === "Enter" && (e.ctrlKey || e.metaKey);
  if ((e.ctrlKey || e.metaKey) && !ctrlEnter) return;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) && !ctrlEnter) return;
  if (/^(BUTTON|A)$/.test(e.target.tagName) && (e.key === "Enter" || e.key === " ") && !ctrlEnter) return;  // a focused control does its own action
  if (examKeys.handle(e)) e.preventDefault();
});
const keyDigit = (e) => { const m = /^Digit([1-9])$/.exec(e.code || "") || /^([1-9])$/.exec(e.key); return m ? Number(m[1]) : 0; };
const keysHint = (parts) => `<div class="keys-hint">מקלדת: ${parts.map(([k, v]) => `<kbd>${k}</kbd> ${v}`).join(" · ")}</div>`;

function whenLabel(iso) {
  const minutes = Math.max(1, Math.round((new Date(iso) - Date.now()) / 60000));
  if (minutes < 60) return `בעוד ${minutes} דק׳`;
  if (minutes < 60 * 24) return `בעוד ${Math.round(minutes / 60)} שע׳`;
  const days = Math.round(minutes / 1440);
  if (days === 1) return "מחר";
  if (days < 31) return `בעוד ${days} ימים`;
  return `בעוד ${Math.round(days / 30)} חודשים`;
}

/* The pieces of a multiple-choice question shared by practice and review */
function mcqOptionsHtml(q, letters, given) {
  const done = given !== undefined;
  return `<div class="stack" style="gap:8px">${q.options.map((o, n) => {
    const state = !done ? "" : o.correct ? "right" : given === o.key ? "wrong" : "";
    return `<button class="option ${state}" data-answer="${esc(o.key)}" ${done ? "disabled" : ""} title="מקש ${n + 1}">
      <span class="letter">${letters["ABCDE".indexOf(o.key)]}</span><span>${esc(o.text)}</span></button>`;
  }).join("")}</div>`;
}

function mcqFeedbackHtml(q, letters, given, links) {
  const chosen = q.options.find((o) => o.key === given);
  const right = Boolean(chosen && chosen.correct);
  return `<div class="banner ${right ? "ok" : "warn"}"><p><b>${right ? "נכון!" : `התשובה הנכונה: ${letters["ABCDE".indexOf(q.answer)]}`}</b>
      ${esc(q.explanation || "")}${chosen && !chosen.correct && chosen.feedback ? `<br><span class="muted">למה ${letters["ABCDE".indexOf(chosen.key)]} שגויה: ${esc(chosen.feedback)}</span>` : ""}</p></div>
    ${links ? `<div class="muted small">בשיעור: ${links}</div>` : ""}`;
}

function sourceLinksHtml(q, projects, titles) {
  return (q.sources || []).map((src) => {
    const project = projects[Number(String(src.lecture || "L1").slice(1)) - 1];
    const title = (titles || {})[src.lecture] || src.lecture || "";
    return project ? `<a href="#/p/${enc(project)}/listen" class="small">${esc(title)} · ${esc(src.time || "")}</a>`
      : `<span class="small">${esc(title)} · ${esc(src.time || "")}</span>`;
  }).join(" · ");
}

async function viewExams() {
  const data = await api("/exams");
  render(`
    <div class="page-head"><div><h1>מוד מבחן</h1>
      <div class="sub">בוחרים שיעורים ומקבלים סיכום לימודי ושאלות תרגול, אמריקאיות או פתוחות, ברמת הקושי ובשפה שבחרת. כל שאלה מבוססת על מה שנאמר בשיעור.</div></div>
      <a class="btn primary" href="#/exam/new">+ מבחן חדש</a></div>
    ${reviewCardHtml(data.review)}
    <div class="stack">${data.exams.length ? data.exams.map((e) => {
      const [label, cls] = EXAM_STATUS[e.status] || [e.status, ""];
      const stats = e.stats && e.stats.final ? ` · ${e.stats.final} שאלות` : "";
      return `<a class="card card-tight row exam-row" href="#/exam/${enc(e.id)}" style="justify-content:space-between">
        <div style="min-width:0"><b><bdi>${esc(e.title)}</bdi></b><div class="muted small">${esc(fmtDate(e.created))}${stats}
          · ${esc(EXAM_LEVELS[e.options.difficulty] || "")}</div></div>
        <span class="badge ${cls}">${e.running ? `<span class="dot"></span>` : ""}${esc(e.running && e.stage ? e.stage : label)}</span></a>`;
    }).join("") : `<div class="card muted">עוד אין מבחנים. לחץ "מבחן חדש" כדי להתחיל.</div>`}</div>`);
}

async function viewExamSetup() {
  const [data, media] = await Promise.all([api("/exams"), api("/media")]);
  const ready = data.projects.filter((p) => p.ready);
  const waiting = data.projects.filter((p) => !p.ready);
  const loose = media.items.filter((i) => i.kind === "transcription" && i.status === "done" && !i.project);
  const chosen = new Set(ready.slice(0, 1).map((p) => p.id));
  const topicState = {};  // concept name -> "focus" | "exclude"
  let difficulty = "mixed";

  render(`
    <div class="page-head"><div><a href="#/exam" class="small">→ למבחנים</a><h1>מבחן חדש</h1>
      <div class="sub">בחר שיעורים, את סוג השאלות ואת הרמה. אחרי היצירה תקבל גם סיכום לימודי של כל השיעורים יחד.</div></div></div>
    <div class="stack">
      <div class="card stack"><div><h2>שיעורים</h2><div class="help">אפשר לבחור כמה שיעורים מאותו קורס. נדרש שיעור שעבר ניתוח (שלב התוכנית בפודקאסט).</div></div>
        <div class="stack" style="gap:6px">${ready.map((p) => `<label class="source-row ${chosen.has(p.id) ? "on" : ""}">
            <input type="checkbox" data-pick="${esc(p.id)}" ${chosen.has(p.id) ? "checked" : ""}> <bdi class="ellipsis">${esc(p.title)}</bdi>
            <span class="muted small">${p.concepts.length} מושגים</span></label>`).join("") || `<div class="muted">אין עדיין שיעורים מנותחים.</div>`}
          ${waiting.map((p) => `<div class="source-row"><bdi class="ellipsis">${esc(p.title)}</bdi>
            <span class="muted small">עוד לא נותח: פתח את הפרויקט והרץ את שלב התוכנית</span></div>`).join("")}
          ${loose.map((i) => `<div class="source-row"><span class="badge">תמלול</span> <bdi class="ellipsis">${esc(i.title)}</bdi>
            <button class="btn" data-analyze="${esc(i.id)}">נתח לשיעור</button></div>`).join("")}
        </div></div>
      <div class="card stack"><h2>השאלות</h2>
        <div class="form-group">
          <label class="field"><span>כמה שאלות</span><input type="number" id="x-count" min="1" max="60" value="10"></label>
          <label class="field"><span>סוג השאלות</span><select id="x-mix">${Object.entries(EXAM_TYPES).map(([k, v]) =>
            `<option value="${k}" ${k === "0.7" ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label class="field"><span>שפת השאלות והסיכום</span><select id="x-lang"><option value="he">עברית</option><option value="en">English</option></select></label>
          <label class="field"><span>תשובות בכל שאלה אמריקאית</span><select id="x-options"><option value="3">3</option><option value="4" selected>4</option><option value="5">5</option></select></label>
          <label class="field"><span>סגנון</span><select id="x-style">${Object.entries(EXAM_STYLES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        </div>
        <div class="field"><span>רמת קושי</span><div class="segmented" id="x-level">${Object.entries(EXAM_LEVELS).map(([k, v]) =>
          `<button type="button" data-level="${k}" class="${k === difficulty ? "active" : ""}">${v}</button>`).join("")}</div>
          <span class="help">קל: זכירה והבנה. בינוני: יישום על דוגמה חדשה. קשה: ניתוח שמשלב מושגים. מעורב: 30% קל, 50% בינוני, 20% קשה. מותאם אליי: התמהיל נקבע לפי התשובות הקודמות שלך כך שתצליח בכ-70%-85%.</span></div>
        <div class="field"><span>התאמה: לחץ על נושא פעם אחת כדי להתמקד בו, ופעם שנייה כדי לדלג עליו</span><div class="chips" id="x-topics"></div></div>
        <label class="field"><span>הוראה חופשית (אופציונלי)</span><textarea id="x-notes" rows="2" maxlength="500"
          placeholder="למשל: המרצה הדגיש הגדרות; שאלו על ההבדלים בין EDA ל-Data Cleaning"></textarea></label>
        <label class="field"><span>שם המבחן (אופציונלי)</span><input type="text" id="x-title" maxlength="120"></label>
      </div>
      <div class="card stack" id="x-cost"></div>
      <div class="row"><button class="btn primary lg" id="x-start">צור מבחן</button><a class="btn ghost" href="#/exam">ביטול</a></div>
    </div>`);

  const paintTopics = () => {
    const names = [...new Set(ready.filter((p) => chosen.has(p.id)).flatMap((p) => p.concepts))];
    document.getElementById("x-topics").innerHTML = names.map((n) => {
      const s = topicState[n];
      return `<button type="button" class="chip topic ${s || ""}" data-topic="${esc(n)}">${s === "focus" ? "★ " : s === "exclude" ? "✕ " : ""}${esc(n)}</button>`;
    }).join("") || `<span class="muted small">בחר שיעור כדי לראות את הנושאים שלו</span>`;
    document.querySelectorAll("[data-topic]").forEach((b) => {
      b.onclick = () => {
        const n = b.dataset.topic;
        topicState[n] = !topicState[n] ? "focus" : topicState[n] === "focus" ? "exclude" : undefined;
        paintTopics();
      };
    });
  };
  const paintCost = async () => {
    const box = document.getElementById("x-cost");
    if (!chosen.size) { box.innerHTML = `<div class="muted">בחר לפחות שיעור אחד.</div>`; return; }
    try {
      const e = await api(`/exams/estimate?projects=${[...chosen].map(enc).join(",")}&count=${Number(document.getElementById("x-count").value) || 10}`);
      const left = e.ils.left === null ? `<a href="#/settings" class="small">הזן יתרה בהגדרות</a>` : `מתוך יתרה של <bdi dir="ltr">₪${e.ils.left.toFixed(2)}</bdi>`;
      box.innerHTML = `<div class="row" style="justify-content:space-between"><b>הערכת עלות</b>
        <span><bdi dir="ltr">₪${e.ils.low.toFixed(2)}–₪${e.ils.high.toFixed(2)}</bdi> מהקרדיט ב-Google · ${left}</span></div>
        <div class="help" style="margin:0">כתיבה, בדיקה עיוורת של כל שאלה ושיפור של שאלות שנפסלו. הסיכום הלימודי נבנה מהניתוח הקיים, בלי עלות.</div>`;
    } catch (err) { box.innerHTML = `<div class="help">${esc(err.message)}</div>`; }
  };
  document.querySelectorAll("[data-pick]").forEach((c) => {
    c.onchange = () => { c.checked ? chosen.add(c.dataset.pick) : chosen.delete(c.dataset.pick); c.closest(".source-row").classList.toggle("on", c.checked); paintTopics(); paintCost(); };
  });
  document.querySelectorAll("[data-analyze]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api("/media/podcast", { method: "POST", body: { items: [b.dataset.analyze], format: "auto" } });
        b.textContent = "הניתוח התחיל";
        toast("הניתוח התחיל (בלי לכתוב פודקאסט). השיעור יופיע כאן לבחירה כשיסתיים.");
      } catch (err) { b.disabled = false; toast(err.message, true); }
    };
  });
  document.querySelectorAll("[data-level]").forEach((b) => {
    b.onclick = () => { difficulty = b.dataset.level; document.querySelectorAll("[data-level]").forEach((x) => x.classList.toggle("active", x === b)); };
  });
  document.getElementById("x-count").oninput = () => { clearTimeout(viewExamSetup.timer); viewExamSetup.timer = setTimeout(paintCost, 400); };
  document.getElementById("x-start").onclick = async (e) => {
    if (!chosen.size) return toast("בחר לפחות שיעור אחד", true);
    e.target.disabled = true;
    try {
      const exam = await api("/exams", { method: "POST", body: {
        projects: [...chosen], title: document.getElementById("x-title").value.trim(),
        options: { count: Number(document.getElementById("x-count").value) || 10, mcq_share: Number(document.getElementById("x-mix").value),
          difficulty, language: document.getElementById("x-lang").value, num_options: Number(document.getElementById("x-options").value),
          style: document.getElementById("x-style").value, instructions: document.getElementById("x-notes").value.trim(),
          focus: Object.keys(topicState).filter((n) => topicState[n] === "focus"),
          exclude: Object.keys(topicState).filter((n) => topicState[n] === "exclude") } } });
      location.hash = `#/exam/${enc(exam.id)}`;
    } catch (err) { e.target.disabled = false; toast(err.message, true); }
  };
  paintTopics();
  paintCost();
}

async function viewExam(id) {
  const exam = await api(`/exams/${enc(id)}`);
  const head = `<div class="page-head"><div><a href="#/exam" class="small">→ למבחנים</a><h1><bdi>${esc(exam.title)}</bdi></h1>
    <div class="sub">${esc(fmtDate(exam.created))} · ${esc(EXAM_LEVELS[exam.options.difficulty] || "")} · ${exam.options.language === "en" ? "English" : "עברית"}</div></div>
    ${exam.running ? "" : `<button class="btn ghost danger" id="x-delete">מחק</button>`}</div>`;
  if (exam.running) {
    render(`${head}<div class="card stack">
      <div class="row" style="justify-content:space-between"><b>${esc(exam.stage || "בעבודה...")}</b>
        ${typeof exam.progress === "number" ? `<span class="muted">${Math.round(exam.progress * 100)}%</span>` : ""}</div>
      ${progressBar(exam)}
      <div class="muted small eta">${etaLine(exam.id, exam.stage, exam.progress, exam.stage_started)}</div>
      <details class="log" ${viewExam.logOpen ? "open" : ""}><summary>יומן עבודה <span class="muted small">(${exam.log.length} שורות)</span></summary>
        <pre>${esc(exam.log.join("\n"))}</pre></details></div>`);
    const details = document.querySelector("details.log");
    details.ontoggle = () => { viewExam.logOpen = details.open; if (details.open) details.querySelector("pre").scrollTop = 1e9; };
    if (details.open) details.querySelector("pre").scrollTop = 1e9;
    schedule(() => { if (location.hash === `#/exam/${enc(id)}`) viewExam(id); }, 2000);
    return;
  }
  if (exam.status === "error") {
    render(`${head}<div class="banner bad"><p>${esc(exam.error)}</p><button class="btn primary" id="x-retry">נסה שוב</button></div>
      <details class="log"><summary>יומן עבודה</summary><pre>${esc(exam.log.join("\n"))}</pre></details>`);
    document.getElementById("x-retry").onclick = async () => { await api(`/exams/${enc(id)}/run`, { method: "POST" }); viewExam(id); };
    bindExamDelete(id);
    return;
  }
  const result = exam.result;
  const s = result.stats;
  const url = (path, extra = "") => `/api/exams/${enc(id)}/${path}${path.includes("?") ? "&" : "?"}t=${enc(TOKEN)}${extra}`;
  const attempts = (await api(`/exams/${enc(id)}/attempts`)).attempts;
  render(`${head}<div class="stack">
    <div class="card stack"><div class="row" style="justify-content:space-between;flex-wrap:wrap">
      <div><h2>${s.final} שאלות</h2><div class="help" style="margin:0">קל ${s.by_difficulty.easy} · בינוני ${s.by_difficulty.medium} · קשה ${s.by_difficulty.hard}
        · אמריקאיות ${s.by_type.mcq} · פתוחות ${s.by_type.open}</div>
        <div class="muted small">בקרת איכות: ${s.passed_first_check} מתוך ${s.written} עברו את הבדיקה הראשונה, ${s.repaired} שופרו, ${s.dropped} נפסלו${s.repeats_left_out ? `, ${s.repeats_left_out} הושמטו כי חזרו על שאלות קודמות` : ""}.</div>
        ${calibrationHtml(exam.options.calibration)}</div>
      <div class="row"><button class="btn" id="x-to-review" title="כל השאלות ייכנסו לחזרה המרווחת, עד 20 חדשות ביום">הוסף לחזרה המרווחת</button>
        <button class="btn primary lg" id="x-practice">התחל תרגול</button></div></div></div>
    <div class="card stack"><h2>סיכום לימודי</h2><div class="help" style="margin:0">כל השיעורים יחד: מושגים, עובדות, טעויות נפוצות ושאלות לבדיקה עצמית.</div>
      <div class="files"><a class="btn" href="${url("summary")}" target="_blank" rel="noopener">פתח</a>
        <a class="btn" href="${url("summary", "&print=1")}" target="_blank" rel="noopener">PDF (הדפסה)</a>
        <a class="btn" href="${url("summary?format=docx", "&download=1")}">Word</a><a class="btn" href="${url("summary?format=md", "&download=1")}">Markdown</a></div></div>
    <div class="card stack"><h2>הדפסה וייצוא</h2>
      <div class="files"><a class="btn" href="${url("export?format=html", "&print=1")}" target="_blank" rel="noopener">דף מבחן (PDF)</a>
        <a class="btn" href="${url("export?format=html&answers=1", "&print=1")}" target="_blank" rel="noopener">דף תשובות (PDF)</a>
        ${EXPORTS.map(([f, label]) => `<a class="btn" href="${url(`export?format=${f}`, "&download=1")}">${esc(label)}</a>`).join("")}</div></div>
    ${attempts.length ? `<div class="card stack"><h2>ניסיונות קודמים</h2>${attempts.slice().reverse().map((a) =>
      `<div class="row" style="justify-content:space-between"><span>ניסיון ${a.number} · ${esc(fmtDate(a.submitted))}</span><b>${pct(a.score)}</b></div>`).join("")}</div>` : ""}
    <div id="x-practice-box"></div>
  </div>`);
  bindExamDelete(id);
  document.getElementById("x-practice").onclick = (e) => { e.currentTarget.blur(); practiceExam(id, exam, result); };  // Enter must not restart it
  document.getElementById("x-to-review").onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api(`/exams/${enc(id)}/review`, { method: "POST" });
      toast(r.added ? `${r.added} שאלות נוספו לחזרה המרווחת` : "כל השאלות כבר בחזרה המרווחת");
    } catch (err) { toast(err.message, true); }
    e.target.disabled = false;
  };
}

function calibrationHtml(c) {
  if (!c || !c.mix) return "";
  const share = (d) => Math.round((c.mix[d] || 0) * 100);
  const basis = c.answers ? `לפי ${c.answers} תשובות קודמות` : "לפי הערכה ראשונית (עוד אין תשובות קודמות)";
  return `<div class="muted small">רמת הקושי כוונה להצלחה צפויה של ${pct(c.expected)} (${basis}): קל ${share("easy")}% · בינוני ${share("medium")}% · קשה ${share("hard")}%.</div>`;
}

function reviewCardHtml(r) {
  if (!r || !r.total) return `<div class="card stack"><div class="row" style="justify-content:space-between"><b>חזרה מרווחת</b>
    <span class="muted small">אחרי תרגול, השאלות נכנסות לכאן וחוזרות בדיוק כשמתחילים לשכוח (FSRS)</span></div></div>`;
  return `<div class="card stack"><div class="row" style="justify-content:space-between;flex-wrap:wrap">
      <div><h2>חזרה מרווחת</h2><div class="help" style="margin:0">${r.due_now ? `${r.due_now} שאלות מחכות לחזרה עכשיו` :
        r.next_due ? `אין חזרות כרגע. הבאה ${esc(whenLabel(r.next_due))}` : "אין חזרות כרגע"}</div></div>
      <div class="row"><a class="btn ghost" href="#/settings/review">הגדרות</a>
        <a class="btn ${r.due_now ? "primary" : ""}" href="#/review">${r.due_now ? "התחל חזרה" : "לחזרה המרווחת"}</a></div></div>
    <div class="review-stats"><div><span class="muted small">בחפיסה</span><b>${r.total}</b></div>
      <div><span class="muted small">נלמדו</span><b>${r.learned}</b></div>
      <div><span class="muted small">זכירה ממוצעת</span><b>${pct(r.average_recall)}</b></div>
      <div><span class="muted small">היום</span><b>${r.reviewed_today}</b></div></div></div>`;
}

function bindExamDelete(id) {
  const button = document.getElementById("x-delete");
  if (button) button.onclick = async () => {
    if (!confirm("למחוק את המבחן ואת הניסיונות שלו?")) return;
    try { await api(`/exams/${enc(id)}`, { method: "DELETE" }); location.hash = "#/exam"; } catch (err) { toast(err.message, true); }
  };
}

/* One question at a time with instant feedback; open questions are self-scored against the rubric. */
function practiceExam(id, exam, result) {
  const questions = result.questions;
  const letters = EXAM_LETTERS[exam.options.language === "en" ? "en" : "he"];
  const answers = {};
  const selfScores = {};
  let index = 0;
  const sourceLinks = (q) => sourceLinksHtml(q, exam.projects, result.lecture_titles);

  const paint = () => {
    const q = questions[index];
    const done = answers[q.id] !== undefined;
    let body;
    if (q.type === "mcq") {
      body = mcqOptionsHtml(q, letters, answers[q.id]);
      if (done) body += mcqFeedbackHtml(q, letters, answers[q.id], sourceLinks(q));
    } else {
      body = `<textarea id="x-open" rows="5" placeholder="כתוב את התשובה שלך" ${done ? "disabled" : ""}>${esc(answers[q.id] || "")}</textarea>`;
      body += done ? `<div class="banner ok"><p><b>תשובה לדוגמה:</b> ${esc(q.model_answer || "")}</p></div>
          <div class="help">מחוון: ${(q.rubric || []).map((r) => `${esc(r.point)} (${esc(String(r.weight))})`).join(" · ")}</div>
          <label class="field"><span>כמה נקודות מגיעות לך מתוך 10?</span><input type="number" id="x-self" min="0" max="10" value="${selfScores[q.id] ?? ""}"></label>
          <div class="muted small">בשיעור: ${sourceLinks(q)}</div>`
        : `<button class="btn" id="x-reveal">הצג תשובה לדוגמה</button>`;
    }
    document.getElementById("x-practice-box").innerHTML = `<div class="card stack practice">
      <div class="row" style="justify-content:space-between"><b>שאלה ${index + 1} מתוך ${questions.length}</b>
        <span class="muted small">${esc(q.topic_name || "")} · ${esc(EXAM_LEVELS[q.difficulty] || "")}</span></div>
      <div class="progress"><div style="width:${Math.round(((index + 1) / questions.length) * 100)}%"></div></div>
      <div class="stem"><bdi>${esc(q.stem)}</bdi></div>${body}
      <div class="row" style="justify-content:space-between">
        <button class="btn ghost" id="x-prev" ${index === 0 ? "disabled" : ""}>→ הקודמת</button>
        ${index < questions.length - 1 ? `<button class="btn primary" id="x-next">הבאה ←</button>` : `<button class="btn primary" id="x-finish">סיים וקבל ציון</button>`}
      </div>
      ${keysHint(q.type === "mcq" ? [[`1-${q.options.length}`, "בחירת תשובה"], ["Enter", index < questions.length - 1 ? "הבאה" : "סיום"], ["→", "הקודמת"]]
        : [["Ctrl/⌘+Enter", done ? "הבאה" : "הצג תשובה"], ["→ / ←", "הקודמת / הבאה (מחוץ לתיבת הטקסט)"]])}</div>`;
    document.getElementById("x-practice-box").scrollIntoView({ block: "nearest" });
    document.querySelectorAll("[data-answer]").forEach((b) => { b.onclick = () => { answers[q.id] = b.dataset.answer; paint(); }; });
    const reveal = document.getElementById("x-reveal");
    if (reveal) reveal.onclick = () => { answers[q.id] = document.getElementById("x-open").value; paint(); };
    const self = document.getElementById("x-self");
    if (self) self.oninput = () => { selfScores[q.id] = self.value; };
    document.getElementById("x-prev").onclick = () => { index -= 1; paint(); };
    const next = document.getElementById("x-next");
    if (next) next.onclick = () => { index += 1; paint(); };
    const finish = document.getElementById("x-finish");
    if (finish) finish.onclick = () => { examKeys = null; submitAttempt(id, result, answers, selfScores); };
  };
  examKeys = {
    box: "x-practice-box",
    handle(e) {
      const q = questions[index];
      if (!q || !document.querySelector("#x-practice-box .practice")) return false;
      const done = answers[q.id] !== undefined;
      const digit = keyDigit(e);
      if (digit && q.type === "mcq" && !done && digit <= q.options.length) { answers[q.id] = q.options[digit - 1].key; paint(); return true; }
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && q.type === "open" && !done) { document.getElementById("x-reveal").click(); return true; }
      if (e.key === "Enter" || e.key === "ArrowLeft") {
        (document.getElementById("x-next") || document.getElementById("x-finish")).click();
        return true;
      }
      if (e.key === "ArrowRight" && index > 0) { index -= 1; paint(); return true; }
      return false;
    },
  };
  paint();
}

async function submitAttempt(id, result, answers, selfScores) {
  let attempt;
  try { attempt = await api(`/exams/${enc(id)}/attempts`, { method: "POST", body: { answers, self_scores: selfScores } }); }
  catch (err) { return toast(err.message, true); }
  const bars = (entries, labels = {}) => Object.entries(entries).map(([name, v]) => `<div class="score-row">
    <span><bdi>${esc(labels[name] || name)}</bdi></span><div class="progress"><div style="width:${Math.round((v.share || 0) * 100)}%"></div></div><b>${pct(v.share)}</b></div>`).join("");
  document.getElementById("x-practice-box").innerHTML = `<div class="card stack">
    <div class="row" style="justify-content:space-between"><h2>הציון שלך</h2><b class="big-score">${pct(attempt.score)}</b></div>
    <div class="help" style="margin:0">ענית על ${attempt.answered} מתוך ${attempt.total} שאלות.</div>
    <h3>לפי נושא</h3>${bars(attempt.topics)}
    <h3>לפי רמת קושי</h3>${bars(attempt.levels, EXAM_LEVELS)}
    ${attempt.weak_topics.length ? `<div class="banner warn"><p><b>נושאים לחיזוק:</b> ${attempt.weak_topics.map(esc).join(" · ")}</p>
        <button class="btn primary" id="x-again">תרגל שוב את הנושאים החלשים</button></div>`
      : `<div class="banner ok"><p><b>כל הכבוד!</b> אין נושאים מתחת ל-70%.</p></div>`}
    ${attempt.review ? `<div class="row" style="justify-content:space-between;flex-wrap:wrap"><span class="muted small">${attempt.review_added
      ? `${attempt.review_added} שאלות נכנסו לחזרה המרווחת.` : "התשובות עדכנו את החזרה המרווחת."} שאלות שטעית בהן יחזרו כבר היום, והשאר כשתתחיל לשכוח אותן.</span>
      <a class="btn" href="#/review">לחזרה המרווחת${attempt.review.due_now ? ` (${attempt.review.due_now})` : ""}</a></div>` : ""}
  </div>`;
  document.getElementById("x-practice-box").scrollIntoView({ block: "start" });
  const again = document.getElementById("x-again");
  if (again) again.onclick = async () => {
    again.disabled = true;
    try { const next = await api(`/exams/${enc(id)}/practice`, { method: "POST" }); location.hash = `#/exam/${enc(next.id)}`; }
    catch (err) { again.disabled = false; toast(err.message, true); }
  };
}

/* Spaced repetition (FSRS-5): the questions due now, one at a time. Multiple choice: answer, then rate how well
   you knew it (a wrong answer is always "again"). Open: think or write the answer, reveal, then rate. */
async function viewReview() {
  const data = await api("/review");
  const queue = data.cards;
  let stats = data.stats;
  let given;          // the chosen option key, or true for a revealed open question
  let busy = false;
  render(`<div class="page-head"><div><a href="#/exam" class="small">→ למוד מבחן</a><h1>חזרה מרווחת</h1>
      <div class="sub">כל שאלה חוזרת בדיוק כשהסיכוי לזכור אותה יורד לרמה שנבחרה (90% כברירת מחדל). שאלה שקל לך בה תחזור בעוד ימים או שבועות; שאלה שטעית בה תחזור כבר היום.</div></div>
      <a class="btn ghost" href="#/settings/review">הגדרות החזרה</a></div>
    <div id="x-review-box"></div>`);

  const finished = () => {
    examKeys = null;
    document.getElementById("x-review-box").innerHTML = `<div class="card stack">
      <h2>${stats.reviewed_today ? "סיימת את החזרות להיום" : "אין שאלות לחזרה כרגע"}</h2>
      <div class="help" style="margin:0">${stats.reviewed_today ? `היום: ${stats.reviewed_today} חזרות, ${stats.correct_today} מהן ידעת. ` : ""}${stats.next_due
        ? `החזרה הבאה ${esc(whenLabel(stats.next_due))}.` : stats.total ? "" : "פתור מבחן בתרגול, והשאלות ייכנסו לכאן אוטומטית."}</div>
      ${reviewCardHtml(stats).replace(/<a [^>]*href="#\/review"[^>]*>[^<]*<\/a>/, "")}
      <div class="row"><a class="btn" href="#/exam">חזרה למוד מבחן</a></div></div>`;
  };

  const paint = () => {
    const card = queue[0];
    if (!card) return finished();
    const q = card.question;
    const letters = EXAM_LETTERS[card.language === "en" ? "en" : "he"];
    const links = sourceLinksHtml(q, card.projects, card.lecture_titles);
    const answered = given !== undefined;
    const right = q.type === "mcq" && answered && q.options.some((o) => o.key === given && o.correct);
    let body = "";
    if (q.type === "mcq") {
      body = mcqOptionsHtml(q, letters, given);
      if (answered) body += mcqFeedbackHtml(q, letters, given, links);
    } else if (!answered) {
      body = `<textarea id="x-open" rows="4" placeholder="כתוב או חשוב על התשובה, ואז הצג את התשובה לדוגמה"></textarea>
        <button class="btn" id="x-reveal">הצג תשובה לדוגמה</button>`;
    } else {
      body = `<div class="banner ok"><p><b>תשובה לדוגמה:</b> ${esc(q.model_answer || "")}</p></div>
        <div class="help">מחוון: ${(q.rubric || []).map((r) => `${esc(r.point)} (${esc(String(r.weight))})`).join(" · ")}</div>
        <div class="muted small">בשיעור: ${links}</div>`;
    }
    const choices = !answered ? [] : q.type === "mcq" && !right ? [1] : [1, 2, 3, 4];
    const fallback = choices.length === 1 ? 1 : 3;
    const rate = choices.length ? `<div class="stack" style="gap:6px"><b>${q.type === "mcq" && !right ? "השאלה תחזור בקרוב" : "כמה טוב ידעת?"}</b>
      <div class="rate-row">${[1, 2, 3, 4].map((r) => choices.includes(r) ? `<button class="btn ${r === fallback ? "primary" : ""}" data-rate="${r}">
        ${RATINGS[r]} <small>${esc(whenLabel(card.preview[r]))}</small></button>` : `<span></span>`).join("")}</div></div>` : "";
    document.getElementById("x-review-box").innerHTML = `<div class="card stack practice">
      <div class="row" style="justify-content:space-between"><b>נשארו ${queue.length}</b>
        <span class="muted small"><bdi>${esc(card.exam_title)}</bdi> · ${esc(q.topic_name || "")} · ${esc(EXAM_LEVELS[q.difficulty] || "")}${card.state === "new" ? " · חדשה" : ""}</span></div>
      <div class="stem"><bdi>${esc(q.stem)}</bdi></div>${body}${rate}
      ${keysHint(!answered ? (q.type === "mcq" ? [[`1-${q.options.length}`, "בחירת תשובה"]] : [["Ctrl/⌘+Enter", "הצג תשובה"]])
        : [[choices.length === 1 ? "1" : "1-4", "דירוג"], ["Enter", RATINGS[fallback]]])}</div>`;
    document.querySelectorAll("#x-review-box [data-answer]").forEach((b) => { b.onclick = () => { given = b.dataset.answer; paint(); }; });
    const reveal = document.getElementById("x-reveal");
    if (reveal) reveal.onclick = () => { given = true; paint(); };
    document.querySelectorAll("[data-rate]").forEach((b) => { b.onclick = () => submit(Number(b.dataset.rate)); });
    examKeys.choices = choices;
    examKeys.fallback = fallback;
  };

  const submit = async (rating) => {
    if (busy || !queue.length) return;
    busy = true;
    const card = queue[0];
    try {
      const r = await api(`/review/${enc(card.id)}`, { method: "POST", body: { rating } });
      stats = r.stats;
      queue.shift();
      if (rating === 1) queue.push({ ...card, state: r.card.state, preview: r.preview || card.preview });  // again: once more before the end
      given = undefined;
      paint();
    } catch (err) { toast(err.message, true); }
    finally { busy = false; }
  };

  examKeys = {
    box: "x-review-box",
    choices: [],
    fallback: 3,
    handle(e) {
      const card = queue[0];
      if (!card) return false;
      const q = card.question;
      const digit = keyDigit(e);
      if (given === undefined) {
        if (digit && q.type === "mcq" && digit <= q.options.length) { given = q.options[digit - 1].key; paint(); return true; }
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && q.type === "open") { given = true; paint(); return true; }
        return false;
      }
      if (digit && this.choices.includes(digit)) { submit(digit); return true; }
      if (e.key === "Enter") { submit(this.fallback); return true; }
      return false;
    },
  };
  paint();
}

/* Key concepts: choose lessons and see every key concept with its explanation (as in the lesson summary),
   the lesson moments where it comes up, and the lesson's examples. Exports: Markdown, Word, PDF. */
const CONCEPT_LEVELS = { 3: "ליבה", 2: "חשוב", 1: "רקע" };

async function viewConcepts() {
  const data = await api("/exams");
  const ready = data.projects.filter((p) => p.ready);
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem("mbs-concept-lessons") || "[]"); } catch (_) { saved = []; }
  const chosen = new Set(saved.filter((id) => ready.some((p) => p.id === id)));
  if (!chosen.size) ready.forEach((p) => chosen.add(p.id));
  let glossary = null;
  let level = 0;       // 0 = all
  let query = "";
  let sort = "importance";

  render(`<div class="page-head"><div><h1>מושגי מפתח</h1>
      <div class="sub">בוחרים שיעורים ומקבלים את כל המושגים שלהם עם ההסבר מהשיעור, הרגעים בשיעור שבהם הם עולים והדוגמאות שניתנו.</div></div></div>
    <div class="stack">
      <div class="card stack"><div><h2>שיעורים</h2><div class="help">מושג שמופיע בכמה שיעורים מוצג פעם אחת, עם ההגדרה של כל שיעור כשהן שונות.</div></div>
        <div class="stack" style="gap:6px" id="c-lessons">${ready.map((p) => `<label class="source-row ${chosen.has(p.id) ? "on" : ""}">
          <input type="checkbox" data-lesson="${esc(p.id)}" ${chosen.has(p.id) ? "checked" : ""}> <bdi class="ellipsis">${esc(p.title)}</bdi>
          <span class="muted small">${p.concepts.length} מושגים</span></label>`).join("")
          || `<div class="muted">אין עדיין שיעורים מנותחים. פתח פרויקט והרץ את שלב התוכנית, או במוד מבחן לחץ "נתח לשיעור" על תמלול.</div>`}</div></div>
      <div class="card stack" id="c-tools"></div>
      <div class="stack" id="c-list"></div>
    </div>`);

  const exportUrl = (format, extra = "") =>
    `/api/concepts/export?projects=${[...chosen].map(enc).join(",")}&format=${format}&t=${enc(TOKEN)}${extra}`;

  const paintTools = () => {
    const box = document.getElementById("c-tools");
    if (!glossary) { box.innerHTML = `<div class="muted">בחר לפחות שיעור אחד.</div>`; return; }
    const counts = glossary.counts;
    box.innerHTML = `<div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px">
        <input type="search" id="c-search" placeholder="חיפוש מושג או מילה בהגדרה" value="${esc(query)}" style="flex:1;min-width:200px">
        <div class="segmented" id="c-level">${[[0, `הכל (${glossary.concepts.length})`], [3, `ליבה (${counts[3]})`], [2, `חשוב (${counts[2]})`], [1, `רקע (${counts[1]})`]]
          .map(([k, v]) => `<button type="button" data-level="${k}" class="${Number(k) === level ? "active" : ""}">${v}</button>`).join("")}</div>
        <select id="c-sort" aria-label="מיון"><option value="importance" ${sort === "importance" ? "selected" : ""}>לפי חשיבות</option>
          <option value="abc" ${sort === "abc" ? "selected" : ""}>לפי א-ב</option></select></div>
      <div class="files"><a class="btn" href="${exportUrl("md")}">Markdown</a><a class="btn" href="${exportUrl("docx")}">Word</a>
        <a class="btn" href="${exportUrl("html", "&download=0&print=1")}" target="_blank" rel="noopener">PDF (הדפסה)</a>
        <a class="btn ghost" href="${exportUrl("html", "&download=0")}" target="_blank" rel="noopener">פתח כדף</a></div>`;
    const search = document.getElementById("c-search");
    search.oninput = () => { query = search.value; paintList(); };
    document.querySelectorAll("#c-level [data-level]").forEach((b) => {
      b.onclick = () => { level = Number(b.dataset.level); document.querySelectorAll("#c-level [data-level]").forEach((x) => x.classList.toggle("active", x === b)); paintList(); };
    });
    document.getElementById("c-sort").onchange = (e) => { sort = e.target.value; paintList(); };
  };

  const paintList = () => {
    const box = document.getElementById("c-list");
    if (!glossary) { box.innerHTML = ""; return; }
    const q = query.trim().toLowerCase();
    let items = glossary.concepts.filter((c) => (!level || c.importance === level)
      && (!q || c.name.toLowerCase().includes(q) || c.definitions.some((d) => d.text.toLowerCase().includes(q))));
    if (sort === "abc") items = items.slice().sort((a, b) => a.name.localeCompare(b.name, "he"));
    const several = glossary.lessons.length > 1;
    box.innerHTML = items.length ? items.map((c) => `<div class="card stack concept-card imp${c.importance}">
        <div class="row" style="justify-content:space-between;gap:8px"><h2 style="margin:0"><bdi>${esc(c.name)}</bdi></h2>
          <span class="badge ${c.importance === 3 ? "ok" : ""}">${CONCEPT_LEVELS[c.importance]}</span></div>
        ${c.definitions.map((d) => `<p style="margin:0">${esc(d.text)}${c.definitions.length > 1 ? ` <span class="muted small">(${esc(d.lesson)})</span>` : ""}</p>`).join("")
          || `<p class="muted" style="margin:0">אין הגדרה בניתוח השיעור.</p>`}
        ${c.depends_on.length ? `<div class="muted small">נשען על: ${c.depends_on.map(esc).join(" · ")}</div>` : ""}
        <div class="muted small">${c.lessons.map((l) => `${several ? `<bdi>${esc(l.title)}</bdi>: ` : "בשיעור: "}${l.moments.length
          ? l.moments.map((m) => `<a href="#/p/${enc(l.project)}/listen">${esc(m.time)}</a>`).join(" · ") : "—"}`).join(" | ")}</div>
        ${c.examples.length ? `<details><summary class="small">דוגמה מהשיעור (${c.examples.length})</summary>
          <ul class="clean">${c.examples.map((x) => `<li class="small">${esc(x.text)}</li>`).join("")}</ul></details>` : ""}
      </div>`).join("") : `<div class="card muted">אין מושגים שמתאימים לחיפוש.</div>`;
  };

  const load = async () => {
    try { localStorage.setItem("mbs-concept-lessons", JSON.stringify([...chosen])); } catch (_) { /* storage blocked */ }
    if (!chosen.size) { glossary = null; paintTools(); paintList(); return; }
    try { glossary = await api(`/concepts?projects=${[...chosen].map(enc).join(",")}`); }
    catch (err) { glossary = null; toast(err.message, true); }
    paintTools();
    paintList();
  };
  document.querySelectorAll("[data-lesson]").forEach((c) => {
    c.onchange = () => { c.checked ? chosen.add(c.dataset.lesson) : chosen.delete(c.dataset.lesson); c.closest(".source-row").classList.toggle("on", c.checked); load(); };
  });
  load();
}

/* Settings card: how spaced repetition schedules the questions (FSRS-5). */
async function paintReviewSettings() {
  const box = document.getElementById("review-settings");
  if (!box) return;
  let view;
  try { view = await api("/review/settings"); } catch (_) { box.innerHTML = ""; return; }
  const s = view.settings;
  const lim = view.limits;
  box.innerHTML = `<div class="card stack" id="review-settings-card">
    <div><h2>חזרה מרווחת (מוד מבחן)</h2><div class="help">שאלות שתרגלת חוזרות לפי אלגוריתם FSRS-5: כל שאלה חוזרת כשהסיכוי שתזכור אותה יורד לרמה שבחרת.
      השינוי חל מהחזרה הבאה של כל שאלה.</div></div>
    <div class="form-group">
      <label class="field"><span>רמת הזכירה שהחזרה שומרת עליה</span><select id="rs-retention">${[0.8, 0.85, 0.9, 0.95].map((r) =>
        `<option value="${r}" ${Math.abs(r - s.retention) < 0.001 ? "selected" : ""}>${Math.round(r * 100)}%${r === 0.9 ? " (מומלץ)" : r === 0.95 ? " (יותר חזרות)" : r === 0.8 ? " (פחות חזרות)" : ""}</option>`).join("")}</select></label>
      <label class="field"><span>שאלות חדשות ביום (מ"הוסף לחזרה")</span><input type="number" id="rs-new" min="${lim.new_per_day[0]}" max="${lim.new_per_day[1]}" value="${s.new_per_day}" class="ltr"></label>
      <label class="field"><span>המרווח הארוך ביותר (ימים)</span><input type="number" id="rs-max" min="${lim.max_days[0]}" max="${lim.max_days[1]}" value="${s.max_days}" class="ltr"></label>
      <label class="field"><span>אחרי טעות, השאלה חוזרת בעוד (דקות)</span><input type="number" id="rs-relearn" min="${lim.relearn_minutes[0]}" max="${lim.relearn_minutes[1]}" value="${s.relearn_minutes}" class="ltr"></label>
    </div>
    <div class="row"><button class="btn" id="rs-save">שמור הגדרות חזרה</button><a class="btn ghost" href="#/review">לחזרה המרווחת</a>
      <span class="help" style="margin:0">לפני מבחן קרוב כדאי 95%; לתרגול שוטף 90%.</span></div></div>`;
  document.getElementById("rs-save").onclick = async () => {
    try {
      const r = await api("/review/settings", { method: "PUT", body: {
        retention: Number(document.getElementById("rs-retention").value), new_per_day: Number(document.getElementById("rs-new").value),
        max_days: Number(document.getElementById("rs-max").value), relearn_minutes: Number(document.getElementById("rs-relearn").value) } });
      toast(`הגדרות החזרה נשמרו (זכירה ${Math.round(r.settings.retention * 100)}%, ${r.settings.new_per_day} חדשות ביום)`);
      paintReviewSettings();
    } catch (err) { toast(err.message, true); }
  };
  if (location.hash.includes("review")) document.getElementById("review-settings-card").scrollIntoView({ block: "start" });
}
