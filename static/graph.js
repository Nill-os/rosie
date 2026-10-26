'use strict';
// board #824 owner-memory-graph-visual
const GCOLORS = ['#3EE6E0', '#D4FF4F', '#FF5FA2', '#FFB04F', '#A98BFF', '#7CFFB0', '#FF6B5A', '#4F8BFF', '#FFE45C', '#00F0FF', '#E07CFF', '#9CFF6B'];
const GMISC = '#8696AE';
const ekey = (a, b) => (a < b ? a + '|' + b : b + '|' + a);
const GSRC = [['corner', "Rosie's corner"], ['board', 'Agent board'], ['mind', "Rosie's mind"]];
let G = null;

VIEWS.graph = async () => {
  setHeader('Memory graph', []);
  const keep = (k, d) => { try { const v = localStorage.getItem('graph.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } };
  const save = (k, v) => { try { localStorage.setItem('graph.' + k, JSON.stringify(v)); } catch (e) { } };
  if (G && G.raf) cancelAnimationFrame(G.raf);
  G = { source: keep('source', 'corner'), mode: keep('mode', 'clusters'), q: '', limit: keep('limit', 150), hidden: keep('hidden', ['Rosie', 'web page']), old: false, clusters: [], multi: [], pathNodes: new Set(), pathEdges: new Set(),
        nodes: [], edges: [], byId: new Map(), sel: null, hover: null, view: { x: 0, y: 0, k: 1 }, alpha: 0, raf: 0, drag: null, counts: {} };

  const canvas = h('canvas', { class: 'gcanvas', 'aria-label': 'Memory graph map. Click a dot to see what Rosie knows about it.' });
  const side = h('aside', { class: 'gside', hidden: true });
  const info = h('div', { class: 'ginfo muted' }, 'Loading…');
  const search = h('input', { type: 'search', placeholder: 'Find a name, then Enter', 'aria-label': 'Find in the graph' });
  const seg = (items, cur, pick) => h('div', { class: 'seg' }, items.map(([v, label]) => h('button', { class: v === cur ? 'on' : '', onclick: () => pick(v) }, label)));
  const srcSeg = h('div');
  const drawSrc = () => srcSeg.replaceChildren(seg(GSRC.map(([v, l]) => [v, G.counts[v] == null ? l : `${l} · ${G.counts[v]}`]), G.source, (v) => {
    G.source = v; save('source', v); G.q = ''; search.value = ''; drawSrc(); load();
  }));
  const modeSeg = h('div');
  const drawMode = () => modeSeg.replaceChildren(seg([['clusters', 'Clusters'], ['free', 'Free']], G.mode, (v) => { G.mode = v; save('mode', v); drawMode(); arrange(); }));
  const sizeSeg = h('div');
  const drawSize = () => sizeSeg.replaceChildren(seg([[80, '80'], [150, '150'], [300, '300'], [500, '500']], G.limit, (v) => { G.limit = v; save('limit', v); drawSize(); load(); }));
  const tog = (label, key, title) => h('label', { class: 'gtog', title }, h('input', { type: 'checkbox', checked: G[key], onchange: (e) => { G[key] = e.target.checked; load(); } }), label);
  const hiddenBox = h('div', { class: 'ghidden' });
  const drawHidden = () => hiddenBox.replaceChildren(...(G.hidden.length ? [h('span', { class: 'muted' }, 'Hidden:'), ...G.hidden.map((n) =>
    h('button', { class: 'chip', title: 'Show it again', onclick: () => { G.hidden = G.hidden.filter((x) => x !== n); save('hidden', G.hidden); drawHidden(); load(); } }, n + ' ✕'))] : []));
  const hideName = (name) => { if (!G.hidden.includes(name)) { G.hidden = [...G.hidden, name]; save('hidden', G.hidden); drawHidden(); closeSide(); load(); } };
  search.onkeydown = (e) => { if (e.key === 'Enter') { G.q = search.value.trim(); load(); } };
  const bar = h('div', { class: 'card gbar' }, srcSeg, h('div', { class: 'grow2' }, search, btn('Find', '', () => { G.q = search.value.trim(); load(); }),
    btn('All', '', () => { search.value = ''; G.q = ''; load(); })),
    h('div', { class: 'gopts' }, modeSeg, h('span', { class: 'muted' }, 'Show'), sizeSeg, tog('Old facts', 'old', 'Also facts that were replaced or expired')), hiddenBox,
    h('div', { class: 'gzoom' }, btn('−', '', () => zoomAt(0.8)), btn('+', '', () => zoomAt(1.25)), btn('Fit', '', fit)));
  const stage = h('div', { class: 'gstage' }, canvas, info, side);
  show(h('div', { class: 'gpage' }, bar, stage));
  drawSrc(); drawSize(); drawHidden(); drawMode();
  J('/api/graph/counts').then((c) => { if (G) { G.counts = c || {}; drawSrc(); } }).catch(() => {});

  const ctx = canvas.getContext('2d');
  let W = 0, H = 0, dpr = 1;
  const resize = () => {
    dpr = window.devicePixelRatio || 1;
    W = stage.clientWidth; H = stage.clientHeight;
    canvas.width = Math.max(1, W * dpr); canvas.height = Math.max(1, H * dpr);
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    paint();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(stage);
  const here = () => location.hash.replace(/^#\/?/, '').startsWith('graph');

  async function load() {
    info.textContent = 'Loading…'; info.hidden = false;
    const p = new URLSearchParams({ source: G.source, q: G.q, limit: G.limit, hide: G.hidden.join('|'), old: G.old ? 'true' : 'false' });
    let d;
    try { d = await J('/api/graph/view?' + p); } catch (e) { info.textContent = 'Could not load the graph: ' + e.message; return; }
    if (!G || !here()) return;
    if (d.error) { info.textContent = d.error; G.nodes = []; G.edges = []; paint(); return; }
    const old = G.byId;
    G.nodes = d.nodes.map((n) => { const o = old.get(n.id); return { ...n, x: o ? o.x : (Math.random() - 0.5) * 600, y: o ? o.y : (Math.random() - 0.5) * 600, vx: 0, vy: 0,
      r: Math.min(20, 3 + Math.sqrt(n.degree) * 1.15) }; });
    G.byId = new Map(G.nodes.map((n) => [n.id, n]));
    G.edges = d.edges.filter((e) => G.byId.has(e.source) && G.byId.has(e.target)).map((e) => ({ ...e, a: G.byId.get(e.source), b: G.byId.get(e.target) }));
    G.adj = new Map(G.nodes.map((n) => [n.id, new Set()]));
    for (const e of G.edges) { G.adj.get(e.source).add(e.target); G.adj.get(e.target).add(e.source); }
    groups();
    const ql = G.q.toLowerCase();
    G.match = ql ? new Set(G.nodes.filter((n) => n.name.toLowerCase().includes(ql)).map((n) => n.id)) : new Set();
    G.labels = new Set([...G.nodes].sort((a, b) => b.degree - a.degree).slice(0, 24).map((n) => n.id));
    info.textContent = G.nodes.length ? `${G.nodes.length} of ${d.total} things · ${G.edges.length} links${G.q ? ` · around “${G.q}”` : ''}. Drag to move, scroll to zoom, click a dot for what Rosie knows, Ctrl+click several to see how they connect.`
      : (G.q ? `Nothing named “${G.q}”.` : 'This graph is empty so far.');
    if (G.sel && !G.byId.has(G.sel)) closeSide();
    if (G.multi.length) { G.multi = []; G.pathNodes = new Set(); G.pathEdges = new Set(); side.hidden = true; }
    arrange();
  }

  function arrange() {
    if (G.mode === 'clusters') { layoutClusters(); G.alpha = 0; fit(); G.refit = true; paint(); return; }
    G.alpha = 1;
    for (let i = 0; i < 260 && G.alpha > 0.05; i++) step();
    fit(); G.refit = false; G.moved = false;
    kick();
  }

  function layoutClusters() {
    const big = G.clusters.filter((c) => c.nodes.length > 1 && !c.other), misc = G.clusters.filter((c) => c.nodes.length === 1 || c.other).flatMap((c) => c.nodes);
    const sorted = big.map((c) => ({ ...c, nodes: [...c.nodes] }));
    const list = [];  // board #824 why-alternate-big-small-rings
    while (sorted.length) { list.push(sorted.shift()); if (sorted.length) list.push(sorted.pop()); }
    if (misc.length) list.push({ color: GMISC, nodes: misc, misc: true, title: G.nodes.some((n) => n.group) ? 'Other topics' : 'Loose ends' });
    const gap = 90, spacing = 17;
    for (const c of list) {
      c.nodes.sort((a, b) => b.degree - a.degree);
      const rest = c.misc ? c.nodes : c.nodes.slice(1);
      c.rings = [];
      let r = c.misc ? 0 : 30, i = 0;
      while (i < rest.length) {
        r = r ? r + 20 : 22;
        const cap = Math.max(6, Math.floor(2 * Math.PI * r / spacing));
        c.rings.push({ r, nodes: rest.slice(i, i + cap) });
        i += cap;
      }
      c.r = (c.rings.length ? c.rings[c.rings.length - 1].r : 14) + 12;
      c.nodes.forEach((nd, i) => { nd.outer = i < 5 || c.nodes.length <= 6; });
    }
    const around = list.reduce((s, c) => s + 2 * c.r + gap, 0);
    const R = Math.max(200, around / (2 * Math.PI) * 1.12);  // board #824 why-rings-never-touch
    let walk = 0;
    for (const c of list) {
      const t = (walk + c.r + gap / 2) / around * Math.PI * 2 - Math.PI / 2;
      walk += 2 * c.r + gap;
      c.cx = Math.cos(t) * R; c.cy = Math.sin(t) * R;
      if (!c.misc) { const hub = c.nodes[0]; hub.x = c.cx; hub.y = c.cy; hub.hub = true; }
      c.rings.forEach((ring, k) => ring.nodes.forEach((n, j) => {
        const a = (j / ring.nodes.length) * Math.PI * 2 + k * 0.35 + t;
        n.x = c.cx + Math.cos(a) * ring.r; n.y = c.cy + Math.sin(a) * ring.r; n.hub = false;
      }));
      for (const n of c.nodes) { n.color = c.color; n.cluster = c; }
      const second = c.nodes[1];
      if (second && second.degree >= Math.max(4, c.nodes[0].degree * 0.5)) second.hub = true;
    }
    G.ring = list;
    G.R = R;
  }

  function groups() {
    if (G.nodes.some((n) => n.group)) return topicGroups();
    return linkGroups();
  }
  function topicGroups() {
    const by = new Map();
    for (const n of G.nodes) { const k = n.group || ''; if (!by.has(k)) by.set(k, { title: n.group_title || 'Other', nodes: [] }); by.get(k).nodes.push(n); }
    const big = [...by.entries()].filter(([k, c]) => k && c.nodes.length > 1).sort((a, b) => b[1].nodes.length - a[1].nodes.length);
    const rest = [...by.entries()].filter(([k, c]) => !k || c.nodes.length === 1).flatMap(([, c]) => c.nodes);
    G.clusters = big.map(([, c], i) => ({ title: c.title, color: GCOLORS[i % GCOLORS.length], nodes: c.nodes }));
    if (rest.length) G.clusters.push({ title: 'Other topics', color: GMISC, nodes: rest, other: true });
    for (const c of G.clusters) for (const n of c.nodes) n.color = c.color;
  }
  function linkGroups() {
    const idx = new Map(G.nodes.map((n, i) => [n.id, i]));
    let n = G.nodes.length, adj = Array.from({ length: n }, () => new Map());
    for (const e of G.edges) {
      const a = idx.get(e.source), b = idx.get(e.target);
      if (a === b) continue;
      adj[a].set(b, (adj[a].get(b) || 0) + 1); adj[b].set(a, (adj[b].get(a) || 0) + 1);
    }
    let seed = 7;  // board #824 why-fixed-seed-groups
    const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    let member = G.nodes.map((_, i) => i);
    for (let level = 0; level < 6; level++) {
      const deg = adj.map((m) => [...m.values()].reduce((s, w) => s + w, 0)), m2 = deg.reduce((s, d) => s + d, 0) || 1;
      const comm = adj.map((_, i) => i), tot = [...deg];
      let moved = false;
      for (let pass = 0; pass < 12; pass++) {
        let any = false;
        const order = [...comm.keys()];
        for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
        for (const i of order) {
          const ci = comm[i], links = new Map();
          for (const [j, w] of adj[i]) if (j !== i) links.set(comm[j], (links.get(comm[j]) || 0) + w);
          tot[ci] -= deg[i];
          let best = ci, gain = (links.get(ci) || 0) - tot[ci] * deg[i] / m2;
          for (const [c, w] of links) { const g = w - tot[c] * deg[i] / m2; if (g > gain + 1e-9) { gain = g; best = c; } }
          tot[best] += deg[i];
          if (best !== ci) { comm[i] = best; any = true; moved = true; }
        }
        if (!any) break;
      }
      if (!moved) break;
      const ids = [...new Set(comm)], re = new Map(ids.map((c, k) => [c, k]));
      const next = Array.from({ length: ids.length }, () => new Map());
      adj.forEach((m, i) => { for (const [j, w] of m) { const a = re.get(comm[i]), b = re.get(comm[j]); next[a].set(b, (next[a].get(b) || 0) + w); } });
      member = member.map((c) => re.get(comm[c]));
      adj = next; n = ids.length;
    }
    const size = new Map();
    for (const c of member) size.set(c, (size.get(c) || 0) + 1);
    const rank = [...size.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
    const lab = new Map(G.nodes.map((nd, i) => [nd.id, member[i]]));
    for (const nd of G.nodes) { const r = rank.indexOf(lab.get(nd.id)); nd.color = r < GCOLORS.length && size.get(lab.get(nd.id)) > 1 ? GCOLORS[r] : GMISC; }
    G.clusters = rank.map((l, r) => ({ color: r < GCOLORS.length ? GCOLORS[r] : GMISC, nodes: G.nodes.filter((nd) => lab.get(nd.id) === l) }));
    for (const c of G.clusters) c.title = [...c.nodes].sort((a, b) => b.degree - a.degree)[0].name;
    if (G.clusters.length > GCOLORS.length) {
      const tail = G.clusters.splice(GCOLORS.length);
      G.clusters.push(...tail.flatMap((c) => c.nodes.map((nd) => ({ color: GMISC, nodes: [nd] }))));
    }
  }

  function step() {
    const N = G.nodes, a = G.alpha, rep = 900, len = 70;
    for (let i = 0; i < N.length; i++) {
      const p = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const q = N[j]; let dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy;
        if (d2 > 160000) continue;  // board #824 why-skip-far-push
        if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.5; }
        const f = rep * a / d2;
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      }
    }
    for (const e of G.edges) {
      const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - len) / d * 0.04 * a;
      e.a.vx += dx * f; e.a.vy += dy * f; e.b.vx -= dx * f; e.b.vy -= dy * f;
    }
    for (const p of N) {
      p.vx -= p.x * 0.004 * a; p.vy -= p.y * 0.004 * a;
      if (G.drag && G.drag.node === p) { p.vx = p.vy = 0; continue; }
      p.vx *= 0.6; p.vy *= 0.6;
      p.x += Math.max(-30, Math.min(30, p.vx)); p.y += Math.max(-30, Math.min(30, p.vy));
    }
    G.alpha *= 0.985;
  }
  function kick() { if (!G.raf) G.raf = requestAnimationFrame(frame); }
  function frame() {
    G.raf = 0;
    if (!G || !here()) { ro.disconnect(); return; }
    if (G.mode === 'free' && (G.alpha > 0.02 || G.drag)) { for (let i = 0; i < 2; i++) step(); paint(); kick(); }
    else { if (!G.moved && !G.refit) { G.refit = true; fit(); } paint(); }
  }

  const toScreen = (p) => [(p.x * G.view.k + G.view.x) + W / 2, (p.y * G.view.k + G.view.y) + H / 2];
  const toWorld = (sx, sy) => [(sx - W / 2 - G.view.x) / G.view.k, (sy - H / 2 - G.view.y) / G.view.k];
  function paint() {
    if (!G) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (G.mode === 'clusters' && G.ring) return paintClusters();
    const L = lit();
    ctx.lineWidth = 1;
    for (const e of G.edges) {
      const on = L.on && L.edge(e);
      if (L.on && !on) { ctx.strokeStyle = 'rgba(255,255,255,0.04)'; } else ctx.strokeStyle = on ? 'rgba(0,240,255,0.75)' : 'rgba(150,170,200,0.16)';
      const [x1, y1] = toScreen(e.a), [x2, y2] = toScreen(e.b);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    for (const n of G.nodes) {
      const [x, y] = toScreen(n), r = Math.max(2, n.r * Math.sqrt(G.view.k));
      if (x < -30 || y < -30 || x > W + 30 || y > H + 30) continue;
      const dim = L.on && !L.nodes.has(n.id);
      ctx.globalAlpha = dim ? 0.18 : 1;
      ctx.fillStyle = n.color;
      ctx.shadowColor = n.color; ctx.shadowBlur = dim ? 0 : (L.main.has(n.id) || G.match.has(n.id) ? 18 : 7);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      if (n.id === G.sel || G.multi.includes(n.id) || G.match.has(n.id)) { ctx.shadowBlur = 0; ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r + 3, 0, Math.PI * 2); ctx.stroke(); ctx.lineWidth = 1; }
    }
    ctx.shadowBlur = 0; ctx.globalAlpha = 1;
    ctx.font = '11px ui-monospace, "JetBrains Mono", "DejaVu Sans Mono", monospace';
    ctx.textAlign = 'center';
    for (const n of G.nodes) {
      const show = L.nodes.has(n.id) || G.match.has(n.id) || (!L.on && (G.labels.has(n.id) || G.view.k > 1.6 && n.degree > 2));
      if (!show) continue;
      const [x, y] = toScreen(n), r = Math.max(2, n.r * Math.sqrt(G.view.k));
      if (x < -60 || y < -20 || x > W + 60 || y > H + 20) continue;
      const t = n.name.length > 28 ? n.name.slice(0, 27) + '…' : n.name;
      ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(4,7,12,0.9)'; ctx.strokeText(t, x, y - r - 5);
      ctx.fillStyle = L.main.has(n.id) ? '#00F0FF' : '#E8E8F0'; ctx.fillText(t, x, y - r - 5);
    }
    ctx.lineWidth = 1;
  }
  function lit() {
    if (G.multi.length) return { on: true, main: new Set(G.multi), nodes: G.pathNodes, edge: (e) => G.pathEdges.has(ekey(e.source, e.target)) };
    const f = G.sel || G.hover;
    if (!f) return { on: false, main: new Set(), nodes: new Set(), edge: () => false };
    return { on: true, main: new Set([f]), nodes: new Set([f, ...(G.adj.get(f) || [])]), edge: (e) => e.source === f || e.target === f };
  }
  function diamond(x, y, r) { ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath(); }
  function paintClusters() {
    const k = G.view.k, L = lit();
    const [ox, oy] = toScreen({ x: 0, y: 0 });
    for (const c of G.ring) {
      const [cx, cy] = toScreen({ x: c.cx, y: c.cy });
      ctx.globalAlpha = L.on ? 0.25 : 0.55;
      ctx.strokeStyle = c.color; ctx.lineWidth = 1.2; ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.arc(cx, cy, c.r * k, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.globalAlpha = L.on ? 0.35 : 1;
    ctx.textAlign = 'center';
    ctx.font = `bold ${Math.round(Math.min(18, 12.5 * Math.max(1, Math.sqrt(k))))}px ui-monospace, "JetBrains Mono", "DejaVu Sans Mono", monospace`;
    for (const c of G.ring) {
      if (!c.title) continue;
      const [cx, cy] = toScreen({ x: c.cx, y: c.cy }), t = `${c.title.length > 26 ? c.title.slice(0, 25) + '…' : c.title} · ${c.nodes.length}`;
      ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(4,7,12,0.92)'; ctx.strokeText(t, cx, cy - c.r * k - 8);
      ctx.fillStyle = c.color; ctx.fillText(t, cx, cy - c.r * k - 8);
    }
    ctx.globalAlpha = 1; ctx.lineWidth = 1;
    ctx.globalCompositeOperation = 'lighter';
    for (const e of G.edges) {
      const on = L.on && L.edge(e);
      if (L.on && !on) continue;
      const [x1, y1] = toScreen(e.a), [x2, y2] = toScreen(e.b), same = e.a.cluster === e.b.cluster;
      ctx.lineWidth = on ? 1.6 : 1;
      if (same) {
        ctx.globalAlpha = on ? 0.9 : 0.32; ctx.strokeStyle = e.a.color;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      } else {
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, qx = mx + (ox - mx) * 0.55, qy = my + (oy - my) * 0.55;
        const g = ctx.createLinearGradient(x1, y1, x2, y2);
        g.addColorStop(0, e.a.color); g.addColorStop(1, e.b.color);
        ctx.globalAlpha = on ? 0.85 : 0.13; ctx.strokeStyle = g;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(qx, qy, x2, y2); ctx.stroke();
      }
    }
    ctx.globalCompositeOperation = 'source-over';
    const zr = Math.max(0.7, Math.sqrt(k));
    for (const n of G.nodes) {
      const [x, y] = toScreen(n);
      if (x < -30 || y < -30 || x > W + 30 || y > H + 30) continue;
      const dim = L.on && !L.nodes.has(n.id);
      ctx.globalAlpha = dim ? 0.15 : 1;
      ctx.fillStyle = n.color; ctx.shadowColor = n.color; ctx.shadowBlur = dim ? 0 : (L.main.has(n.id) || G.match.has(n.id) ? 16 : 6);
      if (n.hub) {
        const r = 7 * zr; diamond(x, y, r); ctx.fill();
        ctx.shadowBlur = 0; ctx.fillStyle = 'rgba(255,255,255,0.75)'; diamond(x, y, r * 0.42); ctx.fill();
      } else {
        const r = Math.min(5, 2.2 + Math.sqrt(n.degree) * 0.5) * zr;
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
        if (n.degree >= 6 && !dim) {
          ctx.shadowBlur = 0; ctx.strokeStyle = n.color; ctx.globalAlpha = 0.6; ctx.setLineDash([1.5, 2.5]);
          ctx.beginPath(); ctx.arc(x, y, r + 5 * zr, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
        }
      }
      if (n.id === G.sel || G.multi.includes(n.id) || G.match.has(n.id)) { ctx.shadowBlur = 0; ctx.globalAlpha = 1; ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 10 * zr, 0, Math.PI * 2); ctx.stroke(); ctx.lineWidth = 1; }
    }
    ctx.shadowBlur = 0; ctx.globalAlpha = 1;
    const many = G.nodes.length > 220 && k < 1.4;
    ctx.font = `${Math.round(Math.min(14, 10.5 * Math.max(1, Math.sqrt(k))))}px ui-monospace, "JetBrains Mono", "DejaVu Sans Mono", monospace`;
    for (const n of G.nodes) {
      const shown = L.nodes.has(n.id) || G.match.has(n.id);
      if (L.on ? !shown : !(n.hub || (n.outer && !many) || k > 1.5 || (many && G.labels.has(n.id)))) continue;
      const [x, y] = toScreen(n);
      if (x < -80 || y < -20 || x > W + 80 || y > H + 20) continue;
      const c = n.cluster, right = c ? x >= toScreen({ x: c.cx, y: c.cy })[0] - 0.5 : true;
      const t = n.name.length > 24 ? n.name.slice(0, 23) + '…' : n.name;
      ctx.textAlign = n.hub ? 'center' : right ? 'left' : 'right';
      const tx = n.hub ? x : x + (right ? 8 : -8), ty = n.hub ? y - 11 * zr : y + 4;
      ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(4,7,12,0.92)'; ctx.strokeText(t, tx, ty);
      ctx.fillStyle = L.main.has(n.id) ? '#ffffff' : n.color; ctx.fillText(t, tx, ty);
    }
    ctx.textAlign = 'center'; ctx.lineWidth = 1;
  }
  function fit() {
    if (!G.nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of G.nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const k = Math.min(3, Math.max(0.15, Math.min((W - 60) / ((x1 - x0) || 1), (H - 60) / ((y1 - y0) || 1))));
    G.view = { k, x: -(x0 + x1) / 2 * k, y: -(y0 + y1) / 2 * k };
    paint();
  }
  function zoomAt(f, sx = W / 2, sy = H / 2) {
    const [wx, wy] = toWorld(sx, sy);
    G.view.k = Math.min(6, Math.max(0.08, G.view.k * f));
    G.view.x = sx - W / 2 - wx * G.view.k; G.view.y = sy - H / 2 - wy * G.view.k;
    paint();
  }
  function pick(sx, sy) {
    let best = null, bd = Infinity;
    for (const n of G.nodes) {
      const [x, y] = toScreen(n), r = Math.max(6, n.r * Math.sqrt(G.view.k) + 4), d = (x - sx) ** 2 + (y - sy) ** 2;
      if (d < r * r && d < bd) { best = n; bd = d; }
    }
    return best;
  }

  const pos = (e) => { const b = canvas.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  canvas.onpointerdown = (e) => {
    const [sx, sy] = pos(e), n = pick(sx, sy);
    canvas.setPointerCapture(e.pointerId);
    G.drag = { node: n, sx, sy, vx: G.view.x, vy: G.view.y, moved: false };
    if (n) { G.alpha = Math.max(G.alpha, 0.15); kick(); }
  };
  canvas.onpointermove = (e) => {
    const [sx, sy] = pos(e);
    if (!G.drag) {
      const n = pick(sx, sy), id = n ? n.id : null;
      canvas.style.cursor = n ? 'pointer' : 'grab';
      if (id !== G.hover) { G.hover = id; canvas.title = n ? n.name : ''; paint(); }
      return;
    }
    if (Math.abs(sx - G.drag.sx) + Math.abs(sy - G.drag.sy) > 3) G.drag.moved = true;
    if (G.drag.node) { const [wx, wy] = toWorld(sx, sy); G.drag.node.x = wx; G.drag.node.y = wy; G.alpha = Math.max(G.alpha, 0.12); kick(); }
    else { G.view.x = G.drag.vx + sx - G.drag.sx; G.view.y = G.drag.vy + sy - G.drag.sy; G.moved = true; canvas.style.cursor = 'grabbing'; paint(); }
  };
  canvas.onpointerup = (e) => {
    const d = G.drag; G.drag = null;
    if (d && !d.moved) {
      if (d.node && (e.ctrlKey || e.metaKey)) toggleMulti(d.node.id);
      else { clearMulti(); if (d.node) select(d.node.id); else closeSide(); }
    }
  };
  canvas.ondblclick = (e) => { const n = pick(...pos(e)); if (n) { search.value = n.name; G.q = n.name; load(); } };
  canvas.onwheel = (e) => { e.preventDefault(); G.moved = true; zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, ...pos(e)); };

  function closeSide() { G.sel = null; side.hidden = true; paint(); }

  function clearMulti() { if (G.multi.length) { G.multi = []; G.pathNodes = new Set(); G.pathEdges = new Set(); side.hidden = true; paint(); } }
  function chain(a, b) {
    const prev = new Map([[a, null]]), todo = [a];
    while (todo.length) {
      const x = todo.shift();
      if (x === b) break;
      for (const y of G.adj.get(x) || []) if (!prev.has(y)) { prev.set(y, x); todo.push(y); }
    }
    if (!prev.has(b)) return null;
    const out = [b];
    while (out[0] !== a) out.unshift(prev.get(out[0]));
    return out;
  }
  function toggleMulti(id) {
    if (G.sel && !G.multi.length) G.multi = [G.sel];
    G.sel = null;
    G.multi = G.multi.includes(id) ? G.multi.filter((x) => x !== id) : [...G.multi, id];
    connect();
  }
  async function connect() {
    const ids = G.multi, name = (id) => (G.byId.get(id) || { name: '?' }).name;
    const steps = [];
    G.pathNodes = new Set(ids); G.pathEdges = new Set();
    for (let i = 0; i + 1 < ids.length; i++) {
      const c = chain(ids[i], ids[i + 1]);
      steps.push({ a: ids[i], b: ids[i + 1], chain: c });
      if (c) for (let j = 0; j + 1 < c.length; j++) { G.pathNodes.add(c[j]); G.pathNodes.add(c[j + 1]); G.pathEdges.add(ekey(c[j], c[j + 1])); }
    }
    paint();
    if (!ids.length) { side.hidden = true; return; }
    side.hidden = false;
    const head = h('div', { class: 'gsh' }, h('b', {}, ids.length > 1 ? 'How they are connected' : name(ids[0])), btn('✕', 'sm', clearMulti));
    const chips = h('div', { class: 'gpick' }, ids.map((id) => h('span', { class: 'chip', style: `border-color:${(G.byId.get(id) || {}).color || '#8696AE'}` }, name(id))));
    if (ids.length < 2) { side.replaceChildren(head, chips, h('p', { class: 'muted' }, 'Ctrl+click another dot to see how they are connected.')); return; }
    side.replaceChildren(head, chips, h('div', { class: 'muted' }, 'Loading…'));
    const hops = [...new Set(steps.filter((s) => s.chain).flatMap((s) => s.chain.slice(1).map((x, j) => ekey(s.chain[j], x))))];
    let facts = {};
    if (hops.length) {
      try { facts = (await J(`/api/graph/links?source=${encodeURIComponent(G.source)}&pairs=${encodeURIComponent(hops.map((k) => k.replace('|', '~')).join('|'))}`)).links || {}; }
      catch (e) { facts = {}; }
    }
    if (!G || G.multi !== ids) return;
    const linked = steps.filter((s) => s.chain).length;
    side.replaceChildren(head, chips,
      h('p', { class: 'gsum' }, linked === steps.length ? (steps.every((s) => s.chain.length === 2) ? 'They are linked directly.' : 'They are linked through other things on the map.')
        : linked ? 'Some of them are connected; the rest are not linked on this map.' : 'These are not connected on this map. Try Show 300 or 500, or Old facts, to see more links.'),
      ...steps.map((s) => h('div', { class: 'gstep' },
        h('div', { class: 'gchain' }, s.chain ? s.chain.map((id, j) => [j ? h('span', { class: 'arrow' }, '→') : null, h('span', { class: 'cn', style: `color:${(G.byId.get(id) || {}).color}` }, name(id))])
          : [h('span', { class: 'cn' }, name(s.a)), h('span', { class: 'arrow muted' }, ' · not connected · '), h('span', { class: 'cn' }, name(s.b))]),
        s.chain ? s.chain.slice(1).map((x, j) => {
          const fs = facts[ekey(s.chain[j], x).replace('|', '~')] || [];
          return h('div', { class: 'ghop' }, h('div', { class: 'muted', style: 'font-size:calc(11px*var(--ts,1))' }, `${name(s.chain[j])} — ${name(x)}`),
            fs.length ? fs.map((f) => h('div', { class: 'gfact' + (f.invalid ? ' old' : '') }, f.fact, h('div', { class: 'gmeta' }, f.name ? h('span', { class: 'chip' }, f.name) : null,
              f.valid ? h('span', {}, 'since ' + f.valid) : null, f.invalid ? h('span', { class: 'bad' }, 'ended ' + f.invalid) : null)))
              : h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, 'Linked, but no fact text was saved for this link.'));
        }) : null)));
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && G && G.multi.length && here()) clearMulti(); });
  async function select(id) {
    G.sel = id; paint();
    const n = G.byId.get(id);
    side.hidden = false;
    side.replaceChildren(h('div', { class: 'gsh' }, h('b', {}, n ? n.name : ''), btn('✕', 'sm', closeSide)), h('div', { class: 'muted' }, 'Loading…'));
    let d;
    try { d = await J(`/api/graph/node?source=${encodeURIComponent(G.source)}&id=${encodeURIComponent(id)}`); } catch (e) { d = { error: e.message }; }
    if (!G || G.sel !== id) return;
    if (d.error) { side.replaceChildren(h('div', { class: 'gsh' }, h('b', {}, n ? n.name : ''), btn('✕', 'sm', closeSide)), h('div', { class: 'err' }, d.error)); return; }
    const goTo = (oid) => { const o = G.byId.get(oid); if (o) { G.view.x = -o.x * G.view.k; G.view.y = -o.y * G.view.k; select(oid); } };
    side.replaceChildren(
      h('div', { class: 'gsh' }, h('b', {}, d.name), btn('✕', 'sm', closeSide)),
      d.summary ? h('p', { class: 'gsum' + (d.summary.length > 360 ? ' clip' : ''), title: 'Click to show all', onclick: (e) => e.currentTarget.classList.remove('clip') }, d.summary) : null,
      h('div', { class: 'row', style: 'gap:6px;margin:6px 0 10px;flex-wrap:wrap' }, btn('Show around it', 'sm', () => { search.value = d.name; G.q = d.name; load(); }),
        btn('Center', 'sm', () => goTo(id)), btn('Hide from map', 'sm', () => hideName(d.name))),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-bottom:6px' }, `${d.facts.length} ${G.source === 'mind' ? 'memories' : 'facts'}`),
      h('div', { class: 'gfacts' }, d.facts.map((f) => h('div', { class: 'gfact' + (f.invalid ? ' old' : '') },
        h('div', {}, f.fact),
        h('div', { class: 'gmeta' }, f.name ? h('span', { class: 'chip' }, f.name) : null,
          f.other ? (f.other_id && G.byId.has(f.other_id) ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); goTo(f.other_id); } }, '→ ' + f.other) : h('span', {}, '→ ' + f.other)) : null,
          f.valid ? h('span', {}, 'since ' + f.valid) : null, f.invalid ? h('span', { class: 'bad' }, 'ended ' + f.invalid) : null),
        (f.sources || []).filter((s) => /^https?:\/\//.test(s.url)).map((s) => h('a', { class: 'gsrc', href: s.url, target: '_blank', rel: 'noopener noreferrer', title: s.what }, '↗ ' + s.url.replace(/^https?:\/\//, '').slice(0, 60)))))));
  }

  resize();
  await load();
};
