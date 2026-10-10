(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  
  const form = $("#qa-form"), errBox = $("#error");

  const store = { get: k => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

  CQ.nav("/");
  CQ.call("/api/services").then(d => {
    const sel = $("#service");
    d.services.forEach(s => sel.append(new Option(s, s)));
    const last = store.get("crespoqa.service"); if (last) sel.value = last;
    $("#ai-wrap").hidden = !d.ai_review;
  });
  $("#consultant").value = store.get("crespoqa.consultant");

  function showError(msg) { errBox.textContent = msg; errBox.hidden = !msg; if (msg) errBox.scrollIntoView({ block: "nearest" }); }

  document.querySelectorAll(".drop").forEach(d => {
    const input = $("input", d), label = $(".fname", d);
    const set = f => {
      if (!f) return;
      const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files;
      label.textContent = f.name; d.classList.add("has");
    };
    d.addEventListener("click", () => input.click());
    d.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    input.addEventListener("click", e => e.stopPropagation());
    input.addEventListener("change", () => set(input.files[0]));
    ["dragenter", "dragover"].forEach(ev => d.addEventListener(ev, e => { e.preventDefault(); d.classList.add("over"); }));
    ["dragleave", "drop"].forEach(ev => d.addEventListener(ev, e => { e.preventDefault(); d.classList.remove("over"); }));
    d.addEventListener("drop", e => set(e.dataTransfer.files[0]));
  });

  form.addEventListener("submit", async e => {
    e.preventDefault(); showError("");
    const fd = new FormData(form);
    if (!fd.get("technical").name || !fd.get("executive").name) return showError("Please add both the Technical and Executive reports.");
    if (!String(fd.get("consultant")).trim()) return showError("Consultant full name is required.");
    fd.set("dry_run", $("#dry_run").checked ? "true" : "false");
    fd.set("ai_review", !$("#ai-wrap").hidden && $("#ai_review").checked ? "true" : "false");
    store.set("crespoqa.consultant", String(fd.get("consultant")).trim());
    store.set("crespoqa.service", String(fd.get("service")));
    const btn = $("#submit"); btn.disabled = true; btn.textContent = "Uploading…";
    try {
      const r = await CQ.api("/api/jobs", { method: "POST", body: fd });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || "Upload failed.");
      track(body.job_id, $("#dry_run").checked);
    } catch (err) { showError(err.message); btn.disabled = false; btn.textContent = "Run QA"; }
  });

  $("#again").addEventListener("click", () => location.reload());

  function track(jobId, dry) {
    $("#form-view").hidden = true; $("#result-view").hidden = false;
    $("#h-result").textContent = dry ? "Dry run in progress" : "Processing";
    const docsBox = $("#docs"), rows = {};
    CQ.call(`/api/jobs/${jobId}`).then(snap => {
      Object.entries(snap.plan).forEach(([name, steps]) => {
        const card = el("div", "box"), h = el("h2", "dochead"); h.append(el("span", null, name));
        const ol = el("ol", "steps"); rows[name] = { steps: {} };
        steps.forEach(s => { const li = el("li", "pending"); li.append(el("span", null, s.label));
          ol.append(li); rows[name].steps[s.key] = { li }; });
        card.append(h, ol); docsBox.append(card);
      });
      const es = new EventSource(`/api/jobs/${jobId}/events`);
      es.onmessage = m => {
        const ev = JSON.parse(m.data);
        if (ev.type === "step") {
          const s = rows[ev.doc]?.steps[ev.key]; if (!s) return;
          s.li.className = ev.state === "running" ? "running" : "done";
        } else if (ev.type === "end") { es.close(); finish(jobId, dry); }
      };
      es.onerror = () => { es.close(); finish(jobId, dry); };
    });
  }

  let finished = false;
  async function finish(jobId, dry) {
    if (finished) return; finished = true;
    const r = await CQ.api(`/api/jobs/${jobId}`);
    if (!r.ok) return showError("This job has expired.");
    const snap = await r.json(), docsBox = $("#docs");
    $("#h-result").textContent = dry ? "Dry run complete" : "Done";
    docsBox.replaceChildren();
    snap.reports.forEach(rep => {
      const card = el("div", "box"), h = el("h2", "dochead");
      h.append(el("span", null, `${rep.report_type === "technical" ? "Technical" : "Executive"}: ${rep.input_name}`));
      card.append(h);
      if (rep.error) card.append(el("div", "alert", rep.error));
      const ol = el("ol", "steps");
      rep.tasks.filter(t => t.key !== "prechecks" || t.warnings.length).forEach(t => {
        const li = el("li"), line = el("div", "stepline");
        line.append(el("span", null, t.title));
        li.append(line);
        const items = [...t.changes.map(c => ["", c]), ...t.warnings.map(w => ["w", "Warning: " + w])];
        if (items.length) {
          const warn = t.warnings.length > 0;
          const d = el("details"); d.open = warn;
          d.append(el("summary", null, warn ? `${t.warnings.length} warning(s), ${t.changes.length} change(s)` : `${t.changes.length} change(s)`));
          const ul = el("ul", "det"); items.forEach(([c, txt]) => ul.append(el("li", c, txt))); d.append(ul);
          li.append(d);
        }
        ol.append(li);
      });
      card.append(ol); docsBox.append(card);
    });
    const sv = $("#saved"); sv.replaceChildren(); sv.hidden = !(snap.saved && snap.saved.length);
    (snap.saved || []).forEach(x => sv.append(el("div", x.ok ? "" : "alert",
      x.ok ? `Saved over the old file: ${x.folder}/${x.name}` : `${x.name}: ${x.error}`)));
    const dl = $("#downloads"); dl.replaceChildren();
    if (!dry && snap.outputs.length) {
      const urls = snap.outputs.map(n => [n, `/api/jobs/${jobId}/files/${encodeURIComponent(n)}`]);
      const both = el("button", "primary", urls.length > 1 ? "Download both files" : "Download file"); both.type = "button";
      both.addEventListener("click", () => urls.forEach(([n, u], i) => setTimeout(() => {
        const a = document.createElement("a"); a.href = u; a.download = n; document.body.append(a); a.click(); a.remove(); }, i * 700)));
      dl.append(both);
      urls.forEach(([n, u]) => { const a = el("a", "btn", n); a.href = u; a.title = "Download this file"; dl.append(a); });
      dl.hidden = false;
    }
    $("#again").hidden = false;
    if (snap.review_status && snap.review_status !== "off") pollReview(jobId);
  }

  const SEV = { warning: "warning", info: "ok" };
  function pollReview(jobId) {
    const box = $("#review"); box.hidden = false;
    const tick = async () => {
      let rv; try { rv = await CQ.call(`/api/jobs/${jobId}/review`); } catch { return; }
      $("#review-progress").textContent = rv.status === "error" ? rv.error : rv.status === "done" ? "" : (rv.progress || "Waiting for the model…");
      const notes = $("#review-notes"); notes.replaceChildren(); rv.notes.forEach(n => notes.append(el("div", null, n)));
      const ul = $("#findings"); ul.replaceChildren();
      if (rv.status === "done" && !rv.findings.length) ul.append(el("li", null, "No issues flagged."));
      rv.findings.forEach(f => {
        const li = el("li", "finding " + f.decision);
        const head = el("div", "fhead"); head.append(el("strong", null, `${f.check.replace("_", " ")} (${f.doc})`), el("span", null, f.message));
        li.append(head);
        if (f.evidence) li.append(el("div", "ev", f.evidence));
        if (f.suggestion) li.append(el("div", "sug", "Suggestion: " + f.suggestion));
        const act = el("div", "row-actions");
        [["accepted", "Accept"], ["ignored", "Ignore"], ["open", "Reset"]].forEach(([d, label]) => {
          const b = el("button", "small ghost", label); b.type = "button"; b.disabled = f.decision === d;
          b.addEventListener("click", async () => { await CQ.call(`/api/jobs/${jobId}/review/${f.id}`, { method: "POST", json: { decision: d } }); tick(); });
          act.append(b); });
        li.append(act); ul.append(li);
      });
      if (rv.status === "pending" || rv.status === "running") setTimeout(tick, 2000);
    };
    tick();
  }
})();
