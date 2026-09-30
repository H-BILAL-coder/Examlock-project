/* ExamLock exam-room integrity agent (vanilla JS).
 * The browser DETECTS; the server DECIDES. Every signal below is advisory
 * evidence POSTed to the violation beacon — enforcement (deadlines, write
 * gating, adjudication) happens server-side.
 */
(function () {
  "use strict";
  const cfg = window.EXAMLOCK;
  const attemptId = cfg.attemptId;
  const API = cfg.apiBase;

  const token = sessionStorage.getItem("examlock_attempt_" + attemptId);
  if (!token) {
    document.body.innerHTML = "<div style='padding:3rem;text-align:center'><h2>Attempt token missing</h2><p>Please start the exam from your dashboard.</p><a href='/'>Back to dashboard</a></div>";
    return;
  }

  let seq = 0;
  let questions = [];
  let answers = {}; // qid -> {choice_ids:[], text:""}
  let remaining = null;
  let deadline = null;
  let submitted = false;
  let eventQueue = [];
  let warnings = 0;
  let saveTimer = null;
  let hiddenAt = null;
  let lastDevtoolsSignal = 0;
  let deviceFp = localStorage.getItem("examlock_device");
  if (!deviceFp) {
    deviceFp = "dev-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem("examlock_device", deviceFp);
  }

  function getCookie(name) {
    const v = document.cookie.split("; ").find((r) => r.startsWith(name + "="));
    return v ? decodeURIComponent(v.split("=")[1]) : "";
  }
  const CSRF = getCookie("csrftoken");

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign(
      { "X-Attempt-Token": token, "X-CSRFToken": CSRF, "Content-Type": "application/json" },
      opts.headers || {}
    );
    return fetch(API + path, opts);
  }

  /* ---------- violation beacon ---------- */
  function logEvent(event_type, payload) {
    eventQueue.push({ event_type, client_ts: new Date().toISOString(), payload: payload || {} });
    warnings += 1;
    const wc = document.getElementById("warn-count");
    if (wc) wc.textContent = warnings;
    const bar = document.getElementById("warnbar");
    if (bar) {
      bar.style.display = "block";
      bar.textContent = "Integrity notice (" + warnings + "): please stay in fullscreen and on this tab. Events are logged for instructor review.";
    }
  }
  setInterval(function () {
    if (!eventQueue.length || submitted) return;
    const batch = eventQueue.splice(0, eventQueue.length);
    api("/attempts/" + attemptId + "/violations/", {
      method: "POST",
      body: JSON.stringify({ events: batch }),
    }).catch(function () { /* keep exam usable offline-ish; events retried next tick */ eventQueue = batch.concat(eventQueue); });
  }, 5000);

  /* ---------- fullscreen discipline ---------- */
  const overlay = document.getElementById("fs-overlay");
  document.getElementById("enter-btn").addEventListener("click", function () {
    document.documentElement.requestFullscreen().catch(function () {});
  });
  document.addEventListener("fullscreenchange", function () {
    if (!document.fullscreenElement && !submitted) {
      logEvent("fullscreen_exit", {});
      overlay.style.display = "flex";
    } else {
      overlay.style.display = "none";
    }
  });

  /* ---------- visibility / focus ---------- */
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      hiddenAt = Date.now();
    } else if (hiddenAt) {
      const dur = Math.round((Date.now() - hiddenAt) / 1000);
      logEvent("tab_hidden", { duration_seconds: dur });
      hiddenAt = null;
    }
  });
  window.addEventListener("blur", function () { logEvent("focus_lost", {}); });

  /* ---------- copy / paste / context menu / shortcuts ---------- */
  ["copy", "cut", "paste"].forEach(function (ev) {
    document.addEventListener(ev, function (e) {
      e.preventDefault();
      logEvent(ev + "_attempt", {});
    });
  });
  document.addEventListener("contextmenu", function (e) {
    e.preventDefault();
    logEvent("copy_attempt", { via: "contextmenu" });
  });
  document.addEventListener("keydown", function (e) {
    const k = e.key.toLowerCase();
    const mod = e.ctrlKey || e.metaKey;
    if (
      e.key === "F12" ||
      (mod && e.shiftKey && ["i", "j", "c"].includes(k)) ||
      (mod && ["u", "s", "p"].includes(k))
    ) {
      e.preventDefault();
      logEvent("copy_attempt", { via: "shortcut", key: e.key });
    }
  });
  document.addEventListener("beforeprint", function (e) {
    e.preventDefault();
    logEvent("copy_attempt", { via: "print" });
  });

  /* ---------- devtools heuristic (evidence only, not proof) ---------- */
  setInterval(function () {
    const wide = window.outerWidth - window.innerWidth > 160;
    const tall = window.outerHeight - window.innerHeight > 160;
    const now = Date.now();
    if ((wide || tall) && now - lastDevtoolsSignal > 60000) {
      lastDevtoolsSignal = now;
      logEvent("devtools_signal", { wide, tall });
    }
  }, 5000);

  /* ---------- heartbeat (session lock + aliveness) ---------- */
  function heartbeat() {
    if (submitted) return;
    api("/attempts/" + attemptId + "/heartbeat/", {
      method: "POST",
      body: JSON.stringify({ device_fingerprint: deviceFp }),
    }).catch(function () {});
  }
  setInterval(heartbeat, 30000);

  /* ---------- server-authoritative countdown ---------- */
  function tick() {
    if (remaining == null || submitted) return;
    remaining -= 1;
    if (remaining <= 0) {
      remaining = 0;
      renderTimer();
      autoSubmit();
      return;
    }
    renderTimer();
  }
  function renderTimer() {
    const el = document.getElementById("timer");
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    el.textContent = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    el.classList.toggle("low", remaining < 300);
  }
  setInterval(tick, 1000);
  setInterval(function () {
    if (submitted) return;
    api("/attempts/" + attemptId + "/status/")
      .then((r) => r.json())
      .then(function (d) {
        remaining = d.remaining_seconds;
        seq = Math.max(seq, d.seq || 0);
        if (d.status !== "in_progress") { submitted = true; window.location = cfg.resultUrl; }
        renderTimer();
      })
      .catch(function () {});
  }, 15000);

  /* ---------- questions ---------- */
  function choiceInput(q, c) {
    if (q.qtype === "mcq_single" || q.qtype === "true_false") {
      return `<label class="choice"><input type="radio" name="q_${q.id}" value="${c.id}"> ${escapeHtml(c.text)}</label>`;
    }
    if (q.qtype === "mcq_multi") {
      return `<label class="choice"><input type="checkbox" name="q_${q.id}" value="${c.id}"> ${escapeHtml(c.text)}</label>`;
    }
    return "";
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
  }
  function renderQuestions() {
    const wrap = document.getElementById("questions");
    const dots = document.getElementById("dots");
    wrap.innerHTML = "";
    dots.innerHTML = "";
    document.getElementById("q-count").textContent = questions.length + " questions";
    questions.forEach(function (q, i) {
      const dot = document.createElement("button");
      dot.className = "dot";
      dot.textContent = i + 1;
      dot.onclick = function () { document.getElementById("qcard-" + q.id).scrollIntoView({ behavior: "smooth" }); };
      dot.id = "dot-" + q.id;
      dots.appendChild(dot);

      const card = document.createElement("div");
      card.className = "qcard";
      card.id = "qcard-" + q.id;
      let body = `<p><strong>Q${i + 1}.</strong> ${escapeHtml(q.stem)} <span class="muted">(${q.marks} marks)</span></p>`;
      if (q.qtype === "short_answer") {
        body += `<textarea id="ta-${q.id}" rows="4" placeholder="Type your answer..."></textarea>`;
      } else {
        body += q.choices.map((c) => choiceInput(q, c)).join("");
      }
      card.innerHTML = body;
      wrap.appendChild(card);

      card.addEventListener("change", function () { collectAnswer(q); });
      const ta = document.getElementById("ta-" + q.id);
      if (ta) ta.addEventListener("input", function () { collectAnswer(q); });
    });
    // restore saved answers
    Object.keys(answers).forEach(prefill);
  }
  function prefill(qid) {
    const a = answers[qid];
    if (!a) return;
    (a.choice_ids || []).forEach(function (cid) {
      const el = document.querySelector(`input[name="q_${qid}"][value="${cid}"]`);
      if (el) el.checked = true;
    });
    if (a.text) {
      const ta = document.getElementById("ta-" + qid);
      if (ta) ta.value = a.text;
    }
    markAnswered(qid);
  }
  function markAnswered(qid) {
    const dot = document.getElementById("dot-" + qid);
    if (dot) dot.classList.add("answered");
  }
  function collectAnswer(q) {
    const choiceIds = Array.from(document.querySelectorAll(`input[name="q_${q.id}"]:checked`)).map((e) => e.value);
    const ta = document.getElementById("ta-" + q.id);
    answers[q.id] = { choice_ids: choiceIds, text: ta ? ta.value : "" };
    markAnswered(q.id);
    scheduleSave();
  }

  /* ---------- autosave with idempotency + monotonic seq ---------- */
  function scheduleSave() {
    setSaveState("Unsaved changes…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 1200);
  }
  function setSaveState(t) {
    document.getElementById("save-state").textContent = t;
  }
  function saveNow() {
    if (submitted) return;
    seq += 1;
    const items = Object.keys(answers).map(function (qid) {
      return { question_id: qid, choice_ids: answers[qid].choice_ids, text: answers[qid].text };
    });
    const key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
    api("/attempts/" + attemptId + "/answers/", {
      method: "POST",
      body: JSON.stringify({ seq: seq, idempotency_key: key, items: items }),
    })
      .then(function (r) { return r.json().then((d) => ({ ok: r.ok, d })); })
      .then(function ({ ok, d }) {
        if (ok) setSaveState("All answers saved ✓ " + new Date().toLocaleTimeString());
        else {
          setSaveState("Save rejected: " + (d.detail || "unknown"));
          if (d.detail && /deadline|expired/i.test(d.detail)) autoSubmit();
        }
      })
      .catch(function () { setSaveState("Save failed — will retry"); });
  }

  /* ---------- submit (two-step) ---------- */
  const modal = document.getElementById("confirm-modal");
  document.getElementById("submit-btn").addEventListener("click", function () {
    const answered = Object.keys(answers).length;
    document.getElementById("confirm-detail").textContent =
      "You answered " + answered + " of " + questions.length + " questions. This cannot be undone.";
    modal.style.display = "flex";
  });
  document.getElementById("confirm-no").addEventListener("click", function () { modal.style.display = "none"; });
  document.getElementById("confirm-yes").addEventListener("click", function () {
    modal.style.display = "none";
    saveNow();
    setTimeout(function () {
      api("/attempts/" + attemptId + "/submit/", {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      })
        .then(function (r) { return r.json(); })
        .then(function () { submitted = true; window.location = cfg.resultUrl; })
        .catch(function () { alert("Submit failed — please try again."); });
    }, 800);
  });
  function autoSubmit() {
    if (submitted) return;
    submitted = true;
    api("/attempts/" + attemptId + "/submit/", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }).finally(function () { window.location = cfg.resultUrl; });
  }

  /* ---------- boot ---------- */
  api("/attempts/" + attemptId + "/")
    .then((r) => { if (!r.ok) throw new Error("load failed"); return r.json(); })
    .then(function (d) {
      questions = d.questions;
      const ansObj = d.answers || {};
      Object.keys(ansObj).forEach(function (qid) {
        const a = ansObj[qid];
        answers[qid] = { choice_ids: a.selected_choice_ids || [], text: a.text_answer || "" };
      });
      renderQuestions();
      return api("/attempts/" + attemptId + "/status/").then((r) => r.json());
    })
    .then(function (s) {
      remaining = s.remaining_seconds;
      deadline = s.deadline;
      seq = s.seq || 0;
      renderTimer();
      heartbeat();
    })
    .catch(function () {
      document.getElementById("questions").innerHTML =
        "<div class='card'><p>Could not load the exam. It may have expired or already been submitted.</p><a href='/'>Back to dashboard</a></div>";
      overlay.style.display = "none";
    });
})();
