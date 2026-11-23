// The timeline of one Pertura session, drawn from its record: each mark is an event, time runs left to right, and the
// Tgfbr1 finding's path is drawn in the accent colour through the lanes. One drawing for the README's images (static,
// notes inside the figure) and for the website (a mark shows a short excerpt of the record on hover or tap).
(function () {
  const PALETTES = {
    light: { bg: "#ffffff", ink: "#18211f", ink2: "#4b5754", muted: "#7b8784", hair: "#e4e9e7", ctx: "#a9b2af",
             accent: "#1d746d" },
    dark: { bg: "#0d1117", ink: "#e6edf3", ink2: "#aab4b1", muted: "#7f8a87", hair: "#232a32", ctx: "#68736f",
            accent: "#5fc0b2" },
    site: { bg: "#121b19", ink: "#e1e9e7", ink2: "#a9b7b3", muted: "#7b8a86", hair: "#22302d", ctx: "#5f6e6a",
            accent: "#6cc3b6" },
  };
  const OUTCOMES = {  // a review comment's outcome, as the record names it, in words
    "holds": "the finding holds",
    "holds, narrowed": "the finding holds, with a narrower claim",
    "narrowed": "the claim becomes narrower",
    "withdrawn": "the finding is withdrawn",
  };
  const clock = m => `${Math.floor(m / 60)}:${String(Math.floor(m % 60)).padStart(2, "0")}`;
  const times = n => (n === 1 ? "once" : n === 2 ? "twice" : `${n} times`);

  function notesOf(S) {
    const F = S.findings.find(f => f.story), D = S.doubts.find(d => d.story), Wk = S.workers.find(w => w.story);
    const R2 = S.results.find(r => r.n === 2);
    return [
      [clock(F.t), "First reading: a clonal expansion in guide 1, not a knockout effect."],
      [clock(D.t), "Review comment: one dominant Trbv gene does not prove a clone. The claim becomes narrower."],
      [clock(Wk.first), "A subagent recomputes the Trbv3 share in each capture and draws the figure."],
      [clock(R2.t), "Result 2: a Trbv3-dominant population carried mainly by one guide in one experiment."],
    ];
  }

  function drawTimeline(svgNode, S, opts) {
    const C = PALETTES[opts.palette || "light"], tip = opts.tooltip || null;
    const W = 880, L = 160, R = 862;
    const x = d3.scaleLinear().domain([13 * 60 + 6, 15 * 60 + 26]).range([L, R]);

    // stack marks that would overlap: each takes the nearest free slot above or below its lane's line
    function dodge(items, r, gap, fixed) {
      const step = 2 * r + gap, placed = fixed.map(t => ({ cx: x(t), dy: 0 }));
      for (const it of items) {
        if (it.story) { it.cx = x(it.t); it.dy = 0; continue; }
        const cx = x(it.t);
        for (let i = 0; ; i++) {
          const dy = i === 0 ? 0 : (i % 2 ? -1 : 1) * Math.ceil(i / 2) * step;
          if (placed.every(p => (p.cx - cx) ** 2 + (p.dy - dy) ** 2 >= step * step)) {
            placed.push({ cx, dy }); it.cx = cx; it.dy = dy; break;
          }
        }
      }
      return d3.max(placed, p => Math.abs(p.dy)) || 0;
    }
    const RF = 3.1, RD = 3.9;
    const F = S.findings.find(f => f.story), D = S.doubts.find(d => d.story), Wk = S.workers.find(w => w.story);
    const R2 = S.results.find(r => r.n === 2);
    const spreadF = dodge(S.findings, RF, 1.1, [F.t, ...F.reworded]), spreadD = dodge(S.doubts, RD, 1.3, [D.t]);

    const svg = d3.select(svgNode);
    svg.selectAll("*").remove();
    const g = svg.append("g");
    const text = (x0, y0, s, o = {}) => g.append("text").attr("x", x0).attr("y", y0).text(s)
      .attr("font-size", o.size || 12).attr("font-weight", o.weight || 400).attr("fill", o.fill || C.ink2)
      .attr("text-anchor", o.anchor || "start").attr("class", o.mono ? "mono" : null);
    // a wider, invisible target over each mark: hover shows the excerpt, and a tap does the same on a touch screen
    const target = (cx, cy, w, h, html) => {
      const t = g.append("rect").attr("x", cx - w / 2).attr("y", cy - h / 2).attr("width", w).attr("height", h)
        .attr("fill", "transparent");
      if (tip) t.on("mouseenter", e => tip.show(e, html)).on("mousemove", e => tip.move(e)).on("mouseleave", () => tip.hide())
        .on("click", e => { e.stopPropagation(); tip.show(e, html); });
      return t;
    };

    const lanes = [];
    let y = 18;
    const lane = (key, name, count, h) => { lanes.push({ key, name, count, top: y, mid: y + h / 2, h }); y += h; };
    lane("design", "Design", "", 30);
    lane("agent", "Main agent", `${S.steps.filter(s => s.w === null).length} steps`, 34);
    lane("workers", "Subagents", `${S.workers.length} subagents, ${S.steps.filter(s => s.w !== null).length} steps`, S.workers.length * 9 + 14);
    lane("findings", "Findings", `${S.findings.length} findings`, 2 * spreadF + 2 * RF + 16);
    lane("review", "Review", `${S.doubts.length} comments`, 2 * spreadD + 2 * RD + 16);
    lane("report", "Report", `${S.results.length} results`, 34);
    const Y = Object.fromEntries(lanes.map(l => [l.key, l]));
    const axisY = y + 6;

    for (const l of lanes) {
      text(24, l.mid - 1, l.name, { size: 12.5, weight: 500, fill: C.ink });
      if (l.count) text(24, l.mid + 13, l.count, { size: 11, fill: C.muted });
      if (l.key !== "workers") g.append("line").attr("x1", L).attr("x2", R).attr("y1", l.mid).attr("y2", l.mid)
        .attr("stroke", C.hair).attr("stroke-width", 1);
    }
    g.append("line").attr("x1", L).attr("x2", R).attr("y1", axisY).attr("y2", axisY).attr("stroke", C.hair);
    for (let m = 13 * 60 + 15; m <= 15 * 60 + 15; m += 15) {
      g.append("line").attr("x1", x(m)).attr("x2", x(m)).attr("y1", axisY).attr("y2", axisY + 4).attr("stroke", C.muted);
      text(x(m), axisY + 17, clock(m), { size: 11, fill: C.muted, anchor: "middle", mono: true });
    }

    // the design, confirmed before any perturbation-effect analysis
    const xd = x(S.design);
    g.append("line").attr("x1", xd).attr("x2", xd).attr("y1", Y.design.mid).attr("y2", axisY).attr("stroke", C.ink2).attr("stroke-width", 1);
    g.append("path").attr("d", d3.symbol(d3.symbolDiamond, 70)()).attr("transform", `translate(${xd},${Y.design.mid})`).attr("fill", C.ink);
    text(xd + 10, Y.design.mid + 4, `${clock(S.design)}  Design confirmed`, { size: 11.5, fill: C.ink2 });

    // the main agent's steps and the subagents' steps
    const stepTip = (s, who) => `<b>${clock(s.t)}</b> ${who}<br>${esc(s.what)}`;
    for (const s of S.steps.filter(s => s.w === null)) {
      g.append("line").attr("x1", x(s.t)).attr("x2", x(s.t)).attr("y1", Y.agent.mid - 6).attr("y2", Y.agent.mid + 6)
        .attr("stroke", C.ctx).attr("stroke-width", 1.3);
      target(x(s.t), Y.agent.mid, 8, 20, stepTip(s, "Main agent step"));
    }
    S.workers.forEach((w, i) => {
      const wy = Y.workers.top + 8 + i * 9, col = w.story ? C.accent : C.ctx;
      w.y = wy;
      g.append("line").attr("x1", x(w.first)).attr("x2", x(w.last)).attr("y1", wy).attr("y2", wy)
        .attr("stroke", col).attr("stroke-width", w.story ? 1.4 : 1).attr("opacity", w.story ? 1 : 0.55);
      for (const s of S.steps.filter(s => s.w === i)) {
        g.append("line").attr("x1", x(s.t)).attr("x2", x(s.t)).attr("y1", wy - 3.2).attr("y2", wy + 3.2)
          .attr("stroke", col).attr("stroke-width", w.story ? 1.6 : 1.2);
        target(x(s.t), wy, 8, 9, stepTip(s, "Subagent step"));
      }
    });

    // the Tgfbr1 finding's path: recorded, revised, its review comment, its subagent, its result
    const fy = Y.findings.mid + F.dy, last = F.reworded[F.reworded.length - 1];
    const path = g.append("g").attr("fill", "none").attr("stroke", C.accent).attr("stroke-width", 1.5);
    path.append("path").attr("d", `M${x(F.t)},${fy} H${x(last)}`);
    const link = d3.linkVertical().x(p => p[0]).y(p => p[1]);
    path.append("path").attr("d", link({ source: [x(F.t), fy + RF], target: [x(D.t), Y.review.mid + D.dy - RD] }));
    path.append("path").attr("d", link({ source: [x(Wk.last), Wk.y + 3], target: [x(last), fy - RF] }));
    path.append("path").attr("d", link({ source: [x(last), fy + RF], target: [x(R2.t), Y.report.mid - 4] }));

    // findings, review comments, results
    for (const f of S.findings) {
      if (f.story) continue;
      g.append("circle").attr("cx", f.cx).attr("cy", Y.findings.mid + f.dy).attr("r", RF).attr("fill", C.ctx);
      target(f.cx, Y.findings.mid + f.dy, 9, 9, `<b>${clock(f.t)}</b> Finding<br>${esc(f.words[0])}`
        + (f.words.length > 1 ? `<br><i>Revised ${times(f.words.length - 1)}.</i>` : ""));
    }
    [F.t, ...F.reworded].forEach((t, i) => {
      g.append("circle").attr("cx", x(t)).attr("cy", fy).attr("r", RF + 0.6).attr("fill", C.accent);
      target(x(t), fy, 11, 11, `<b>${clock(t)}</b> ${i === 0 ? "First reading" : "Revised finding"}<br>${esc(F.words[i])}`);
    });
    for (const d of S.doubts) {
      g.append("path").attr("d", d3.symbol(d3.symbolTriangle, 58)())
        .attr("transform", `translate(${d.cx},${Y.review.mid + d.dy + 1})`).attr("fill", d.story ? C.accent : C.ctx);
      target(d.cx, Y.review.mid + d.dy, 11, 10, `<b>${clock(d.t)}</b> Review comment<br>${esc(d.words)}`
        + (d.answer ? `<br><i>Outcome: ${esc(OUTCOMES[d.answer] || d.answer)}.</i>` : ""));
    }
    for (const r of S.results) {
      const col = r.n === 2 ? C.accent : C.ctx;
      g.append("rect").attr("x", x(r.t) - 4.5).attr("y", Y.report.mid - 4.5).attr("width", 9).attr("height", 9).attr("fill", col);
      text(x(r.t), Y.report.mid + 19, `${r.n}`, { size: 10.5, fill: r.n === 2 ? C.accent : C.muted, anchor: "middle", mono: true });
      target(x(r.t), Y.report.mid, 13, 13, `<b>${clock(r.t)}</b> Result ${r.n} in the report<br>${esc(r.title)}`);
    }

    // callouts: the numbers of the steps that the text under the figure explains
    const callout = (n, px, py, dx, dy) => {
      g.append("line").attr("x1", px).attr("y1", py).attr("x2", px + dx).attr("y2", py + dy).attr("stroke", C.accent).attr("stroke-width", 1);
      text(px + dx + (dx < 0 ? -3 : 3), py + dy + 4, n, { size: 12.5, weight: 600, fill: C.accent, anchor: dx < 0 ? "end" : "start" });
    };
    callout("1", x(F.t) - RF - 1, fy - 2, -16, -10);
    callout("2", x(D.t) - RD - 1, Y.review.mid + D.dy, -18, 8);
    callout("3", x(Wk.first) - 2, Wk.y, -16, -6);
    callout("4", x(R2.t) + 6, Y.report.mid, 14, 0);

    let H = axisY + 30;
    if (opts.notesInside) {
      notesOf(S).forEach(([t, s], i) => {
        const ny = axisY + 46 + i * 20;
        text(24, ny, `${i + 1}`, { size: 12.5, weight: 600, fill: C.accent });
        text(40, ny, t, { size: 11.5, fill: C.muted, mono: true });
        text(84, ny, s, { size: 12.5, fill: C.ink2 });
      });
      H = axisY + 46 + 4 * 20 + 10;
    }
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    if (opts.fixedWidth) svg.attr("width", W).attr("height", H);
    return H;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  window.PerturaTimeline = { draw: drawTimeline, notes: notesOf };
})();
