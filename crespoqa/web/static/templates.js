(async () => {
  const { el, call } = CQ;
  const $ = s => document.querySelector(s);
  const me = await CQ.nav("/templates");
  if (!me) return;
  const isEditor = me.role === "editor";
  let current = null;   // { id|null, latest }
  let timer = null;

  const showErr = m => { const b = $("#error"); b.textContent = m || ""; b.hidden = !m; };
  const note = m => { const b = $("#note"); b.textContent = m || ""; b.hidden = !m; };
  const lineErr = m => { const b = $("#line-error"); b.textContent = m || ""; b.hidden = !m; };
  const fmt = ts => new Date(ts * 1000).toLocaleString();

  async function loadList() {
    showErr("");
    const { templates } = await call("/api/templates");
    const box = $("#cards"); box.replaceChildren();
    templates.forEach(t => {
      const card = el("div", "tcard"), left = el("div");
      left.append(el("h3", null, `${t.service}: ${t.report_type === "technical" ? "Technical" : "Executive"}`),
        el("small", null, `${t.name} · v${t.version} · ${fmt(t.updated_at)} · ${t.updated_by}`));
      const act = el("div", "row-actions");
      const btn = (label, fn, cls = "small") => { const b = el("button", cls, label); b.type = "button"; b.addEventListener("click", fn); act.append(b); };
      btn(isEditor ? "Edit" : "View", () => openEditor(t.id));
      if (isEditor) {
        btn("Duplicate", async () => {
          const svc = prompt(`Service name for the copy of "${t.service} ${t.report_type}":`);
          if (!svc) return;
          try { const r = await call(`/api/templates/${t.id}/duplicate`, { method: "POST", json: { service: svc } }); note("Duplicated."); openEditor(r.id); }
          catch (e) { showErr(e.message); }
        });
        btn("Delete", async () => {
          if (!confirm(`Delete ${t.service} ${t.report_type}? It stops being offered; old results stay explainable.`)) return;
          try { await call(`/api/templates/${t.id}`, { method: "DELETE" }); note("Deleted."); loadList(); } catch (e) { showErr(e.message); }
        }, "small danger");
      }
      card.append(left, act); box.append(card);
    });
    if (!templates.length) box.append(el("p", "muted", "No templates yet."));
    $("#new").hidden = !isEditor;
  }

  const NEW_TEXT = `# CrespoQA template. One step per line, run from top to bottom.
period: remove "Period:"
toc
details: title "Document Details" ; page 4 ; set author = consultant
tables: cantsplit on ; header rows 2 ; empty-page-breaks remove
refresh
`;

  async function openEditor(id, version) {
    showErr(""); note(""); lineErr("");
    if (id === null) {
      current = { id: null };
      $("#t-name").value = ""; $("#t-service").value = ""; $("#t-type").value = "technical";
      $("#t-text").value = NEW_TEXT;
      $("#t-versions").replaceChildren(new Option("New (unsaved)", ""));
      $("#h-edit").textContent = "New template";
    } else {
      const [t, v] = await Promise.all([call(`/api/templates/${id}${version ? `?version=${version}` : ""}`), call(`/api/templates/${id}/versions`)]);
      current = { id, latest: v.versions[0].version };
      $("#t-name").value = t.name; $("#t-service").value = t.service; $("#t-type").value = t.report_type;
      $("#t-text").value = t.text;
      const sel = $("#t-versions"); sel.replaceChildren();
      v.versions.forEach(x => sel.append(new Option(`v${x.version} · ${fmt(x.created_at)} · ${x.created_by}`, x.version)));
      sel.value = t.version;
      $("#h-edit").textContent = `${isEditor ? "Edit" : "View"} ${t.service} (${t.report_type})`;
    }
    $("#t-service").disabled = $("#t-type").disabled = id !== null;
    $("#t-text").readOnly = !isEditor; $("#t-name").disabled = !isEditor;
    $("#save").hidden = !isEditor;
    $("#list-view").hidden = true; $("#edit-view").hidden = false;
    preview();
  }

  function preview() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const ol = $("#plain");
      try {
        const { description } = await call("/api/templates/preview", { method: "POST", json: { text: $("#t-text").value } });
        ol.replaceChildren();
        description.forEach(d => ol.append(el("li", d.enabled ? "" : "off", d.text)));
        lineErr("");
      } catch (e) {
        ol.replaceChildren(el("li", "off", "Fix the line shown under the editor to see the preview."));
        lineErr(e.message);
      }
    }, 300);
  }

  $("#t-text").addEventListener("input", preview);
  $("#t-text").addEventListener("keydown", e => {            // Tab inserts spaces instead of leaving the box
    if (e.key === "Tab") { e.preventDefault(); const t = e.target, s = t.selectionStart; t.setRangeText("  ", s, t.selectionEnd, "end"); }
  });
  $("#help-toggle").addEventListener("click", async () => {
    const h = $("#help");
    if (h.hidden && !h.textContent) h.textContent = (await call("/api/templates-help")).help;
    h.hidden = !h.hidden; $("#help-toggle").textContent = h.hidden ? "Show help" : "Hide help";
  });
  $("#new").addEventListener("click", () => openEditor(null));
  $("#back").addEventListener("click", () => { $("#edit-view").hidden = true; $("#list-view").hidden = false; showErr(""); loadList(); });
  $("#t-versions").addEventListener("change", e => {
    if (current.id !== null && e.target.value) {
      openEditor(current.id, e.target.value).then(() => { if (Number(e.target.value) !== current.latest) note("Viewing an older version. Saving creates a new version from it."); });
    }
  });
  $("#save").addEventListener("click", async () => {
    showErr(""); note("");
    try {
      const name = $("#t-name").value, text = $("#t-text").value;
      if (current.id === null) {
        const r = await call("/api/templates", { method: "POST", json: { name, service: $("#t-service").value, report_type: $("#t-type").value, text } });
        await openEditor(r.id); note("Template created (v1).");
      } else {
        const r = await call(`/api/templates/${current.id}`, { method: "PUT", json: { name, text } });
        await openEditor(current.id); note(`Saved as version ${r.version}.`);
      }
    } catch (e) { lineErr(e.message); }
  });
  loadList().catch(e => showErr(e.message));
})();
