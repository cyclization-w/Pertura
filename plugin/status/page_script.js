// The page's views, one shown at a time by the address's #part; the overview's parts, one at a time by its tabs; the
// search; the theme; an enlarged figure; and the
// reload, which keeps the view, its place, the folds opened and the timeline's end in sight. Without scripts every
// view shows, one after another.
(function () {
  var root = document.documentElement;
  var views = Array.prototype.slice.call(document.querySelectorAll("section.view"));
  var links = Array.prototype.slice.call(document.querySelectorAll(".nav a[data-view]"));
  var crumbs = document.getElementById("crumbs");
  var box = document.getElementById("lightbox"), big = box.querySelector("img");
  var pal = document.getElementById("palette"), q = document.getElementById("q"), res = document.getElementById("res");
  var KEY = "pertura-place-" + location.pathname;
  var saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); sessionStorage.removeItem(KEY); } catch (e) {}
  if ("scrollRestoration" in history) { history.scrollRestoration = "manual"; }

  function viewOf(el) {
    while (el && !(el.classList && el.classList.contains("view"))) { el = el.parentElement; }
    return el;
  }
  function atEnd() { return window.innerHeight + window.scrollY >= document.body.scrollHeight - 40; }

  function route(first) {
    var id = decodeURIComponent((location.hash || "#overview").slice(1));
    var target = document.getElementById(id) || document.getElementById("overview");
    var view = viewOf(target) || views[0];
    views.forEach(function (v) { v.classList.toggle("on", v === view); });
    links.forEach(function (a) {
      if (a.getAttribute("data-view") === view.id) { a.setAttribute("aria-current", "page"); }
      else { a.removeAttribute("aria-current"); }
    });
    if (crumbs) { crumbs.textContent = view.getAttribute("data-crumbs") || ""; }
    document.querySelector(".side").classList.remove("open");
    if (first && saved && saved.hash === location.hash) {
      (saved.open || []).forEach(function (k) {
        var d = document.querySelector('details[data-k="' + k + '"]');
        if (d) { d.open = true; }
      });
    }
    function place() {
      if (first && saved && saved.hash === location.hash) {
        window.scrollTo(0, saved.end ? document.body.scrollHeight : saved.y);
      } else if (target !== view) {
        target.scrollIntoView();
      } else {
        // the newest step, or the newest message, in sight
        window.scrollTo(0, view.id === "timeline" || view.id === "conversation" ? document.body.scrollHeight : 0);
      }
    }
    place();
    if (first) {  // the browser's own move to the address's #part comes after the page loads: place it again
      window.addEventListener("load", function () { setTimeout(place, 0); });
    }
  }
  window.addEventListener("hashchange", function () { route(false); });
  route(true);

  document.getElementById("menu").addEventListener("click", function () {
    document.querySelector(".side").classList.toggle("open");
  });
  // the overview's parts, one at a time; the reader's choice kept across the page's reloads
  var TAB = "pertura-tab-" + location.pathname;
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".tab-panel"));
  function showTab(k) {
    if (!panels.some(function (p) { return p.getAttribute("data-panel") === k; })) { return; }
    tabs.forEach(function (b) {
      var on = b.getAttribute("data-tab") === k;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    panels.forEach(function (p) { p.classList.toggle("on", p.getAttribute("data-panel") === k); });
  }
  try { showTab(sessionStorage.getItem(TAB)); } catch (e) {}
  tabs.forEach(function (b) {
    b.addEventListener("click", function () {
      showTab(b.getAttribute("data-tab"));
      try { sessionStorage.setItem(TAB, b.getAttribute("data-tab")); } catch (e) {}
    });
  });
  document.getElementById("theme").addEventListener("click", function () {
    var next = { "": "light", "light": "dark", "dark": "" }[root.getAttribute("data-theme") || ""];
    if (next) { root.setAttribute("data-theme", next); } else { root.removeAttribute("data-theme"); }
    try { localStorage.setItem("pertura-theme", next); } catch (e) {}
  });

  // the search: findings, results, steps, threads and the entries read, by their words
  var data = [];
  try { data = JSON.parse(document.getElementById("search-data").textContent); } catch (e) {}
  function draw() {
    var w = q.value.trim().toLowerCase(), groups = {}, order = [];
    data.forEach(function (x) {
      if (w && (x.t + " " + (x.s || "")).toLowerCase().indexOf(w) < 0) { return; }
      if (!groups[x.g]) { groups[x.g] = []; order.push(x.g); }
      if (groups[x.g].length < 8) { groups[x.g].push(x); }
    });
    res.textContent = "";
    if (!order.length) {
      var none = document.createElement("p");
      none.className = "none";
      none.textContent = "No matches";
      res.appendChild(none);
    }
    order.forEach(function (g) {
      var h = document.createElement("div");
      h.className = "grp";
      h.textContent = g;
      res.appendChild(h);
      groups[g].forEach(function (x) {
        var a = document.createElement("a");
        a.href = x.h;
        a.textContent = x.t;
        if (x.s) {
          var s = document.createElement("span");
          s.className = "muted";
          s.textContent = " · " + x.s;
          a.appendChild(s);
        }
        if (x.ext) { a.target = "_blank"; a.rel = "noopener"; }
        a.addEventListener("click", function () { close(); });
        res.appendChild(a);
      });
    });
    var one = res.querySelector("a");
    if (one) { one.classList.add("sel"); }
  }
  var opener = null;  // what had the focus before the search opened, given it back on closing
  function open() { opener = document.activeElement; pal.hidden = false; q.value = ""; draw(); q.focus(); }
  function close() {
    if (pal.hidden) { return; }
    pal.hidden = true;
    if (opener && opener.focus) { opener.focus(); }
  }
  function move(by) {
    var all = Array.prototype.slice.call(res.querySelectorAll("a")), at = all.indexOf(res.querySelector("a.sel"));
    if (!all.length) { return; }
    if (at >= 0) { all[at].classList.remove("sel"); }
    var next = all[(at + by + all.length) % all.length];
    next.classList.add("sel");
    next.scrollIntoView({ block: "nearest" });
  }
  q.addEventListener("input", draw);
  q.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { var s = res.querySelector("a.sel"); if (s) { s.click(); } }
  });
  document.getElementById("search").addEventListener("click", open);
  pal.addEventListener("click", function (e) { if (e.target === pal) { close(); } });
  document.addEventListener("keydown", function (e) {
    var typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "");
    if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); open(); }
    else if (e.key === "Escape") { close(); box.hidden = true; }
    else if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("fig")) {
      e.preventDefault(); enlarge(e.target);
    }
  });

  // a click (or Enter) enlarges a figure, with a link to the file at full size; a click closes it
  var full = document.getElementById("lb-full");
  function enlarge(img) { big.src = img.src; big.alt = img.alt; full.href = img.getAttribute("src"); box.hidden = false; }
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t.classList && t.classList.contains("fig")) { enlarge(t); }
    else if (!box.hidden && box.contains(t)) { box.hidden = true; }
  });

  // the page as a window of the session (window.PERTURA_LINK: the helper the session started): the conversation, the
  // user's words sent as the session's next turn, and Pertura's form while it waits; with no session to reach, each
  // object's button copies its id
  var LINK = window.PERTURA_LINK, talk = document.getElementById("talk"), formBox = document.getElementById("form-box");
  var say = document.getElementById("say"), about = document.getElementById("about"), sent = document.getElementById("sent");
  var DRAFT = "pertura-draft-" + location.pathname, lastN = 0, openForm = null;
  var ABOUT = /\n\n\[From the page, about: ([^\]\n]+)\]$/;  // the line the composer adds for the agent, shown as words
  function post(path, body) {
    body.token = LINK.token;
    return fetch("http://127.0.0.1:" + LINK.port + path, { method: "POST", headers: { "Content-Type": "text/plain" },
                                                         body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { throw j; } return j; }); });
  }
  function said(x) {
    var d = document.createElement("div"), w = document.createElement("div"), t = document.createElement("div");
    var m = x.who === "you" ? ABOUT.exec(x.text) : null;
    d.className = "say " + (x.who === "you" ? "you" : "pertura");
    w.className = "who";
    w.textContent = (x.who === "you" ? "You" + (x.where === "page" ? " · from this page" : x.where === "terminal" ?
      " · in the terminal" : "") + (m ? " · about " + m[1] : "") : "Pertura") + " · " +
      new Date(x.time * 1000).toTimeString().slice(0, 5);
    t.className = "text";
    t.textContent = m ? x.text.slice(0, m.index) : x.text;
    d.appendChild(w);
    d.appendChild(t);
    return d;
  }
  function field(name, p, need, marked) {
    var f = document.createElement("div"), label = document.createElement("div");
    f.className = "field";
    label.className = "label";
    label.textContent = (p.title || name) + (need || !marked ? "" : " (optional)");  // marked: the form names some required
    f.appendChild(label);
    if (p.description) {
      var d = document.createElement("p");
      d.className = "desc";
      d.textContent = p.description;
      f.appendChild(d);
    }
    if (p.enum) {
      var opts = document.createElement("div");
      opts.className = "opts";
      p.enum.forEach(function (v, i) {
        var l = document.createElement("label"), r = document.createElement("input");
        r.type = "radio"; r.name = name; r.value = v; r.checked = p["default"] === v;
        l.appendChild(r);
        l.appendChild(document.createTextNode(" " + ((p.enumNames && p.enumNames[i]) || v)));
        opts.appendChild(l);
      });
      f.appendChild(opts);
    } else if (p.type === "boolean") {  // a checklist row: the box beside the field's own words
      var l2 = document.createElement("label"), c = document.createElement("input");
      c.type = "checkbox"; c.name = name; c.checked = !!p["default"];
      l2.className = "check";
      l2.appendChild(c);
      l2.appendChild(document.createTextNode(" " + label.textContent));
      f.replaceChild(l2, label);
    } else if (p.type === "number" || p.type === "integer") {
      var n = document.createElement("input");
      n.type = "number"; n.name = name; n.step = p.type === "integer" ? "1" : "any";
      if (p["default"] != null) { n.value = p["default"]; }
      f.appendChild(n);
    } else {
      var t = document.createElement("textarea");
      t.name = name; t.rows = 2; t.value = p["default"] || "";
      f.appendChild(t);
    }
    return f;
  }
  function showForm(form) {
    if (!form) { openForm = null; formBox.hidden = true; return; }
    document.getElementById("form-clock").textContent = "If unanswered, it moves to the terminal in " +
      Math.max(0, Math.round((form.deadline - Date.now() / 1000) / 60)) + " min";
    if (openForm && openForm.id === form.id) { return; }
    openForm = form;
    document.getElementById("form-message").textContent = form.message || "";
    var fields = document.getElementById("form-fields"), props = (form.schema && form.schema.properties) || {};
    var need = (form.schema && form.schema.required) || [];
    fields.textContent = "";
    Object.keys(props).forEach(function (name) {
      fields.appendChild(field(name, props[name], need.indexOf(name) >= 0, need.length > 0));
    });
    formBox.hidden = false;
  }
  function answer(action) {
    if (!openForm) { return; }
    var content = {}, props = (openForm.schema && openForm.schema.properties) || {};
    Object.keys(props).forEach(function (name) {
      var p = props[name], at = '[name="' + CSS.escape(name) + '"]';
      if (p.enum) { var r = formBox.querySelector("input" + at + ":checked"); if (r) { content[name] = r.value; } }
      else if (p.type === "boolean") { content[name] = formBox.querySelector("input" + at).checked; }
      else if (p.type === "number" || p.type === "integer") {
        var v = formBox.querySelector("input" + at).value;
        if (v !== "") { content[name] = Number(v); }
      } else { var t = formBox.querySelector("textarea" + at).value; if (t !== "") { content[name] = t; } }
    });
    post("/answer", { id: openForm.id, action: action, content: content }).then(function () { showForm(null); })
      .catch(function (j) {
        if (j && j.ok === false) {  // the form is no longer the page's
          showForm(null);
          sent.textContent = "Not sent: the form had moved to the terminal. Answer it there.";
        } else {  // the answers stay on the form
          document.getElementById("form-clock").textContent =
            "Not sent: the session cannot be reached. Try again, or answer in the terminal.";
        }
      });
  }
  var pill = document.querySelector(".pill"), pillLine = pill && pill.querySelector(".l1");
  var pillSaid = pillLine && pillLine.textContent;
  function waiting(on) {  // a form the session holds open: the pill says so and leads to it
    if (!pillLine) { return; }
    pillLine.textContent = on ? "Needs your input · a form is waiting" : pillSaid;
    pill.setAttribute("href", on ? "#conversation" : "#timeline");
  }
  function setAbout(ref, title) {
    if (!about) { return; }
    try {
      if (ref) { sessionStorage.setItem(DRAFT + "-about", JSON.stringify([ref, title || ""])); }
      else { sessionStorage.removeItem(DRAFT + "-about"); }
    } catch (e) {}
    about.textContent = "";
    about.hidden = !ref;
    if (!ref) { about.removeAttribute("data-about"); return; }
    about.setAttribute("data-about", ref + (title ? " (" + title + ")" : ""));
    about.appendChild(document.createTextNode("About " + ref));
    var x = document.createElement("button");
    x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", "Remove");
    x.addEventListener("click", function () { setAbout(null); });
    about.appendChild(x);
  }
  function poll() {  // visible: the page counts as open, for a form, only while it is on the screen
    post("/state", { after: lastN, visible: document.visibilityState === "visible" }).then(function (s) {
      document.getElementById("talk-state").textContent = "Connected to this Claude Code session.";
      (s.said || []).forEach(function (x) { talk.appendChild(said(x)); lastN = Math.max(lastN, x.n); });
      if ((s.said || []).length && location.hash === "#conversation") { window.scrollTo(0, document.body.scrollHeight); }
      showForm(s.form);
      waiting(!!s.form);
    }).catch(function () {
      document.getElementById("talk-state").textContent = "Not connected: the session has ended, or not started.";
    });
  }
  if (LINK && say) {
    try {
      say.value = sessionStorage.getItem(DRAFT) || "";
      var kept = JSON.parse(sessionStorage.getItem(DRAFT + "-about") || "null");
      if (kept) { setAbout(kept[0], kept[1]); }
    } catch (e) {}
    say.addEventListener("input", function () { try { sessionStorage.setItem(DRAFT, say.value); } catch (e) {} });
    say.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); document.getElementById("composer").requestSubmit(); }
    });
    document.getElementById("composer").addEventListener("submit", function (e) {
      e.preventDefault();
      var text = say.value.trim(), ref = about.getAttribute("data-about");
      if (!text) { return; }
      post("/ask", { text: ref ? text + "\n\n[From the page, about: " + ref + "]" : text }).then(function () {
        say.value = "";
        setAbout(null);
        try { sessionStorage.removeItem(DRAFT); } catch (e2) {}
        sent.textContent = "Sent. It becomes the session's next turn when the session is free.";
      }).catch(function () { sent.textContent = "Not sent: the session cannot be reached."; });
    });
    formBox.addEventListener("submit", function (e) { e.preventDefault(); answer("accept"); });
    Array.prototype.slice.call(formBox.querySelectorAll("[data-action]")).forEach(function (b) {
      b.addEventListener("click", function () { answer(b.getAttribute("data-action")); });
    });
    poll();
    setInterval(poll, 2000);  // a tab in the background is slowed by the browser; back in front it asks at once
    document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible") { poll(); } });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("button.ask") : null;
    if (!b) { return; }
    if (b.hasAttribute("data-copy")) {
      var id = b.getAttribute("data-copy");
      (navigator.clipboard ? navigator.clipboard.writeText(id) : Promise.reject(new Error("no clipboard")))
        .then(function () { b.textContent = "Copied"; }, function () { b.textContent = id; });
      return;
    }
    setAbout(b.getAttribute("data-about"), b.getAttribute("data-title"));
    if (say && b.hasAttribute("data-say")) {  // a choice is put in the box, never sent: the user sends it
      say.value = b.getAttribute("data-say");
      try { sessionStorage.setItem(DRAFT, say.value); } catch (e2) {}
    }
    location.hash = "#conversation";
    if (say) { setTimeout(function () { say.focus(); }, 50); }
  });

  // the page is written again after each call: read it again, unless the reader is in the middle of something
  setInterval(function () {
    var a = document.activeElement;
    if (!box.hidden || !pal.hidden || document.querySelector(".side.open") ||
        String(window.getSelection ? window.getSelection() : "") ||
        (a && /INPUT|TEXTAREA|SELECT/.test(a.tagName)) || (formBox && !formBox.hidden)) { return; }
    var opened = Array.prototype.slice.call(document.querySelectorAll("details[open][data-k]"))
      .map(function (d) { return d.getAttribute("data-k"); });
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ hash: location.hash, y: window.scrollY, open: opened,
                                                   end: location.hash === "#timeline" && atEnd() }));
    } catch (e) {}
    location.reload();
  }, RELOAD_MS);
})();
