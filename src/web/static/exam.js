/* Exam mode screens: setup, progress, practice, results, exports. Uses the helpers in app.js
   (api, render, esc, enc, toast, schedule, fmtDate, etaLine, chainSteps...). Everything shown that
   came from a model or a file is escaped with esc(). */

const EXAM_LEVELS = { easy: "קל", medium: "בינוני", hard: "קשה", mixed: "מעורב" };
const EXAM_TYPES = { "1": "אמריקאיות בלבד", "0.7": "בעיקר אמריקאיות (70/30)", "0.5": "חצי-חצי", "0": "פתוחות בלבד" };
const EXAM_STYLES = { final: "מבחן סיום אוניברסיטאי", quick: "בדיקה עצמית קצרה", oral: "הכנה למבחן בעל-פה" };
const EXAM_STATUS = { queued: ["בתור", "run"], running: ["בעבודה", "run"], done: ["מוכן", "ok"], error: ["נכשל", "bad"] };
const EXAM_LETTERS = { he: "אבגדה", en: "ABCDE" };
const EXPORTS = [["md", "Markdown"], ["docx", "Word"], ["anki", "Anki (כרטיסיות)"], ["moodle", "Moodle XML"],
  ["gift", "GIFT"], ["qti", "QTI (Canvas)"], ["json", "JSON"]];
const pct = (share) => (share === null || share === undefined ? "–" : `${Math.round(share * 100)}%`);

async function viewExams() {
  const data = await api("/exams");
  render(`
    <div class="page-head"><div><h1>מוד מבחן</h1>
      <div class="sub">בוחרים שיעורים ומקבלים סיכום לימודי ושאלות תרגול, אמריקאיות או פתוחות, ברמת הקושי ובשפה שבחרת. כל שאלה מבוססת על מה שנאמר בשיעור.</div></div>
      <a class="btn primary" href="#/exam/new">+ מבחן חדש</a></div>
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
          <span class="help">קל: זכירה והבנה. בינוני: יישום על דוגמה חדשה. קשה: ניתוח שמשלב מושגים. מעורב: 30% קל, 50% בינוני, 20% קשה.</span></div>
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
        <div class="muted small">בקרת איכות: ${s.passed_first_check} מתוך ${s.written} עברו את הבדיקה הראשונה, ${s.repaired} שופרו, ${s.dropped} נפסלו.</div></div>
      <button class="btn primary lg" id="x-practice">התחל תרגול</button></div></div>
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
  document.getElementById("x-practice").onclick = () => practiceExam(id, exam, result);
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
  const lectureProject = (label) => exam.projects[Number(String(label || "L1").slice(1)) - 1];
  const sourceLinks = (q) => (q.sources || []).map((src) => {
    const project = lectureProject(src.lecture);
    const title = (result.lecture_titles || {})[src.lecture] || src.lecture;
    return `<a href="#/p/${enc(project)}/listen" class="small">${esc(title)} · ${esc(src.time || "")}</a>`;
  }).join(" · ");

  const paint = () => {
    const q = questions[index];
    const done = answers[q.id] !== undefined;
    let body;
    if (q.type === "mcq") {
      body = `<div class="stack" style="gap:8px">${q.options.map((o) => {
        const state = !done ? "" : o.correct ? "right" : answers[q.id] === o.key ? "wrong" : "";
        return `<button class="option ${state}" data-answer="${o.key}" ${done ? "disabled" : ""}>
          <span class="letter">${letters["ABCDE".indexOf(o.key)]}</span><span>${esc(o.text)}</span></button>`;
      }).join("")}</div>`;
      if (done) {
        const chosen = q.options.find((o) => o.key === answers[q.id]);
        body += `<div class="banner ${chosen && chosen.correct ? "ok" : "warn"}"><p><b>${chosen && chosen.correct ? "נכון!" : `התשובה הנכונה: ${letters["ABCDE".indexOf(q.answer)]}`}</b>
          ${esc(q.explanation || "")}${chosen && !chosen.correct && chosen.feedback ? `<br><span class="muted">למה ${letters["ABCDE".indexOf(chosen.key)]} שגויה: ${esc(chosen.feedback)}</span>` : ""}</p></div>
          <div class="muted small">בשיעור: ${sourceLinks(q)}</div>`;
      }
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
      </div></div>`;
    document.getElementById("x-practice-box").scrollIntoView({ block: "start" });
    document.querySelectorAll("[data-answer]").forEach((b) => { b.onclick = () => { answers[q.id] = b.dataset.answer; paint(); }; });
    const reveal = document.getElementById("x-reveal");
    if (reveal) reveal.onclick = () => { answers[q.id] = document.getElementById("x-open").value; paint(); };
    const self = document.getElementById("x-self");
    if (self) self.oninput = () => { selfScores[q.id] = self.value; };
    document.getElementById("x-prev").onclick = () => { index -= 1; paint(); };
    const next = document.getElementById("x-next");
    if (next) next.onclick = () => { index += 1; paint(); };
    const finish = document.getElementById("x-finish");
    if (finish) finish.onclick = () => submitAttempt(id, result, answers, selfScores);
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
  </div>`;
  document.getElementById("x-practice-box").scrollIntoView({ block: "start" });
  const again = document.getElementById("x-again");
  if (again) again.onclick = async () => {
    again.disabled = true;
    try { const next = await api(`/exams/${enc(id)}/practice`, { method: "POST" }); location.hash = `#/exam/${enc(next.id)}`; }
    catch (err) { again.disabled = false; toast(err.message, true); }
  };
}
