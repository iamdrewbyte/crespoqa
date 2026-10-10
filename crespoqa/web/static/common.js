// Shared helpers: CSRF header, 401 redirect, DOM builder, header nav.
window.CQ = (() => {
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  async function api(url, opts = {}) {
    opts.headers = Object.assign({ "X-Requested-With": "CrespoQA" }, opts.headers || {});
    if (opts.json !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); delete opts.json; }
    const r = await fetch(url, opts);
    if (r.status === 401 && !location.pathname.startsWith("/login")) { location.href = "/login"; throw new Error("Please sign in."); }
    return r;
  }
  async function call(url, opts) {
    const r = await api(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Request failed.");
    return body;
  }
  async function nav(active) {
    const bar = document.querySelector(".bar nav"); if (!bar) return null;
    let me; try { me = await call("/api/me"); } catch { return null; }
    bar.replaceChildren();
    [["/", "Run QA"], ["/templates", "Templates"]].forEach(([href, label]) => {
      const a = el("a", null, label); a.href = href; if (href === active) a.setAttribute("aria-current", "page"); bar.append(a); });
    if (me.auth !== false) {                              // no account chrome when login is switched off
      const who = el("span", "who", `${me.username} (${me.role})`);
      const out = el("button", "ghost small", "Sign out");
      out.addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); location.href = "/login"; });
      bar.append(who, out);
    }
    return me;
  }
  return { el, api, call, nav };
})();
