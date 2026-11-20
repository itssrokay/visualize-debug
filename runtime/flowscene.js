/* flowscene.js — runtime for animated code-flow scenes. One file, no dependencies.
 * Reads window.FLOW_SCENES (array of scene specs) and renders into #flow-app.
 * Test/automation API: window.FLOW = { ready, seek(step, u), play(), pause(), tab(i), check(), state() }.
 */
(function () {
  'use strict';
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, k) => a + (b - a) * k;
  const seg = (t, a, b) => clamp((t - a) / (b - a), 0, 1);
  const E = {
    soft: (k) => 0.5 - 0.5 * Math.cos(Math.PI * k),
    out3: (k) => 1 - Math.pow(1 - k, 3),
    back: (k) => { const c = 1.7; return 1 + (c + 1) * Math.pow(k - 1, 3) + c * Math.pow(k - 1, 2); },
  };
  const hash = (n) => { n = Math.sin(n * 127.1 + 311.7) * 43758.5453; return n - Math.floor(n); };
  const strHash = (s) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h) % 9973; };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

  const THEMES = {
    light: { paper: '#fbfaf6', ink: '#141414', ink2: '#55534e', soft: '#9a978f', faint: '#dedad0', grid: '#e8e4d9', card: '#ffffff',
      butter: '#ffd84d', butterSoft: '#fff4c7', red: '#d64545', redSoft: '#fde4e4', green: '#2f9e5b', greenSoft: '#e1f3e7',
      blue: '#3866c9', blueSoft: '#e3eafb', orange: '#d9822b', orangeSoft: '#fcebd7' },
    dark: { paper: '#16161a', ink: '#ecebe6', ink2: '#bdbab3', soft: '#7a7871', faint: '#3a3940', grid: '#25252b', card: '#1f1f25',
      butter: '#f5c84c', butterSoft: '#40381f', red: '#ff6b6b', redSoft: '#3e2325', green: '#4cc38a', greenSoft: '#1d3328',
      blue: '#7aa2ff', blueSoft: '#1f2a44', orange: '#f0a050', orangeSoft: '#3b2c1c' },
  };
  const MODCOL = ['#3d7a8c', '#d97757', '#6a5acd', '#2f9e5b', '#c2410c', '#a21caf', '#0e7490', '#65a30d', '#b45309', '#be123c', '#1d4ed8', '#78716c', '#0f766e', '#9333ea'];
  const FONT = '"Balsamiq Sans","Comic Neue","Chalkboard SE","Comic Sans MS","Segoe Print","Bradley Hand",system-ui,-apple-system,"Segoe UI",sans-serif';
  const MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace';
  const KINDS = ['call', 'return', 'async', 'event', 'error', 'slow', 'self', 'note', 'focus'];
  const KIND_COLOR = (T, k) => ({ call: T.ink, return: T.green, async: T.blue, event: T.blue, error: T.red, slow: T.orange, self: T.ink2, note: T.soft, focus: T.butter }[k] || T.ink);

  /* ------------------------------------------------------------------ spec normalisation */
  function normalize(raw, idx) {
    const sc = JSON.parse(JSON.stringify(raw || {}));
    sc.id = sc.id || 'scene' + (idx + 1);
    sc.title = sc.title || sc.id;
    sc.mode = sc.mode || (sc.steps && sc.steps.length ? 'flow' : 'map');
    sc.nodes = sc.nodes || [];
    sc.steps = sc.steps || [];
    sc.edges = sc.edges || [];
    sc.groups = sc.groups || [];
    sc.warnings = [];
    sc.byId = {};
    sc.nodes.forEach((n, i) => {
      if (typeof n === 'string') n = sc.nodes[i] = { id: n };
      n.id = String(n.id);
      if (sc.byId[n.id]) sc.warnings.push('duplicate node id ' + n.id);
      n.label = n.label || n.id;
      n.order = i;
      sc.byId[n.id] = n;
    });
    const ensure = (id) => {
      if (id == null) return;
      id = String(id);
      if (!sc.byId[id]) {
        sc.warnings.push('step/edge refers to unknown node "' + id + '" (auto-created)');
        const n = { id, label: id, order: sc.nodes.length, auto: true };
        sc.nodes.push(n); sc.byId[id] = n;
      }
    };
    sc.steps.forEach((s, i) => {
      if (s.node && !s.from) { s.from = s.to = s.node; }
      if (s.at && !s.from) { s.from = s.to = s.at; }
      ensure(s.from); ensure(s.to);
      s.from = String(s.from); s.to = String(s.to || s.from);
      s.kind = s.kind || (s.from === s.to ? 'self' : 'call');
      if (!KINDS.includes(s.kind)) { sc.warnings.push('step ' + (i + 1) + ': unknown kind "' + s.kind + '" → call'); s.kind = 'call'; }
      if (s.from === s.to && (s.kind === 'call' || s.kind === 'return')) s.kind = 'self';
      s.label = s.label || (s.kind === 'return' ? 'return' : '');
      s.i = i;
    });
    sc.edges.forEach((e) => { ensure(e.from); ensure(e.to); e.from = String(e.from); e.to = String(e.to); e.weight = e.weight || 1; });
    // groups / modules → colours
    const gmap = {};
    sc.groups.forEach((g, i) => { g.color = g.color || MODCOL[i % MODCOL.length]; gmap[g.id] = g; (g.nodes || []).forEach((id) => { if (sc.byId[id]) sc.byId[id].group = g.id; }); });
    sc.nodes.forEach((n) => {
      const gid = n.group || n.module;
      if (gid != null && !gmap[gid]) { const g = { id: gid, label: gid, color: MODCOL[sc.groups.length % MODCOL.length] }; sc.groups.push(g); gmap[gid] = g; }
      n.group = gid; n.gcolor = gid != null ? gmap[gid].color : null;
      n.sub = n.sub != null ? n.sub : [n.kind && n.kind !== 'module' ? n.kind : null, n.group && n.group !== n.label ? n.group : null].filter(Boolean).join(' · ');
    });
    sc.gmap = gmap;
    if (sc.nodes.length > 28) sc.warnings.push(sc.nodes.length + ' nodes: too many to read. Split into module drill-downs.');
    return sc;
  }

  /* ------------------------------------------------------------------ layout */
  function layout(sc, measure) {
    const N = sc.nodes;
    for (const n of N) {
      const lw = measure(n.label, 13, true), sw = n.sub ? measure(n.sub, 10, false) : 0;
      n.w = Math.max(92, Math.ceil(Math.max(lw, sw) + 30) + (n.kind === 'client' || n.kind === 'user' ? 14 : 0));
      n.h = n.sub ? 48 : 38;
      if (n.kind === 'db' || n.kind === 'cache') n.h += 12;
    }
    // first-appearance order
    const appear = {}; let k = 0;
    sc.steps.forEach((s) => { [s.from, s.to].forEach((id) => { if (appear[id] == null) appear[id] = k++; }); });
    sc.edges.forEach((e) => { [e.from, e.to].forEach((id) => { if (appear[id] == null) appear[id] = k++; }); });
    N.forEach((n) => { if (appear[n.id] == null) appear[n.id] = k++; });
    const pairs = [];
    const fwd = new Set(sc.steps.filter((s) => !['return', 'error'].includes(s.kind)).map((s) => s.from + '>' + s.to));
    sc.steps.forEach((s) => {
      if (s.from === s.to || s.kind === 'return') return;
      if (s.kind === 'error' && fwd.has(s.to + '>' + s.from)) return; // an error travelling back up a call
      pairs.push([s.from, s.to]);
    });
    sc.edges.forEach((e) => { if (e.from !== e.to) pairs.push([e.from, e.to]); });
    // ranks: longest path on DAG (back edges dropped by DFS)
    const adj = {}; N.forEach((n) => (adj[n.id] = []));
    pairs.forEach(([a, b]) => { if (!adj[a].includes(b)) adj[a].push(b); });
    const st = {}, dag = {}; N.forEach((n) => (dag[n.id] = []));
    const dfs = (u) => { st[u] = 1; for (const v of adj[u]) { if (st[v] === 1) continue; dag[u].push(v); if (!st[v]) dfs(v); } st[u] = 2; };
    N.slice().sort((a, b) => appear[a.id] - appear[b.id]).forEach((n) => { if (!st[n.id]) dfs(n.id); });
    const indeg = {}; N.forEach((n) => (indeg[n.id] = 0));
    N.forEach((n) => dag[n.id].forEach((v) => indeg[v]++));
    const rank = {}; N.forEach((n) => (rank[n.id] = 0));
    const q = N.filter((n) => !indeg[n.id]).map((n) => n.id);
    while (q.length) { const u = q.shift(); for (const v of dag[u]) { rank[v] = Math.max(rank[v], rank[u] + 1); if (--indeg[v] === 0) q.push(v); } }
    N.forEach((n) => { if (Number.isFinite(n.layer)) rank[n.id] = n.layer; n.rank = rank[n.id]; });
    let nr = Math.max(0, ...N.map((n) => n.rank)) + 1;
    let cols = Array.from({ length: nr }, () => []);
    N.forEach((n) => cols[n.rank].push(n));
    // wrap an over-tall column (common for loosely connected maps) into several columns
    const per = Math.max(5, Math.ceil(Math.sqrt(N.length * 1.6)));
    if (cols.some((c) => c.length > per + 1) && !N.some((n) => Number.isFinite(n.layer))) {
      const out = [];
      cols.forEach((c) => { c.sort((a, b) => appear[a.id] - appear[b.id]); for (let i = 0; i < c.length; i += per) out.push(c.slice(i, i + per)); });
      cols = out; nr = cols.length; cols.forEach((c, i) => c.forEach((n) => (n.rank = i)));
    }
    const gidx = {}; sc.groups.forEach((g, i) => (gidx[g.id] = i));
    cols.forEach((c) => c.sort((a, b) => (a.row ?? 1e9) - (b.row ?? 1e9) || (gidx[a.group] ?? 99) - (gidx[b.group] ?? 99) || appear[a.id] - appear[b.id]));
    // barycentre sweeps (skip columns with manual rows)
    const nb = {}; N.forEach((n) => (nb[n.id] = []));
    pairs.forEach(([a, b]) => { nb[a].push(b); nb[b].push(a); });
    const pos = () => { const p = {}; cols.forEach((c) => c.forEach((n, i) => (p[n.id] = i / Math.max(1, c.length - 1 || 1)))); return p; };
    for (let pass = 0; pass < 4; pass++) {
      const p = pos();
      const order = pass % 2 ? cols.slice().reverse() : cols;
      order.forEach((c) => {
        if (c.some((n) => n.row != null)) return;
        const key = {};
        c.forEach((n, i) => { const ns = nb[n.id].filter((id) => sc.byId[id].rank !== n.rank); key[n.id] = ns.length ? ns.reduce((s, id) => s + p[id], 0) / ns.length : i / Math.max(1, c.length - 1); });
        c.sort((a, b) => key[a.id] - key[b.id] || appear[a.id] - appear[b.id]);
      });
    }
    // direction: TB for deep, thin graphs (stack traces), LR otherwise
    const maxCol = Math.max(...cols.map((c) => c.length));
    let dir = sc.direction;
    if (!dir) dir = nr > 6 && maxCol <= 3 ? 'TB' : 'LR';
    sc.dir = dir;
    const PAD = 36, TOP = 78;
    const GR = dir === 'LR' ? 118 : 74, GC = dir === 'LR' ? 30 : 46;
    const along = (n) => (dir === 'LR' ? n.w : n.h), across = (n) => (dir === 'LR' ? n.h : n.w);
    const sizes = cols.map((c) => Math.max(0, ...c.map(along)));
    const spans = cols.map((c) => c.reduce((s, n) => s + across(n), 0) + GC * Math.max(0, c.length - 1));
    const maxSpan = Math.max(0, ...spans);
    let a = 0;
    cols.forEach((c, ci) => {
      let b = (maxSpan - spans[ci]) / 2;
      c.forEach((n) => {
        const ra = a + (sizes[ci] - along(n)) / 2;
        if (dir === 'LR') { n.x = PAD + ra; n.y = TOP + b; } else { n.x = PAD + 40 + b; n.y = TOP + ra; }
        b += across(n) + GC;
      });
      a += sizes[ci] + GR;
    });
    sc.worldW = Math.max(...N.map((n) => n.x + n.w), 200) + PAD + (dir === 'TB' ? 40 : 0);
    sc.worldH = Math.max(...N.map((n) => n.y + n.h), 120) + PAD + 10;
    buildPaths(sc);
    // make room above arcs that bulge upward (courier + its tag need ~75 units)
    let minY = Infinity;
    for (const k in sc.paths) for (const o of sc.paths[k].table) minY = Math.min(minY, o.pt.y);
    const need = 78 - minY;
    if (need > 0) { N.forEach((n) => (n.y += need)); sc.worldH += need; buildPaths(sc); }
  }

  /* ------------------------------------------------------------------ edge geometry */
  const bez = (p, t) => {
    const u = 1 - t;
    return { x: u * u * u * p[0].x + 3 * u * u * t * p[1].x + 3 * u * t * t * p[2].x + t * t * t * p[3].x,
             y: u * u * u * p[0].y + 3 * u * u * t * p[1].y + 3 * u * t * t * p[2].y + t * t * t * p[3].y };
  };
  const inside = (pt, n, pad) => pt.x > n.x - pad && pt.x < n.x + n.w + pad && pt.y > n.y - pad && pt.y < n.y + n.h + pad;
  function pathFor(sc, a, b, off) {
    if (a === b) {
      const cx = a.x + a.w / 2, y = a.y;
      return [{ x: cx + a.w * 0.22, y }, { x: cx + a.w * 0.36, y: y - 50 }, { x: cx - a.w * 0.36, y: y - 50 }, { x: cx - a.w * 0.22, y }];
    }
    const ac = { x: a.x + a.w / 2, y: a.y + a.h / 2 }, bc = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
    const dx = bc.x - ac.x, dy = bc.y - ac.y;
    const horiz = Math.abs(dx) - (a.w + b.w) / 2 > Math.abs(dy) - (a.h + b.h) / 2;
    let p0, p3, c;
    if (horiz) {
      const s = Math.sign(dx) || 1;
      p0 = { x: s > 0 ? a.x + a.w : a.x, y: ac.y + off }; p3 = { x: s > 0 ? b.x : b.x + b.w, y: bc.y + off };
      c = Math.max(36, Math.abs(p3.x - p0.x) * 0.45);
      return [p0, { x: p0.x + s * c, y: p0.y }, { x: p3.x - s * c, y: p3.y }, p3];
    }
    const s = Math.sign(dy) || 1;
    p0 = { x: ac.x + off, y: s > 0 ? a.y + a.h : a.y }; p3 = { x: bc.x + off, y: s > 0 ? b.y : b.y + b.h };
    c = Math.max(30, Math.abs(p3.y - p0.y) * 0.45);
    return [p0, { x: p0.x, y: p0.y + s * c }, { x: p3.x, y: p3.y - s * c }, p3];
  }
  function collides(sc, p, a, b) {
    for (let i = 2; i < 29; i++) { const pt = bez(p, i / 30); for (const n of sc.nodes) if (n !== a && n !== b && inside(pt, n, 6)) return n; }
    return null;
  }
  function buildPaths(sc) {
    sc.paths = {};
    const want = new Set();
    sc.steps.forEach((s) => { if (s.kind !== 'focus' && s.kind !== 'note') want.add(s.from + '>' + s.to); });
    sc.edges.forEach((e) => want.add(e.from + '>' + e.to));
    sc.blocked = [];
    want.forEach((key) => {
      const [fa, fb] = key.split('>'); const a = sc.byId[fa], b = sc.byId[fb];
      const both = fa !== fb && want.has(fb + '>' + fa);
      const off = both ? (fa < fb ? -7 : 7) : 0;
      let p = pathFor(sc, a, b, off);
      let hit = a !== b && collides(sc, p, a, b);
      if (hit) {
        const perp = sc.dir === 'LR' ? { x: 0, y: 1 } : { x: 1, y: 0 };
        for (const d of [70, -70, 130, -130, 200, -200]) {
          const q = p.map((pt, i) => (i === 1 || i === 2 ? { x: pt.x + perp.x * d, y: pt.y + perp.y * d } : pt));
          if (!collides(sc, q, a, b)) { p = q; hit = null; break; }
        }
        if (hit) sc.blocked.push(fa + ' → ' + fb + ' passes through ' + hit.id);
      }
      // arc length table
      const pts = []; let L = 0, prev = p[0];
      for (let i = 0; i <= 40; i++) { const pt = bez(p, i / 40); L += Math.hypot(pt.x - prev.x, pt.y - prev.y); pts.push({ pt, L }); prev = pt; }
      sc.paths[key] = { p, len: L, table: pts };
      sc.worldH = Math.max(sc.worldH, ...pts.map((o) => o.pt.y + 30));
      sc.worldW = Math.max(sc.worldW, ...pts.map((o) => o.pt.x + 30));
    });
  }
  function atLen(path, d) {
    const t = path.table; d = clamp(d, 0, path.len);
    for (let i = 1; i < t.length; i++) if (t[i].L >= d) { const k = (d - t[i - 1].L) / Math.max(1e-6, t[i].L - t[i - 1].L); return { x: lerp(t[i - 1].pt.x, t[i].pt.x, k), y: lerp(t[i - 1].pt.y, t[i].pt.y, k), dx: t[i].pt.x - t[i - 1].pt.x, dy: t[i].pt.y - t[i - 1].pt.y }; }
    const a = t[t.length - 2].pt, b = t[t.length - 1].pt; return { x: b.x, y: b.y, dx: b.x - a.x, dy: b.y - a.y };
  }

  /* ------------------------------------------------------------------ derived step state */
  function derive(sc) {
    const stacks = [], errs = [];
    let stack = sc.steps.length ? [sc.steps[0].from] : [];
    const errSet = new Set();
    sc.steps.forEach((s) => {
      // a step that starts from a frame lower in the stack means the frames above it already returned
      if (s.kind !== 'return' && stack.includes(s.from) && stack[stack.length - 1] !== s.from) stack = stack.slice(0, stack.lastIndexOf(s.from) + 1);
      if (s.kind === 'call' || s.kind === 'slow') {
        stack = stack.concat([s.to]);
      } else if (s.kind === 'return') {
        const at = stack.lastIndexOf(s.to);
        stack = at >= 0 ? stack.slice(0, at + 1) : stack.slice(0, -1);
      } else if (s.kind === 'error') {
        errSet.add(s.to);
        if (s.from !== s.to && stack[stack.length - 1] !== s.to) stack = stack.concat([s.to]);
      }
      stacks.push(stack.slice()); errs.push(new Set(errSet));
    });
    sc.stacks = stacks; sc.errs = errs;
    sc.dur = sc.steps.map((s) => (s.kind === 'slow' ? 3.0 : s.kind === 'note' || s.kind === 'focus' ? 2.2 : s.kind === 'error' ? 2.6 : s.kind === 'self' ? 1.8 : 2.0));
  }

  /* ------------------------------------------------------------------ drawing helpers */
  function wob(g, pts, seed, amp, close) {
    g.beginPath();
    pts.forEach((p, i) => {
      const jx = (hash(seed + i * 3.1) - 0.5) * amp, jy = (hash(seed + i * 7.7) - 0.5) * amp;
      i ? g.lineTo(p.x + jx, p.y + jy) : g.moveTo(p.x + jx, p.y + jy);
    });
    if (close) g.closePath();
  }
  function sketchRect(g, x, y, w, h, r, seed) {
    const pts = [];
    const steps = (len) => Math.max(2, Math.round(len / 26));
    const edge = (x0, y0, x1, y1) => { const n = steps(Math.hypot(x1 - x0, y1 - y0)); for (let i = 0; i < n; i++) pts.push({ x: lerp(x0, x1, i / n), y: lerp(y0, y1, i / n) }); };
    const arc = (cx, cy, a0) => { for (let i = 0; i < 3; i++) { const a = a0 + (i / 3) * Math.PI / 2; pts.push({ x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r }); } };
    edge(x + r, y, x + w - r, y); arc(x + w - r, y + r, -Math.PI / 2);
    edge(x + w, y + r, x + w, y + h - r); arc(x + w - r, y + h - r, 0);
    edge(x + w - r, y + h, x + r, y + h); arc(x + r, y + h - r, Math.PI / 2);
    edge(x, y + h - r, x, y + r); arc(x + r, y + r, Math.PI);
    wob(g, pts, seed, 1.3, true);
  }
  function chevron(g, x, y, ang, len, col, lw) {
    g.save(); g.strokeStyle = col; g.lineWidth = lw; g.lineCap = 'round'; g.lineJoin = 'round';
    g.beginPath();
    g.moveTo(x - Math.cos(ang - 0.5) * len, y - Math.sin(ang - 0.5) * len); g.lineTo(x, y);
    g.lineTo(x - Math.cos(ang + 0.5) * len, y - Math.sin(ang + 0.5) * len);
    g.stroke(); g.restore();
  }
  let WORLD_W = 1e9;
  function pill(g, T, s, x, y, col, fill, size) {
    g.font = (size || 11) + 'px ' + FONT;
    const w = g.measureText(s).width + 14, h = (size || 11) + 9;
    x = clamp(x, w / 2 + 4, Math.max(w / 2 + 4, WORLD_W - w / 2 - 4));
    g.fillStyle = fill; g.strokeStyle = col; g.lineWidth = 1.3;
    g.beginPath(); if (g.roundRect) g.roundRect(x - w / 2, y - h / 2, w, h, h / 2); else g.rect(x - w / 2, y - h / 2, w, h);
    g.fill(); g.stroke();
    g.fillStyle = col; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(s, x, y + 0.5);
    return { w, h };
  }
  function burst(g, x, y, r, k, col) {
    g.save(); g.strokeStyle = col; g.lineWidth = 2; g.lineCap = 'round';
    for (let i = 0; i < 9; i++) { const a = (i / 9) * Math.PI * 2 + 0.3; const r0 = r * 0.35 * k, r1 = r * (0.55 + 0.45 * k); g.beginPath(); g.moveTo(x + Math.cos(a) * r0, y + Math.sin(a) * r0); g.lineTo(x + Math.cos(a) * r1, y + Math.sin(a) * r1); g.stroke(); }
    g.restore();
  }
  function envelope(g, T, x, y, w, kind) {
    const h = w * 0.66;
    const fill = kind === 'return' ? T.greenSoft : kind === 'error' ? T.redSoft : kind === 'slow' ? T.orangeSoft : T.butterSoft;
    const ink = kind === 'return' ? T.green : kind === 'error' ? T.red : kind === 'slow' ? T.orange : T.ink;
    g.save(); g.fillStyle = fill; g.strokeStyle = ink; g.lineWidth = 1.4; g.lineJoin = 'round';
    g.beginPath(); g.rect(x - w / 2, y - h / 2, w, h); g.fill(); g.stroke();
    g.beginPath(); g.moveTo(x - w / 2, y - h / 2); g.lineTo(x, y + h * 0.08); g.lineTo(x + w / 2, y - h / 2); g.stroke();
    if (kind === 'return') { g.lineWidth = 1.8; g.beginPath(); g.moveTo(x - 3, y + 2); g.lineTo(x - 0.5, y + 4.5); g.lineTo(x + 4, y - 1); g.stroke(); }
    g.restore();
  }
  function plane(g, T, x, y, ang) {
    g.save(); g.translate(x, y); g.rotate(ang);
    g.fillStyle = T.card; g.strokeStyle = T.blue; g.lineWidth = 1.4; g.lineJoin = 'round';
    g.beginPath(); g.moveTo(11, 0); g.lineTo(-9, -7); g.lineTo(-4, 0); g.lineTo(-9, 7); g.closePath(); g.fill(); g.stroke();
    g.beginPath(); g.moveTo(11, 0); g.lineTo(-4, 0); g.stroke();
    g.restore();
  }
  function clock(g, T, x, y, t) {
    g.save(); g.strokeStyle = T.orange; g.fillStyle = T.card; g.lineWidth = 1.5;
    g.beginPath(); g.arc(x, y, 7, 0, Math.PI * 2); g.fill(); g.stroke();
    g.beginPath(); g.moveTo(x, y); g.lineTo(x + Math.cos(t * 4) * 5, y + Math.sin(t * 4) * 5); g.moveTo(x, y); g.lineTo(x, y - 4); g.stroke();
    g.restore();
  }
  // tiny stick figure. (x,y) = feet. ph = walk phase, dir = ±1, fall = 0..1, carry = arms up.
  function figure(g, T, x, y, o) {
    const s = o.s || 1, ph = o.ph || 0, amp = o.amp == null ? 1 : o.amp, ink = o.ink || T.ink;
    g.save(); g.translate(x, y);
    if (o.fall) g.rotate(-o.dir * o.fall * Math.PI * 0.46);
    g.scale(s * (o.dir || 1), s);
    g.strokeStyle = ink; g.lineWidth = 2.1 / s; g.lineCap = 'round'; g.lineJoin = 'round';
    const bob = Math.abs(Math.sin(ph)) * 1.2 * amp + (o.breathe || 0);
    const hip = { x: 0, y: -15 - bob }, sh = { x: 0.6, y: -27 - bob }, head = { x: 1, y: -33 - bob };
    const leg = (p) => { const sw = Math.sin(p) * amp; const knee = { x: hip.x + sw * 4.2, y: hip.y + 7.5 }; const lift = Math.max(0, Math.cos(p)) * 2.2 * amp; const foot = { x: hip.x + sw * 6.8, y: -lift }; g.beginPath(); g.moveTo(hip.x, hip.y); g.lineTo(knee.x, knee.y); g.lineTo(foot.x, foot.y); g.lineTo(foot.x + 2.2, foot.y); g.stroke(); };
    leg(ph); leg(ph + Math.PI);
    g.beginPath(); g.moveTo(hip.x, hip.y); g.lineTo(sh.x, sh.y); g.stroke();
    g.beginPath(); g.arc(head.x, head.y, 4.6, 0, Math.PI * 2); g.fillStyle = T.card; g.fill(); g.stroke();
    if (o.carry) {
      [-1, 1].forEach((side) => { g.beginPath(); g.moveTo(sh.x, sh.y + 1); g.lineTo(sh.x + side * 5, sh.y - 5); g.lineTo(sh.x + side * 3.6, sh.y - 11.5); g.stroke(); });
    } else {
      [-1, 1].forEach((side) => { const sw = Math.sin(ph + (side > 0 ? 0 : Math.PI)) * amp; g.beginPath(); g.moveTo(sh.x, sh.y + 1); g.lineTo(sh.x + sw * 3 + 1, sh.y + 7); g.lineTo(sh.x + sw * 5 + 2, sh.y + 13); g.stroke(); });
    }
    g.restore();
    return { headY: y + (-33 - bob - 12) * s, carryY: y + (-27 - bob - 16) * s };
  }

  /* ------------------------------------------------------------------ app */
  const CSS = `
  :root{--paper:#fbfaf6;--ink:#141414;--ink2:#55534e;--soft:#9a978f;--faint:#dedad0;--card:#fff;--butter:#ffd84d;--butterSoft:#fff4c7;--red:#d64545;--green:#2f9e5b;--blue:#3866c9;--orange:#d9822b}
  .fs-dark{--paper:#16161a;--ink:#ecebe6;--ink2:#bdbab3;--soft:#7a7871;--faint:#3a3940;--card:#1f1f25;--butter:#f5c84c;--butterSoft:#40381f;--red:#ff6b6b;--green:#4cc38a;--blue:#7aa2ff;--orange:#f0a050}
  html,body{margin:0;background:var(--paper);color:var(--ink)}
  #flow-app{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:1400px;margin:0 auto;padding:14px 18px 40px;box-sizing:border-box}
  .fs-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px 18px;margin-bottom:10px}
  .fs-title{font:700 20px ${FONT};}
  .fs-sub{color:var(--ink2);font-size:13px}
  .fs-tabs{display:flex;gap:6px;flex-wrap:wrap;flex:1 1 auto}
  .fs-tab{border:1.5px solid var(--faint);background:var(--card);color:var(--ink);border-radius:999px;padding:3px 12px;font:13px ${FONT};cursor:pointer}
  .fs-tab[aria-selected=true]{border-color:var(--ink);background:var(--butter);color:#141414}
  .fs-tools{display:flex;gap:6px;margin-left:auto}
  .fs-btn{border:1.5px solid var(--faint);background:var(--card);color:var(--ink);border-radius:8px;padding:4px 10px;font:13px system-ui,sans-serif;cursor:pointer;line-height:1.2}
  .fs-btn:hover{border-color:var(--ink)} .fs-btn:focus-visible,.fs-tab:focus-visible,.fs-chip:focus-visible{outline:2px solid var(--blue);outline-offset:1px}
  .fs-btn[aria-pressed=true]{background:var(--butter);color:#141414;border-color:var(--ink)}
  .fs-stage{position:relative;border:1.5px solid var(--faint);border-radius:14px;background:var(--paper);overflow:hidden}
  .fs-stage canvas{display:block;width:100%;touch-action:none;cursor:grab}
  .fs-zoom{position:absolute;right:10px;top:10px;display:flex;gap:4px}
  .fs-warn{position:absolute;left:10px;bottom:8px;font-size:11px;color:var(--orange);max-width:70%}
  .fs-bar{display:flex;align-items:center;gap:6px;margin:10px 0 6px;flex-wrap:wrap}
  .fs-bar input[type=range]{flex:1 1 200px;accent-color:var(--ink)}
  .fs-count{font:12px ${MONO};color:var(--ink2);min-width:64px;text-align:right}
  .fs-chips{display:flex;gap:4px;overflow-x:auto;padding:2px 0 8px}
  .fs-chip{flex:0 0 auto;min-width:26px;height:22px;border-radius:6px;border:1.5px solid var(--faint);background:var(--card);color:var(--ink);font:11px ${MONO};cursor:pointer;padding:0 5px}
  .fs-chip[data-k=error]{border-color:var(--red);color:var(--red)} .fs-chip[data-k=slow]{border-color:var(--orange);color:var(--orange)}
  .fs-chip[data-k=return]{border-color:var(--green);color:var(--green)} .fs-chip[data-k=async],.fs-chip[data-k=event]{border-color:var(--blue);color:var(--blue);border-style:dashed}
  .fs-chip.on{background:var(--butter);color:#141414;border-color:var(--ink)}
  .fs-info{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:14px}
  @media (max-width:820px){.fs-info{grid-template-columns:1fr}}
  .fs-narr,.fs-side{border:1.5px solid var(--faint);border-radius:12px;background:var(--card);padding:12px 14px}
  .fs-meta{font:12px ${MONO};color:var(--ink2)} .fs-meta b{color:var(--ink)}
  .fs-label{font:600 17px ${FONT};margin:4px 0 6px}
  .fs-detail{color:var(--ink);white-space:pre-wrap}
  .fs-code{font:12px ${MONO};background:var(--paper);border:1px solid var(--faint);border-radius:8px;padding:6px 8px;margin-top:8px;white-space:pre;overflow-x:auto}
  .fs-code a{color:var(--blue)}
  .fs-side h4{margin:0 0 6px;font:600 12px system-ui,sans-serif;text-transform:uppercase;letter-spacing:.06em;color:var(--ink2)}
  .fs-stack{list-style:none;margin:0 0 12px;padding:0;font:12px ${MONO}}
  .fs-stack li{padding:3px 6px;border-left:3px solid var(--faint);margin:2px 0}
  .fs-stack li.top{border-color:var(--ink);background:var(--butterSoft)} .fs-stack li.err{border-color:var(--red);color:var(--red)}
  .fs-legend{list-style:none;margin:0;padding:0;font-size:12px;display:flex;flex-wrap:wrap;gap:4px 10px}
  .fs-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
  .fs-pop{position:absolute;z-index:5;max-width:330px;background:var(--card);border:1.5px solid var(--ink);border-radius:12px;padding:10px 12px;box-shadow:0 6px 24px rgba(0,0,0,.14);font-size:13px}
  .fs-pop h5{margin:0 0 2px;font:700 15px ${FONT}} .fs-pop ul{margin:6px 0 0;padding-left:16px;font:12px ${MONO};max-height:160px;overflow:auto}
  .fs-pop .fs-steps button{margin:2px 3px 0 0}
  .fs-help{font-size:11px;color:var(--soft);margin-top:6px}
  `;

  function el(tag, attrs, html) { const e = document.createElement(tag); if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]); if (html != null) e.innerHTML = html; return e; }

  function App(root, specs) {
    const store = (k, v) => { try { if (v === undefined) return localStorage.getItem('flowscene.' + k); localStorage.setItem('flowscene.' + k, v); } catch (e) { return null; } };
    const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    const opts = specs[0] || {};
    let themeName = store('theme') || opts.theme || 'light';
    let courier = store('courier') || opts.courier || 'stick';
    const scenes = specs.map(normalize);
    let cur = 0, sc = null, step = 0, u = 0, playing = false, stopAtEnd = false, speed = 1, loop = false, t = 0, last = 0;
    let view = { k: 1, ox: 0, oy: 0 }, dpr = 1, cssW = 0, cssH = 0;
    root.appendChild(el('style', null, CSS));
    const head = el('div', { class: 'fs-head' });
    const title = el('div', { class: 'fs-title' }), sub = el('div', { class: 'fs-sub' });
    const tabs = el('nav', { class: 'fs-tabs', role: 'tablist' });
    const tools = el('div', { class: 'fs-tools' });
    const bCourier = el('button', { class: 'fs-btn', title: 'Courier: stick figure or dot' });
    const bTheme = el('button', { class: 'fs-btn', title: 'Light / dark' }, 'theme');
    const bPng = el('button', { class: 'fs-btn', title: 'Download this frame as PNG' }, 'PNG');
    const bRec = el('button', { class: 'fs-btn', title: 'Record the whole flow as a WebM video' }, '● rec');
    tools.append(bCourier, bTheme, bPng, bRec);
    const tbox = el('div'); tbox.append(title, sub);
    head.append(tbox, tabs, tools);
    const stage = el('div', { class: 'fs-stage' });
    const cv = el('canvas', { role: 'img' }); const g = cv.getContext('2d');
    const zoom = el('div', { class: 'fs-zoom' });
    const zIn = el('button', { class: 'fs-btn', title: 'Zoom in (+)' }, '+'), zOut = el('button', { class: 'fs-btn', title: 'Zoom out (−)' }, '−'), zFit = el('button', { class: 'fs-btn', title: 'Fit (F)' }, 'fit');
    zoom.append(zIn, zOut, zFit);
    const warn = el('div', { class: 'fs-warn' });
    const pop = el('div', { class: 'fs-pop', hidden: '' });
    stage.append(cv, zoom, warn, pop);
    const bar = el('div', { class: 'fs-bar' });
    const bFirst = el('button', { class: 'fs-btn', title: 'First step (Home)' }, '⏮'), bPrev = el('button', { class: 'fs-btn', title: 'Previous step (←)' }, '◀');
    const bPlay = el('button', { class: 'fs-btn', title: 'Play / pause (Space)' }, '▶'), bNext = el('button', { class: 'fs-btn', title: 'Next step (→)' }, '▶▶'), bLast = el('button', { class: 'fs-btn', title: 'Last step (End)' }, '⏭');
    const scrub = el('input', { type: 'range', min: '0', max: '1000', value: '0', 'aria-label': 'Timeline' });
    const count = el('span', { class: 'fs-count' });
    const sp = el('select', { class: 'fs-btn', 'aria-label': 'Speed' }, ['0.5', '1', '1.5', '2', '3'].map((v) => `<option value="${v}"${v === '1' ? ' selected' : ''}>${v}×</option>`).join(''));
    const bLoop = el('button', { class: 'fs-btn', 'aria-pressed': 'false', title: 'Loop' }, '⟲');
    bar.append(bFirst, bPrev, bPlay, bNext, bLast, scrub, count, sp, bLoop);
    const chips = el('div', { class: 'fs-chips' });
    const info = el('div', { class: 'fs-info' });
    const narr = el('section', { class: 'fs-narr', 'aria-live': 'polite' });
    const side = el('aside', { class: 'fs-side' });
    info.append(narr, side);
    root.append(head, stage, bar, chips, info);

    const measure = (s, size, bold) => { g.font = (bold ? '600 ' : '') + size + 'px ' + FONT; return g.measureText(s || '').width; };
    const T = () => THEMES[themeName] || THEMES.light;

    function setTheme(n) { themeName = n; store('theme', n); root.classList.toggle('fs-dark', n === 'dark'); document.documentElement.classList.toggle('fs-dark', n === 'dark'); }
    function setCourier(c) { courier = c; store('courier', c); bCourier.textContent = c === 'stick' ? '🚶 stick' : '● dot'; }

    function resize() {
      dpr = window.devicePixelRatio || 1;
      cssW = stage.clientWidth;
      const fitK = Math.min(cssW / sc.worldW, 1.25);
      cssH = Math.round(clamp(sc.worldH * fitK, 240, Math.max(320, window.innerHeight * 0.64)));
      cv.style.height = cssH + 'px';
      cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
      fit();
    }
    function fit() {
      const k = Math.min(cssW / sc.worldW, cssH / sc.worldH, 1.25);
      view = { k, ox: (cssW - sc.worldW * k) / 2, oy: (cssH - sc.worldH * k) / 2 };
    }
    function zoomAt(f, cx, cy) {
      const k = clamp(view.k * f, 0.2, 4);
      cx = cx == null ? cssW / 2 : cx; cy = cy == null ? cssH / 2 : cy;
      view.ox = cx - (cx - view.ox) * (k / view.k); view.oy = cy - (cy - view.oy) * (k / view.k); view.k = k;
    }
    const toWorld = (cx, cy) => ({ x: (cx - view.ox) / view.k, y: (cy - view.oy) / view.k });

    function loadScene(i, keepStep) {
      cur = i; sc = scenes[i];
      if (!sc.laid) { layout(sc, measure); derive(sc); sc.laid = true; }
      step = keepStep ? clamp(step, 0, Math.max(0, sc.steps.length - 1)) : 0; u = 0; playing = false;
      title.textContent = sc.title; sub.textContent = sc.subtitle || '';
      cv.setAttribute('aria-label', sc.label || sc.title);
      [...tabs.children].forEach((b, j) => b.setAttribute('aria-selected', j === i ? 'true' : 'false'));
      chips.innerHTML = '';
      sc.steps.forEach((s, j) => { const c = el('button', { class: 'fs-chip', 'data-k': s.kind, title: (j + 1) + '. ' + s.kind + (s.label ? ': ' + s.label : '') }, String(j + 1)); c.onclick = () => { seek(j, 0); play(); }; chips.appendChild(c); });
      const ws = sc.warnings.concat(sc.blocked || []);
      warn.textContent = ws.length && /[?&]debug/.test(location.search) ? '⚠ ' + ws.slice(0, 3).join(' · ') : '';
      bar.style.display = chips.style.display = sc.steps.length ? '' : 'none';
      hidePop(); resize(); panel();
      try { history.replaceState(null, '', '#' + encodeURIComponent(sc.id) + (step ? ':' + (step + 1) : '')); } catch (e) {}
      if (sc.steps.length && (sc.autoplay !== false) && !reduce && !keepStep) setTimeout(() => { if (sc === scenes[cur] && step === 0 && u === 0) play(); }, 700);
      if (reduce) u = 1;
    }
    function seek(i, uu) { if (!sc.steps.length) return; step = clamp(i, 0, sc.steps.length - 1); u = clamp(uu == null ? 0 : uu, 0, 1); if (reduce) u = 1; panel(); }
    function play() { if (!sc.steps.length) return; if (step >= sc.steps.length - 1 && u >= 1) { step = 0; u = 0; } stopAtEnd = false; playing = true; bPlay.textContent = '⏸'; }
    function pause() { playing = false; bPlay.textContent = '▶'; }
    function next() { pause(); if (u < 1) u = 1; else seek(step + 1, reduce ? 1 : 0); if (!reduce && u === 0) { playing = true; bPlay.textContent = '⏸'; stopAtEnd = true; } panel(); }
    function prev() { pause(); seek(u > 0.05 && u < 1 ? step : step - 1, 1); }

    function codeLink(ref) {
      const m = /^(.*?):(\d+)$/.exec(ref || '');
      if (!sc.editor || !sc.root || !m || !m[1].includes('/')) return esc(ref);
      const abs = (sc.root.replace(/\/$/, '') + '/' + m[1].replace(/^\.?\//, ''));
      const href = sc.editor === 'idea' ? `idea://open?file=${encodeURIComponent(abs)}&line=${m[2]}` : sc.editor === 'cursor' ? `cursor://file${abs}:${m[2]}` : `vscode://file${abs}:${m[2]}`;
      return `<a href="${esc(href)}">${esc(ref)}</a>`;
    }
    let lastPanel = '';
    function panel() {
      const s = sc.steps[step];
      const key = cur + ':' + step;
      [...chips.children].forEach((c, j) => c.classList.toggle('on', j === step));
      count.textContent = sc.steps.length ? step + 1 + ' / ' + sc.steps.length : '';
      scrub.value = sc.steps.length ? Math.round(((step + u) / sc.steps.length) * 1000) : 0;
      if (key === lastPanel) return; lastPanel = key;
      if (!s) {
        narr.innerHTML = `<div class="fs-label">${esc(sc.title)}</div><div class="fs-detail">${esc(sc.description || 'Click a box to see what is inside it.')}</div><div class="fs-help">Drag to pan · ⌘/Ctrl + scroll to zoom · click a box for details</div>`;
      } else {
        const fromL = sc.byId[s.from].label, toL = sc.byId[s.to].label;
        const route = s.from === s.to ? `<b>${esc(fromL)}</b>` : `<b>${esc(fromL)}</b> → <b>${esc(toL)}</b>`;
        const codes = [].concat(s.code || []).map(codeLink).join('\n');
        const payload = s.payload == null ? '' : typeof s.payload === 'string' ? s.payload : JSON.stringify(s.payload, null, 2);
        narr.innerHTML = `<div class="fs-meta">step ${step + 1}/${sc.steps.length} · <span style="color:${KIND_COLOR(T(), s.kind)}">${s.kind}</span> · ${route}${s.ms != null ? ' · <b>' + esc(s.ms) + ' ms</b>' : ''}</div>
          <div class="fs-label">${esc(s.label || (s.kind === 'return' ? 'returns' : ''))}</div>
          ${s.detail ? `<div class="fs-detail">${esc(s.detail)}</div>` : ''}
          ${codes ? `<div class="fs-code">${codes}</div>` : ''}
          ${s.snippet ? `<div class="fs-code">${esc(s.snippet)}</div>` : ''}
          ${payload ? `<div class="fs-code">${esc(payload)}</div>` : ''}
          <div class="fs-help">Space play/pause · ← → step · click a box for details</div>`;
      }
      const stk = (sc.stacks && sc.stacks[step]) || [];
      const errs = (sc.errs && sc.errs[step]) || new Set();
      const showStack = sc.steps.some((x) => x.kind !== 'focus' && x.kind !== 'note');
      side.innerHTML = (showStack ? `<h4>Call stack</h4><ol class="fs-stack">${stk.slice().reverse().map((id, j) => `<li class="${j === 0 ? 'top' : ''} ${errs.has(id) ? 'err' : ''}">${esc(sc.byId[id].label)}</li>`).join('') || '<li>—</li>'}</ol>` : '') +
        (sc.groups.length ? `<h4>Modules</h4><ul class="fs-legend">${sc.groups.map((gr) => `<li><i style="background:${gr.color}"></i>${esc(gr.label || gr.id)}</li>`).join('')}</ul>` : '');
      try { history.replaceState(null, '', '#' + encodeURIComponent(sc.id) + (sc.steps.length ? ':' + (step + 1) : '')); } catch (e) {}
    }

    /* ---------------- frame */
    function frame(now) {
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 0; last = now; t += dt;
      if (playing && sc.steps.length) {
        u += (dt * speed) / sc.dur[step];
        if (u >= 1) {
          if (stopAtEnd) { u = 1; stopAtEnd = false; pause(); }
          else if (step < sc.steps.length - 1) { step++; u = 0; }
          else if (loop) { step = 0; u = 0; }
          else { u = 1; pause(); }
          panel();
        } else if (Math.random() < 0.2) panel();
      }
      draw();
      requestAnimationFrame(frame);
    }

    function nodeShape(n, T, fill, stroke, lw, seed) {
      g.lineWidth = lw; g.strokeStyle = stroke; g.fillStyle = fill;
      if (n.kind === 'db' || n.kind === 'cache') {
        const ry = 7; const x = n.x, y = n.y + ry, w = n.w, h = n.h - ry * 2;
        g.beginPath(); g.ellipse(x + w / 2, y + h, w / 2, ry, 0, 0, Math.PI); g.lineTo(x, y); g.ellipse(x + w / 2, y, w / 2, ry, 0, Math.PI, 0, true); g.closePath(); g.fill(); g.stroke();
        g.beginPath(); g.ellipse(x + w / 2, y, w / 2, ry, 0, 0, Math.PI * 2); g.stroke();
        return;
      }
      sketchRect(g, n.x, n.y, n.w, n.h, 9, seed);
      g.fill();
      if (n.kind === 'external' || n.kind === 'framework' || n.auto) g.setLineDash([5, 4]);
      g.stroke(); g.setLineDash([]);
      if (n.kind === 'queue' || n.kind === 'topic') { for (let i = 1; i <= 3; i++) { g.beginPath(); g.moveTo(n.x + n.w - 6 * i, n.y + 6); g.lineTo(n.x + n.w - 6 * i, n.y + n.h - 6); g.stroke(); } }
    }

    function draw() {
      const Th = T();
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.fillStyle = Th.paper; g.fillRect(0, 0, cssW, cssH);
      // dot grid
      g.fillStyle = Th.grid;
      const gs = 16 * view.k; if (gs > 5) { const x0 = view.ox % gs, y0 = view.oy % gs; for (let x = x0; x < cssW; x += gs) for (let y = y0; y < cssH; y += gs) g.fillRect(x, y, 1.2, 1.2); }
      g.setTransform(dpr * view.k, 0, 0, dpr * view.k, dpr * view.ox, dpr * view.oy);
      const s = sc.steps[step];
      const has = !!s;
      const stk = has ? (u >= 0.85 ? sc.stacks[step] : step ? sc.stacks[step - 1] : [s.from]) : [];
      const errs = has ? (u >= 0.75 ? sc.errs[step] : step ? sc.errs[step - 1] : new Set()) : new Set();
      // focus (map tours): dim unrelated
      const focus = has && (s.kind === 'focus') ? s.from : null;
      const rel = (a, b) => !focus || a === focus || b === focus;
      const visited = new Set(); if (has) for (let j = 0; j < step + (u >= 1 ? 1 : 0); j++) visited.add(sc.steps[j].from + '>' + sc.steps[j].to);
      const curKey = has ? s.from + '>' + s.to : null;
      // base edges
      g.lineCap = 'round';
      for (const key in sc.paths) {
        const P = sc.paths[key]; const [a, b] = key.split('>');
        const e = sc.edges.find((x) => x.from === a && x.to === b);
        const kinds = sc.steps.filter((x) => x.from === a && x.to === b).map((x) => x.kind);
        const dashed = kinds.length && kinds.every((k) => k === 'return' || k === 'async' || k === 'event');
        g.globalAlpha = rel(a, b) ? 1 : 0.18;
        let col = visited.has(key) ? Th.ink2 : Th.faint, lw = e ? clamp(1 + Math.log2(e.weight || 1) * 0.9, 1.2, 5) : 1.6;
        if (focus && a === focus) { col = Th.orange; lw += 0.6; } else if (focus && b === focus) { col = Th.blue; lw += 0.6; }
        if (key === curKey && s.kind !== 'focus') { col = KIND_COLOR(Th, s.kind); }
        if (key === curKey && s.kind !== 'focus' && s.kind !== 'note') { // butter halo
          g.save(); g.strokeStyle = Th.butter; g.globalAlpha = 0.55; g.lineWidth = lw + 7; g.beginPath(); g.moveTo(P.p[0].x, P.p[0].y); g.bezierCurveTo(P.p[1].x, P.p[1].y, P.p[2].x, P.p[2].y, P.p[3].x, P.p[3].y); g.stroke(); g.restore();
        }
        g.strokeStyle = col; g.lineWidth = lw;
        if (dashed) g.setLineDash(kinds.every((k) => k === 'return') ? [7, 5] : [2, 5]);
        g.beginPath(); g.moveTo(P.p[0].x, P.p[0].y); g.bezierCurveTo(P.p[1].x, P.p[1].y, P.p[2].x, P.p[2].y, P.p[3].x, P.p[3].y); g.stroke(); g.setLineDash([]);
        const end = atLen(P, P.len - 1); chevron(g, P.p[3].x, P.p[3].y, Math.atan2(end.dy, end.dx), 7 + lw, col, Math.max(1.6, lw * 0.9));
        // map particles
        if (e && !reduce && sc.mode === 'map' && rel(a, b)) {
          const n = clamp(Math.round(Math.log2((e.weight || 1) + 1)), 1, 3);
          g.fillStyle = focus ? col : Th.soft;
          for (let j = 0; j < n; j++) { const d = ((t * 40 + (j * P.len) / n + strHash(key)) % P.len); const q = atLen(P, d); g.beginPath(); g.arc(q.x, q.y, 2.2, 0, Math.PI * 2); g.fill(); }
        }
        g.globalAlpha = 1;
      }
      // nodes
      const top = stk[stk.length - 1];
      for (const n of sc.nodes) {
        let shake = 0;
        const isErr = errs.has(n.id);
        if (has && s.kind === 'error' && s.to === n.id && u > 0.72 && u < 0.95) shake = Math.sin(t * 60) * 2.5 * (1 - seg(u, 0.8, 0.95));
        const related = !focus || n.id === focus || sc.edges.some((e) => (e.from === focus && e.to === n.id) || (e.to === focus && e.from === n.id));
        g.globalAlpha = related ? 1 : 0.25;
        g.save(); g.translate(shake, 0);
        let fill = Th.card, stroke = Th.ink, lw = 1.6;
        if (stk.includes(n.id)) fill = Th.butterSoft;
        if (n.id === top) { fill = Th.butter; lw = 2.2; }
        if (focus === n.id) { fill = Th.butter; lw = 2.4; }
        if (isErr) { fill = Th.redSoft; stroke = Th.red; lw = 2.4; }
        if (n.kind === 'framework' || n.auto) stroke = isErr ? Th.red : Th.soft;
        nodeShape(n, Th, fill, stroke, lw, strHash(n.id));
        if (n.gcolor) { g.fillStyle = n.gcolor; g.beginPath(); if (g.roundRect) g.roundRect(n.x + 7, n.y + 6, 5, n.h - 12, 2.5); else g.rect(n.x + 7, n.y + 6, 5, n.h - 12); g.fill(); }
        const dark = fill === Th.butter;
        g.fillStyle = dark ? '#141414' : isErr ? Th.red : Th.ink; g.textAlign = 'center'; g.textBaseline = 'middle';
        const cx = n.x + n.w / 2 + (n.gcolor ? 4 : 0), cy = n.y + n.h / 2 + (n.kind === 'db' || n.kind === 'cache' ? 4 : 0);
        g.font = '600 13px ' + FONT; g.fillText(n.label, cx, n.sub ? cy - 7 : cy);
        if (n.sub) { g.font = '10px ' + FONT; g.fillStyle = dark ? '#55534e' : Th.ink2; g.fillText(trunc(n.sub, 40), cx, cy + 9); }
        if (n.kind === 'client' || n.kind === 'user') figure(g, Th, n.x + 14, n.y + n.h - 7, { s: 0.55, amp: 0, ink: dark ? '#141414' : Th.ink });
        if (n.href) { g.fillStyle = dark ? '#141414' : Th.soft; g.font = '11px ' + FONT; g.fillText('↗', n.x + n.w - 9, n.y + 9); }
        if (n.badge) pill(g, Th, String(n.badge), n.x + n.w - 6, n.y - 2, Th.ink2, Th.card, 9);
        g.restore(); g.globalAlpha = 1;
      }
      WORLD_W = sc.worldW;
      if (has) drawStep(s, Th);
      g.setTransform(1, 0, 0, 1, 0, 0);
    }

    function tagY(text, x, y, size) {
      g.font = (size || 11) + 'px ' + FONT;
      const w = g.measureText(text).width + 14, h = (size || 11) + 9;
      for (let k = 0; k < 6; k++) {
        const hit = sc.nodes.find((n) => x + w / 2 > n.x - 2 && x - w / 2 < n.x + n.w + 2 && y + h / 2 > n.y - 2 && y - h / 2 < n.y + n.h + 2);
        if (!hit) break;
        y = hit.y - h / 2 - 5;
      }
      return y;
    }
    function drawStep(s, Th) {
      const key = s.from + '>' + s.to, P = sc.paths[key];
      const a = sc.byId[s.from], b = sc.byId[s.to];
      if (s.kind === 'note' || s.kind === 'focus') {
        const k = E.back(seg(u, 0, 0.25));
        if (s.label && s.kind === 'note') { g.save(); g.globalAlpha = seg(u, 0, 0.15); const y = tagY(trunc(s.label, 46), a.x + a.w / 2, a.y - 22 - 6 * k, 12); pill(g, Th, trunc(s.label, 46), a.x + a.w / 2, y, Th.ink, Th.card, 12); g.beginPath(); g.fillStyle = Th.card; g.strokeStyle = Th.ink; g.lineWidth = 1.2; g.arc(a.x + a.w / 2 - 6, y + 15, 2.6, 0, 7); g.fill(); g.stroke(); g.restore(); }
        return;
      }
      const tripAt = 0.8;
      const walk = s.kind === 'error' ? E.soft(seg(u, 0.06, 0.7)) * tripAt : E.soft(seg(u, 0.06, 0.86));
      const d = walk * P.len, q = atLen(P, d);
      const dir = q.dx >= 0 ? 1 : -1;
      if (s.kind === 'async' || s.kind === 'event') {
        const lift = Math.sin(walk * Math.PI) * 26;
        const ang = Math.atan2(q.dy - Math.cos(walk * Math.PI) * 6, q.dx);
        plane(g, Th, q.x, q.y - lift, ang);
        if (s.label) pill(g, Th, trunc(s.label, 34), q.x, q.y - lift - 18, Th.blue, Th.card, 11);
        if (u > 0.86) { const k = seg(u, 0.86, 1); burst(g, P.p[3].x, P.p[3].y, 12, k, Th.blue); }
        return;
      }
      let fall = 0;
      if (s.kind === 'error') fall = E.out3(seg(u, 0.7, 0.8));
      const phase = d / 5.5;
      const moving = u > 0.06 && u < (s.kind === 'error' ? 0.7 : 0.86);
      const breathe = moving ? 0 : Math.sin(t * 2.4) * 0.5;
      let headY;
      if (courier === 'stick') {
        const r = figure(g, Th, q.x, q.y + 1, { s: 1.1, ph: phase, amp: moving ? 1 : 0, dir, carry: !fall, fall, breathe, ink: s.kind === 'error' && fall ? Th.red : Th.ink });
        headY = r.headY;
        if (!fall) envelope(g, Th, q.x + dir * 0.5, r.carryY, 20, s.kind);
      } else {
        g.fillStyle = KIND_COLOR(Th, s.kind); g.beginPath(); g.arc(q.x, q.y, 6, 0, Math.PI * 2); g.fill();
        headY = q.y - 12;
      }
      if (s.kind === 'error') {
        const k = seg(u, 0.72, 0.9);
        if (k > 0) {
          // envelope flies off and hits the node
          const ex = lerp(q.x, P.p[3].x, E.out3(k)), ey = lerp(q.y - 30, P.p[3].y, E.out3(k)) - Math.sin(k * Math.PI) * 20;
          if (k < 1) envelope(g, Th, ex, ey, 16, 'error');
          burst(g, P.p[3].x, P.p[3].y, 16, E.back(k), Th.red);
        }
        if (u > 0.78) { const txt = trunc(s.label || 'error', 46); pill(g, Th, txt, b.x + b.w / 2, tagY(txt, b.x + b.w / 2, b.y - 16, 12), Th.red, Th.redSoft, 12); }
        else if (s.label) { const txt = trunc(s.label, 40); pill(g, Th, txt, q.x, tagY(txt, q.x, Math.min(headY - 6, b.y - 16), 11), Th.red, Th.card, 11); }
        return;
      }
      const col = KIND_COLOR(Th, s.kind);
      if (s.label) { const txt = trunc(s.label + (s.ms != null ? '  ' + s.ms + 'ms' : ''), 40); pill(g, Th, txt, q.x, tagY(txt, q.x, headY - (courier === 'stick' ? 10 : 2), 11), col, Th.card, 11); }
      if (s.kind === 'slow') clock(g, Th, q.x + 18 * dir, headY + 10, t);
      if (u > 0.86 && a !== b) { const k = seg(u, 0.86, 1); g.save(); g.globalAlpha = 1 - k; g.strokeStyle = col; g.lineWidth = 2; g.beginPath(); if (g.roundRect) g.roundRect(b.x - 6 * k, b.y - 6 * k, b.w + 12 * k, b.h + 12 * k, 12); else g.rect(b.x, b.y, b.w, b.h); g.stroke(); g.restore(); }
    }

    /* ---------------- popover */
    function showPop(n, cx, cy) {
      const steps = sc.steps.filter((s) => s.from === n.id || s.to === n.id);
      const outs = sc.edges.filter((e) => e.from === n.id), ins = sc.edges.filter((e) => e.to === n.id);
      pop.innerHTML = `<h5>${esc(n.label)}</h5><div class="fs-meta">${esc([n.kind, n.group].filter(Boolean).join(' · '))}</div>
        ${n.note ? `<div style="margin-top:6px">${esc(n.note)}</div>` : ''}
        ${n.files && n.files.length ? `<ul>${n.files.slice(0, 12).map((f) => `<li>${codeLink(f)}</li>`).join('')}${n.files.length > 12 ? `<li>… ${n.files.length - 12} more</li>` : ''}</ul>` : ''}
        ${outs.length ? `<div class="fs-meta" style="margin-top:6px">uses → ${outs.map((e) => esc(sc.byId[e.to].label) + (e.weight > 1 ? ' (' + e.weight + ')' : '')).join(', ')}</div>` : ''}
        ${ins.length ? `<div class="fs-meta">used by ← ${ins.map((e) => esc(sc.byId[e.from].label)).join(', ')}</div>` : ''}
        ${steps.length ? `<div class="fs-steps" style="margin-top:6px">${steps.map((s) => `<button class="fs-chip" data-k="${s.kind}" data-i="${s.i}" title="${esc(s.label)}">${s.i + 1}</button>`).join('')}</div>` : ''}
        ${n.href ? `<div style="margin-top:8px"><button class="fs-btn" data-href="${esc(n.href)}">Open ${esc(n.href.replace(/^#/, ''))} ↗</button></div>` : ''}`;
      pop.hidden = false;
      const pw = Math.min(330, pop.offsetWidth || 300);
      pop.style.left = clamp(cx + 12, 6, cssW - pw - 6) + 'px';
      pop.style.top = clamp(cy + 12, 6, Math.max(6, cssH - pop.offsetHeight - 6)) + 'px';
      pop.querySelectorAll('[data-i]').forEach((b) => (b.onclick = () => { seek(+b.dataset.i, 0); play(); hidePop(); }));
      pop.querySelectorAll('[data-href]').forEach((b) => (b.onclick = () => openHref(b.dataset.href)));
    }
    function hidePop() { pop.hidden = true; }
    function openHref(h) {
      const id = h.replace(/^#/, ''); const i = scenes.findIndex((x) => x.id === id);
      if (i >= 0) loadScene(i); else window.open(h, '_blank', 'noopener');
    }

    /* ---------------- events */
    let drag = null;
    cv.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy, moved: false }; cv.setPointerCapture(e.pointerId); });
    cv.addEventListener('pointermove', (e) => {
      if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; view.ox = drag.ox + dx; view.oy = drag.oy + dy; cv.style.cursor = 'grabbing'; return; }
      const r = cv.getBoundingClientRect(); const w = toWorld(e.clientX - r.left, e.clientY - r.top);
      cv.style.cursor = sc.nodes.some((n) => inside(w, n, 0)) ? 'pointer' : 'grab';
    });
    cv.addEventListener('pointerup', (e) => {
      const d = drag; drag = null; cv.style.cursor = 'grab';
      if (d && !d.moved) {
        const r = cv.getBoundingClientRect(); const cx = e.clientX - r.left, cy = e.clientY - r.top; const w = toWorld(cx, cy);
        const n = sc.nodes.find((n) => inside(w, n, 0));
        if (n) showPop(n, cx, cy); else hidePop();
      }
    });
    cv.addEventListener('dblclick', (e) => { const r = cv.getBoundingClientRect(); const w = toWorld(e.clientX - r.left, e.clientY - r.top); const n = sc.nodes.find((n) => inside(w, n, 0)); if (n && n.href) openHref(n.href); else if (!n) fit(); });
    cv.addEventListener('wheel', (e) => { if (!(e.ctrlKey || e.metaKey)) return; e.preventDefault(); const r = cv.getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
    zIn.onclick = () => zoomAt(1.2); zOut.onclick = () => zoomAt(1 / 1.2); zFit.onclick = fit;
    bPlay.onclick = () => (playing ? pause() : play());
    bNext.onclick = next; bPrev.onclick = prev;
    bFirst.onclick = () => { pause(); seek(0, 0); }; bLast.onclick = () => { pause(); seek(sc.steps.length - 1, 1); };
    scrub.oninput = () => { pause(); const v = (scrub.value / 1000) * sc.steps.length; seek(Math.min(sc.steps.length - 1, Math.floor(v)), v >= sc.steps.length ? 1 : v - Math.floor(v)); };
    sp.onchange = () => (speed = +sp.value);
    bLoop.onclick = () => { loop = !loop; bLoop.setAttribute('aria-pressed', String(loop)); };
    bCourier.onclick = () => setCourier(courier === 'stick' ? 'dot' : 'stick');
    bTheme.onclick = () => setTheme(themeName === 'dark' ? 'light' : 'dark');
    bPng.onclick = () => { const a = document.createElement('a'); a.download = sc.id + (sc.steps.length ? '-step' + (step + 1) : '') + '.png'; a.href = cv.toDataURL('image/png'); a.click(); };
    let rec = null;
    bRec.onclick = () => {
      if (rec) { rec.stop(); return; }
      if (!cv.captureStream || !window.MediaRecorder) { alert('This browser cannot record a canvas.'); return; }
      const chunks = []; const mr = new MediaRecorder(cv.captureStream(30), { mimeType: MediaRecorder.isTypeSupported('video/webm;codecs=vp9') ? 'video/webm;codecs=vp9' : 'video/webm' });
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      mr.onstop = () => { rec = null; bRec.textContent = '● rec'; const a = document.createElement('a'); a.download = sc.id + '.webm'; a.href = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' })); a.click(); };
      rec = mr; bRec.textContent = '■ stop'; loop = false; bLoop.setAttribute('aria-pressed', 'false');
      seek(0, 0); mr.start(); play();
      const iv = setInterval(() => { if (!rec) return clearInterval(iv); if (!playing) { clearInterval(iv); setTimeout(() => rec && rec.stop(), 900); } }, 200);
    };
    window.addEventListener('keydown', (e) => {
      if (/input|select|textarea/i.test(e.target.tagName)) return;
      if (e.key === ' ') { e.preventDefault(); playing ? pause() : play(); }
      else if (e.key === 'ArrowRight') next(); else if (e.key === 'ArrowLeft') prev();
      else if (e.key === 'Home') bFirst.onclick(); else if (e.key === 'End') bLast.onclick();
      else if (e.key === '+' || e.key === '=') zoomAt(1.2); else if (e.key === '-') zoomAt(1 / 1.2);
      else if (e.key === 'f') fit(); else if (e.key === 'Escape') hidePop();
    });
    window.addEventListener('resize', () => resize());

    scenes.forEach((s, i) => { const b = el('button', { class: 'fs-tab', role: 'tab' }, esc(s.tab || s.title)); b.onclick = () => loadScene(i); tabs.appendChild(b); });
    if (scenes.length < 2) tabs.style.display = 'none';
    setTheme(themeName); setCourier(courier);
    // deep link  #sceneId:step
    const m = /^#([^:]+)(?::(\d+))?$/.exec(decodeURIComponent(location.hash || ''));
    const start = m ? Math.max(0, scenes.findIndex((s) => s.id === m[1])) : 0;
    loadScene(start);
    if (m && m[2]) { seek(+m[2] - 1, 1); }
    requestAnimationFrame(frame);

    window.FLOW = {
      ready: true,
      scenes: scenes.map((s) => s.id),
      tab: (i) => loadScene(typeof i === 'number' ? i : scenes.findIndex((s) => s.id === i)),
      seek: (i, uu) => { pause(); seek(i, uu); draw(); },
      play, pause,
      state: () => ({ scene: sc.id, step, u, playing }),
      check: () => {
        const issues = [];
        sc.nodes.forEach((a, i) => sc.nodes.slice(i + 1).forEach((b) => { if (a.x < b.x + b.w + 4 && b.x < a.x + a.w + 4 && a.y < b.y + b.h + 4 && b.y < a.y + a.h + 4) issues.push({ level: 'error', msg: 'nodes overlap: ' + a.id + ' / ' + b.id }); }));
        (sc.blocked || []).forEach((m) => issues.push({ level: 'warn', msg: 'edge ' + m + ' (set layer/row on nodes)' }));
        sc.warnings.forEach((m) => issues.push({ level: 'warn', msg: m }));
        sc.steps.forEach((s, i) => { if ((s.label || '').length > 38) issues.push({ level: 'warn', msg: 'step ' + (i + 1) + ' label is ' + s.label.length + ' chars; canvas shows ~36. Move the rest to detail.' }); });
        const aspect = sc.worldW / sc.worldH;
        if (aspect > 6) issues.push({ level: 'warn', msg: 'very wide (' + aspect.toFixed(1) + ':1). Try direction "TB" or split the flow.' });
        if (view.k < 0.45) issues.push({ level: 'warn', msg: 'scene renders at ' + Math.round(view.k * 100) + '% at this width: too big. Split it.' });
        return { scene: sc.id, nodes: sc.nodes.length, steps: sc.steps.length, dir: sc.dir, scale: +view.k.toFixed(2), issues };
      },
    };
  }

  function boot() {
    const root = document.getElementById('flow-app') || document.body.appendChild(el('div', { id: 'flow-app' }));
    const specs = [].concat(window.FLOW_SCENES || []);
    if (!specs.length) { root.textContent = 'No scenes: set window.FLOW_SCENES.'; return; }
    try { App(root, specs); } catch (e) { root.innerHTML = '<pre style="color:#d64545">flowscene error: ' + esc(e.stack || e) + '</pre>'; throw e; }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
