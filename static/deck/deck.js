'use strict';
// Agent Deck page: polls the snapshot and draws the agents, the board, the system and every process. All text goes in through
// textContent (h()), never as HTML, because process names, board posts and commands are data from other programs.
// Two homes: inside Rosie (/deck/, her login cookie, /api/deck/*) and the desktop window (~/agent-deck: a per-launch token, /api/*).
const TOKEN = new URLSearchParams(location.hash.slice(1)).get('t') || '';
const BASE = TOKEN ? '/api/' : '/api/deck/';
if (window.self !== window.top) document.documentElement.classList.add('framed');   // inside Rosie: her header already names the page
const $ = (s) => document.querySelector(s);
const store = {
  get(k, d) { try { const v = localStorage.getItem('deck.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('deck.' + k, JSON.stringify(v)); } catch { /* private window: fine */ } },
};

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'checked' || k === 'value' || k === 'open') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : String(c));
  return el;
}

// ---------------------------------------------------------------- formatting
const KB = 1024, MB = KB * 1024, GB = MB * 1024;
const bytes = (b) => b == null ? '–' : b >= GB ? (b / GB).toFixed(1) + ' GB' : b >= MB ? (b / MB).toFixed(b >= 100 * MB ? 0 : 1) + ' MB' : b >= KB ? (b / KB).toFixed(0) + ' KB' : Math.round(b) + ' B';
const rate = (b) => b == null ? '–' : bytes(b) + '/s';
const pct = (x) => x == null ? '–' : (Math.round(x * 10) / 10) + '%';
const num = (n) => n == null ? '–' : n >= 1e9 ? (n / 1e9).toFixed(2) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n);
const dur = (s) => { s = Math.max(0, Math.round(s)); const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${hh}h ${m}m` : hh ? `${hh}h ${m}m ${s % 60}s` : m ? `${m}m ${s % 60}s` : `${s}s`; };
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const ago = (t) => dur(Date.now() / 1000 - t) + ' ago';
const level = (p) => p >= 85 ? 'hi' : p >= 60 ? 'mid' : '';
const bar = (p, cls = '') => h('div', { class: 'bar ' + cls }, h('i', { class: level(p), style: `width:${Math.min(100, Math.max(0, p || 0))}%` }));
const cpuOf = (g) => g.cores >= 0.95 ? `${pct(g.cpu)} · ${g.cores.toFixed(1)} cores` : pct(g.cpu);   // % of the whole CPU, plus cores when it matters
const tokTotal = (t) => (t.input || 0) + (t.output || 0) + (t.cache_write || 0) + (t.cache_read || 0);

// ---------------------------------------------------------------- API
async function api(path, body) {
  const headers = TOKEN ? { 'X-Deck-Token': TOKEN } : {};
  const r = await fetch(BASE + path, body ? { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { headers });
  if (r.status === 401) throw new Error('Log in to Rosie first.');
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
function toast(msg, err) {
  const d = h('div', { class: err ? 'err' : '' }, msg);
  $('#toast').append(d);
  setTimeout(() => d.remove(), err ? 7000 : 3500);
}
async function act(body, confirmText) {
  if (confirmText && !(await ask(confirmText))) return;
  try {
    const r = await api('action', body);
    if (r.error) throw new Error(r.error);
    toast(r.msg || 'Done.');
  } catch (e) { toast(e.message, true); }
  tick(true);
}

// ---------------------------------------------------------------- modal
function modal(...content) {
  $('#modalbody').replaceChildren(...content);
  $('#modal').hidden = false;
  const first = $('#modalbody').querySelector('button, input, select');
  if (first) first.focus();
}
function closeModal() { $('#modal').hidden = true; }
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#modal').hidden) closeModal(); });
function ask(text, yes = 'Yes', danger = true) {
  return new Promise((done) => {
    const fin = (v) => { closeModal(); done(v); };
    modal(h('h3', {}, 'Are you sure?'), h('p', {}, text),
      h('div', { class: 'row', style: 'justify-content:flex-end' }, h('button', { class: 'btn ghost', onclick: () => fin(false) }, 'Cancel'),
        h('button', { class: 'btn ' + (danger ? 'bad' : 'acc'), onclick: () => fin(true) }, yes)));
  });
}

// ---------------------------------------------------------------- state
const S = {
  tab: new URLSearchParams(location.hash.slice(1)).get('tab') || store.get('tab', 'monitor'), snap: null, paused: false, every: store.get('every', 3000), timer: null, busy: false,
  sort: store.get('sort', { key: 'cpu', dir: -1 }), page: 0, per: store.get('per', 25), q: '',
  filters: store.get('filters', { status: [], mine: false, kthreads: false, agents: false }),
  cols: store.get('cols', ['name', 'pid', 'status', 'user', 'cpu', 'rss', 'virt', 'disk', 'age']),
  pinned: new Set(store.get('pinned', [])), open: store.get('open', {}), hist: { cpu: [], mem: [], gpu: [], rx: [], tx: [] },
};
const COLS = {
  name: ['Process', (p) => p.name], pid: ['PID', (p) => p.pid], ppid: ['Parent', (p) => p.ppid], status: ['Status', (p) => p.status],
  user: ['User', (p) => p.user], cpu: ['CPU %', (p) => p.cpu], rss: ['RAM', (p) => p.rss], mem: ['RAM %', (p) => p.rss],
  virt: ['VIRT', (p) => p.virt], vram: ['VRAM', (p) => p.vram], threads: ['Threads', (p) => p.threads], nice: ['Nice', (p) => p.nice],
  disk: ['Disk R/W', (p) => (p.rd || 0) + (p.wr || 0)], age: ['Run time', (p) => p.age], cmd: ['Command', (p) => p.cmd],
};
const NUMERIC = new Set(['pid', 'ppid', 'cpu', 'rss', 'mem', 'virt', 'vram', 'threads', 'nice', 'disk', 'age']);
const STATUSES = ['Running', 'Sleeping', 'Idle', 'Disk wait', 'Stopped', 'Zombie'];

// remember which drop-downs are open, across redraws and restarts
function keepOpen(el, key, dflt = false) {
  el.open = key in S.open ? S.open[key] : dflt;
  el.addEventListener('toggle', () => { S.open[key] = el.open; store.set('open', S.open); });
  return el;
}
document.querySelectorAll('details.sec').forEach((d) => keepOpen(d, 'sec:' + d.dataset.sec, true));

// ---------------------------------------------------------------- header
function drawTop(s) {
  const memUsed = s.mem.total - s.mem.available, memP = 100 * memUsed / s.mem.total;
  const swapUsed = s.mem.swap_total - s.mem.swap_free, swapP = s.mem.swap_total ? 100 * swapUsed / s.mem.swap_total : 0;
  const W = { CPU: 6, RAM: 17, Swap: 6, GPU: 6, VRAM: 15, 'Claude tokens today': 6, out: 6, 'CPU temp': 5 };   // widest value of each, in characters
  const chip = (label, val, p) => h('span', { class: 'chip ' + (p >= 90 ? 'hot' : p >= 70 ? 'warm' : ''), style: `--w:${W[label] || 0}ch` }, label, h('b', {}, val));
  const tt = s.agents.tokens_today || {};
  const g = s.gpu;
  $('#chips').replaceChildren(
    chip('CPU', pct(s.cpu.total), s.cpu.total), chip('RAM', `${bytes(memUsed)} / ${bytes(s.mem.total)}`, memP), chip('Swap', pct(swapP), swapP),
    g ? chip('GPU', pct(g.util), g.util) : null, g ? chip('VRAM', `${bytes(g.mem_used)} / ${bytes(g.mem_total)}`, 100 * g.mem_used / g.mem_total) : null,
    chip('Claude tokens today', num(tokTotal(tt)), 0), chip('out', num(tt.output || 0), 0),
    s.cpu.temp ? chip('CPU temp', Math.round(s.cpu.temp) + '°C', s.cpu.temp >= 90 ? 95 : s.cpu.temp >= 80 ? 75 : 0) : null);
}

// ---------------------------------------------------------------- at a glance
const vramOf = (g) => (g.vram ? [['VRAM', bytes(g.vram)]] : []);     // video memory the agent's processes hold on the graphics card
function drawGlance(s) {
  const a = s.agents, now = Date.now() / 1000, rows = [];
  const row = (kind, state, name, sub, what, verb, why, nums) => h('div', { class: 'grow ' + kind },
    h('i', { class: 'dot ' + state }), h('div', { class: 'gname' }, h('b', {}, name), h('span', {}, sub)),
    h('div', { class: 'gwhat' }, h('div', { class: 'now', title: what }, verb ? h('i', {}, verb) : null, what), why ? h('div', { class: 'why', title: why }, why) : null),
    h('div', { class: 'gnums' }, nums.map(([k, v]) => h('span', {}, k + ' ', h('b', {}, v)))));
  for (const c of a.claude) {
    const st = c.step, t = st ? Date.parse(st.t) / 1000 : 0, busy = c.status === 'busy';
    const what = st ? (st.what || st.tool) + ` · ${ago(t)}` : 'No steps yet';
    rows.push(row('claude', busy ? '' : 'idle', c.title || 'Claude ' + (c.name || c.pid), `Claude ${c.name || c.pid} · ${busy ? 'working' : 'idle, waiting for you'}`, what, st && st.tool ? st.tool : (st ? '✎' : ''),
      c.task ? 'Asked: ' + c.task : '', [['CPU', cpuOf(c)], ['RAM', bytes(c.rss)], ...vramOf(c), ['out', num((c.tokens || {}).output || 0)]]));
  }
  const r = a.rosie, gl = r.graph && r.graph.last ? Date.parse(r.graph.last) / 1000 : 0;
  const talking = r.seen && now - r.seen < 300;
  const graphing = r.up && s.gpu && s.gpu.util > 25 && now - gl < 1800;     // one memory-graph piece can take minutes on a busy GPU
  rows.push(row('rosie', r.up ? (talking || graphing ? '' : 'idle') : 'off', 'Rosie', r.up ? `slot ${r.slot}` : 'down',
    !r.up ? (r.stopped ? 'Shut down' : 'Not answering') : talking ? 'Talking with you' : graphing ? `Adding to her memory graph (${r.graph.today} today, last ${ago(gl)})` : 'Idle',
    '', [r.browsing ? 'Browsing when idle' : 'Browsing off', r.planning ? 'plans with Claude' : 'plans on her own', r.orchestrator ? 'orchestrator on' : 'orchestrator off'].join(' · '),
    [['CPU', cpuOf(r)], ['RAM', bytes(r.rss)], ...vramOf(r)]));
  const o = a.ollama, gpuBusy = s.gpu && s.gpu.util > 25;
  rows.push(row('ollama', o.up ? (o.models.length && gpuBusy ? '' : 'idle') : 'off', 'Ollama', 'local models',
    !o.up ? 'Not running' : !o.models.length ? 'No model loaded' : (gpuBusy ? 'Running ' : 'Loaded ') + o.models.map((m) => m.name.split(':')[0].split('/').pop()).join(', '),
    '', s.gpu ? `GPU ${pct(s.gpu.util)} · VRAM ${bytes(s.gpu.mem_used)} of ${bytes(s.gpu.mem_total)}` : '', [['CPU', cpuOf(o)], ['RAM', bytes(o.rss)], ['GPU', s.gpu ? pct(s.gpu.util) : '–'], ...vramOf(o)]));
  // busy: it saved a chat in the last 45 s (it saves as it works), took a turn through Rosie in the last 2 min, or uses real CPU itself
  const z = a.agent_zero, zon = z.status === 'running', zlast = Math.max(z.last_active || 0, z.seen || 0);
  const zbusy = zon && ((z.last_active && now - z.last_active < 45) || (z.seen && now - z.seen < 120) || z.cpu > 3);
  rows.push(row('a0', zon ? (zbusy ? '' : 'idle') : 'off', 'Agent Zero', z.status, !zon ? 'Stopped' : zbusy ? `Working${z.chat ? ' · chat ' + z.chat : ''}` : 'Idle',
    '', [zlast ? `last active ${ago(zlast)}` : '', z.task ? 'last: ' + z.task : '', r.brain === 'agent_zero' ? "Rosie's brain" : ''].filter(Boolean).join(' · '),
    [['CPU', cpuOf(z)], ['RAM', bytes(z.rss)], ...vramOf(z)]));
  const m = a.memory;
  rows.push(row('mem', m.status === 'running' ? 'idle' : 'off', 'Memory graph', 'FalkorDB', m.status === 'running' ? `Running · ${r.graph ? num(r.graph.done) + ' pieces' : ''}` : m.status, '',
    m.mcp ? 'Graphiti MCP on' : 'Graphiti MCP off', [['RAM', bytes(m.rss)]]));
  for (const j of a.others) {
    const dead = j.unit && j.state !== 'active';
    const why = dead ? `${j.unit} stopped${j.result && j.result !== 'success' ? ' (' + j.result.replace('oom-kill', 'out of memory') + ')' : ''}${j.ended ? ' at ' + j.ended.split(' ')[2] : ''}`
      + (j.claims.length ? ' · still holds ' + j.claims.join(', ') : '') : (j.claims.length ? 'holds ' + j.claims.join(', ') : '');
    rows.push(row('job', dead ? 'off' : j.count ? '' : 'idle', j.agent, j.unit ? j.unit.replace('.service', '') + ' service' : 'seen ' + ago(j.seen),
      dead ? 'Stopped' : j.task || 'No task posted', '', why || j.task, j.count ? [['CPU', cpuOf(j)], ['RAM', bytes(j.rss)], ...vramOf(j)] : []));
  }
  const t = a.total, used = s.mem.total - s.mem.available;
  rows.push(h('div', { class: 'grow total' }, h('i', { class: 'dot', style: 'visibility:hidden' }),
    h('div', { class: 'gname' }, h('b', {}, 'Total'), h('span', {}, `${t.count} agent processes`)),
    h('div', { class: 'gwhat' }, h('div', { class: 'now' }, `Agents use ${bytes(t.rss)} of the ${bytes(used)} RAM in use`),
      h('div', { class: 'why' }, `CPU: agents ${pct(t.cpu)} of ${pct(s.cpu.total)} in use (${t.cores.toFixed(1)} of ${s.cpu.cores.length} cores) · everything else: ${bytes(Math.max(0, used - t.rss))} RAM · swap ${bytes(s.mem.swap_total - s.mem.swap_free)} · VRAM ${bytes(t.vram)}`),
      h('div', { class: 'tbar' }, h('i', { style: `width:${Math.min(100, 100 * t.rss / s.mem.total)}%` }), h('i', { class: 'rest', style: `width:${Math.max(0, Math.min(100, 100 * (used - t.rss) / s.mem.total))}%` }))),
    h('div', { class: 'gnums' }, h('span', {}, 'CPU ', h('b', {}, cpuOf(t))), h('span', {}, 'RAM ', h('b', {}, bytes(t.rss))),
      s.gpu ? h('span', {}, 'GPU ', h('b', {}, pct(s.gpu.util))) : null, s.gpu ? h('span', {}, 'VRAM ', h('b', {}, `${bytes(s.gpu.mem_used)} / ${bytes(s.gpu.mem_total)}`)) : null)));
  $('#glance').replaceChildren(...rows);
  const busy = a.claude.filter((c) => c.status === 'busy').length;
  $('#glance-sum').textContent = `${busy} of ${a.claude.length} Claude busy · Rosie ${r.up ? 'live' : 'down'} · GPU ${s.gpu ? pct(s.gpu.util) : '–'}`;
}

// ---------------------------------------------------------------- agents
function metrics(g, extra = []) {
  return h('div', { class: 'metrics' },
    h('div', { class: 'metric', title: `${(g.cores || 0).toFixed(2)} of ${S.snap ? S.snap.cpu.cores.length : ''} cores` }, h('small', {}, 'CPU'), h('b', {}, pct(g.cpu))),
    h('div', { class: 'metric' }, h('small', {}, 'RAM'), h('b', {}, bytes(g.rss))),
    h('div', { class: 'metric' }, h('small', {}, g.vram ? 'VRAM' : 'Procs'), h('b', {}, g.vram ? bytes(g.vram) : String(g.count))),
    ...extra);
}
function procList(key, g) {
  if (!g.procs || !g.procs.length) return null;
  return keepOpen(h('details', { class: 'sub' }, h('summary', {}, `Processes (${g.count})`),
    h('table', { class: 'mtable' }, h('tbody', {}, g.procs.map((p) => h('tr', {},
      h('td', {}, p.name), h('td', { class: 'r' }, p.pid), h('td', { class: 'r', title: `${p.cores} cores` }, pct(p.cpu)), h('td', { class: 'r' }, bytes(p.rss)),
      h('td', { title: p.cmd }, p.cmd), h('td', {}, h('button', { class: 'ib', title: 'Details', 'aria-label': 'Details', onclick: () => info(p.pid) }, 'ⓘ'))))))), 'ag:' + key);
}
function card(kind, icon, title, subtitle, badge, ...body) {
  return h('div', { class: `glass card ${kind}` },
    h('div', { class: 'chead' }, h('div', { class: 'ic' }, icon), h('div', { class: 't' }, h('b', {}, title), h('span', { title: subtitle }, subtitle)), badge), ...body);
}
const badge = (text, cls) => h('span', { class: 'badge ' + cls }, text);
const sw = (label, on, name, why) => h('label', { class: 'sw', title: why || '' }, h('input', { type: 'checkbox', class: 'swi', checked: !!on,
  onchange: (e) => act({ action: 'toggle', name, on: e.target.checked }) }), label);

function drawAgents(s) {
  const a = s.agents, cards = [];
  for (const c of a.claude) {
    const t = c.tokens || {};
    cards.push(card('claude', '✳', c.title || `Claude · ${c.name || c.agent}`, `Claude ${c.name || c.agent} · ${c.agent} · ${c.cwd.replace(/^\/home\/[^/]+/, '~')} · v${c.version}`,
      badge(c.status || 'unknown', c.status === 'busy' ? 'busy' : 'idle'),
      c.task ? h('div', { class: 'task', title: c.task }, c.task) : null,
      c.step ? h('div', { class: 'muted', style: 'font-size:.9em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis', title: c.step.what },
        'Now: ', h('b', {}, c.step.tool || 'reply'), ' · ', c.step.what, ' · ', ago(Date.parse(c.step.t) / 1000)) : null,
      metrics(c, [h('div', { class: 'metric' }, h('small', {}, 'MCP'), h('b', {}, String(c.mcp)))]),
      SET.tokens === false ? null : h('div', { class: 'toks' }, h('div', {}, h('small', {}, 'Input'), num(t.input || 0)), h('div', {}, h('small', {}, 'Output'), num(t.output || 0)),
        h('div', {}, h('small', {}, 'Cache write'), num(t.cache_write || 0)), h('div', {}, h('small', {}, 'Cache read'), num(t.cache_read || 0))),
      h('div', { class: 'row' }, h('span', { class: 'muted' }, `${num(t.messages || 0)} replies · up ${dur(Date.now() / 1000 - c.started)}`),
        h('span', { style: 'flex:1' }), h('button', { class: 'btn sm pur', onclick: () => noteTo(c.agent) }, 'Note'),
        h('button', { class: 'btn sm bad', onclick: () => act({ action: 'claude_stop', pid: c.pid }, `Stop the Claude session ${c.name || c.agent}? Work it hasn't saved is lost.`) }, 'Stop')),
      procList('claude-' + c.pid, c)));
  }
  const r = a.rosie;
  cards.push(card('rosie', '◉', 'Rosie', r.up ? `slot ${r.slot} · ${r.commit} · brain ${r.brain.replace('_', ' ')}` : (r.stopped ? 'Shut down' : 'Not answering'),
    badge(r.up ? 'live' : 'down', r.up ? 'busy' : 'down'),
    r.task ? h('div', { class: 'task' }, r.task) : null,
    metrics(r, [h('div', { class: 'metric' }, h('small', {}, 'Memory graph'), h('b', {}, r.graph ? `${num(r.graph.done)} (+${r.graph.today})` : '–'))]),
    h('div', { class: 'switches' }, sw('Browse when idle', r.browsing, 'browsing', 'Rosie reads the web on her own when you are away'),
      sw('Plan with Claude', r.planning, 'planning', 'Planning requests go to Claude (falls back to her own model)'),
      r.orchestrator === null ? null : sw('Orchestrator', r.orchestrator, 'orchestrator', 'Claude plans and checks; Rosie and Agent Zero do the work')),
    r.plan && r.plan.paused_until > Date.now() / 1000 ? h('div', { class: 'muted' }, `Claude planning paused until ${clock(r.plan.paused_until)}: ${r.plan.why}`) : null,
    h('div', { class: 'row' }, h('button', { class: 'btn sm acc', onclick: () => act({ action: 'open', what: 'rosie' }) }, 'Open Rosie'),
      h('button', { class: 'btn sm', onclick: () => act({ action: 'power', op: 'start' }) }, 'Start all'),
      h('button', { class: 'btn sm bad', onclick: () => act({ action: 'power', op: 'stop' }, 'Shut down Rosie, Agent Zero, her memory graph and the AI models?') }, 'Shut down all')),
    procList('rosie', r)));
  const o = a.ollama;
  cards.push(card('ollama', '◆', 'Ollama · local models', o.up ? `${o.models.length} model${o.models.length === 1 ? '' : 's'} loaded` : 'Not running',
    badge(o.up ? (o.models.length ? 'loaded' : 'idle') : 'down', o.up ? (o.models.length ? 'busy' : 'idle') : 'down'),
    metrics(o, [h('div', { class: 'metric' }, h('small', {}, 'GPU'), h('b', {}, s.gpu ? pct(s.gpu.util) : '–'))]),
    o.models.length ? h('div', { class: 'models' }, o.models.map((m) => h('div', { class: 'model' },
      h('span', { class: 'n', title: m.name }, h('b', {}, m.name)), h('span', { class: 'muted' }, `${m.params || ''} · ctx ${num(m.ctx)} · ${bytes(m.vram)} VRAM`),
      h('button', { class: 'btn sm', onclick: () => act({ action: 'ollama_unload', model: m.name }, `Unload ${m.name}? Whoever uses it waits while it loads again.`) }, 'Unload'))))
      : h('div', { class: 'muted' }, 'No model in memory.'),
    h('div', { class: 'muted', style: 'font-size:11px' }, 'Local models are free: tokens are counted for Claude only.'),
    procList('ollama', o)));
  const z = a.agent_zero, zup = z.status === 'running';
  cards.push(card('a0', '◎', 'Agent Zero', `${z.status}${z.started ? ' · since ' + z.started.replace('T', ' ') : ''}`, badge(zup ? 'running' : z.status, zup ? 'busy' : 'down'),
    z.task ? h('div', { class: 'task' }, z.task) : null, metrics(z, [h('div', { class: 'metric' }, h('small', {}, 'Brain'), h('b', {}, r.brain === 'agent_zero' ? 'Rosie' : 'spare'))]),
    h('div', { class: 'row' }, h('button', { class: 'btn sm acc', onclick: () => act({ action: 'open', what: 'agentzero' }) }, 'Open'),
      zup ? h('button', { class: 'btn sm', onclick: () => act({ action: 'docker', name: 'agent-zero', op: 'restart' }, 'Restart Agent Zero? A task it is on stops.', ) }, 'Restart')
        : h('button', { class: 'btn sm', onclick: () => act({ action: 'docker', name: 'agent-zero', op: 'start' }) }, 'Start'),
      zup ? h('button', { class: 'btn sm bad', onclick: () => act({ action: 'docker', name: 'agent-zero', op: 'stop' }, 'Stop Agent Zero? Rosie falls back to her own model.') }, 'Stop') : null),
    procList('a0', z)));
  const m = a.memory;
  cards.push(card('mem', '❖', 'Memory graph', `FalkorDB ${m.status} · Graphiti MCP ${m.mcp ? 'on' : 'off'}`, badge(m.status === 'running' ? 'running' : m.status, m.status === 'running' ? 'busy' : 'down'),
    metrics(m), h('div', { class: 'row' }, m.status === 'running' ? h('button', { class: 'btn sm', onclick: () => act({ action: 'docker', name: 'rosie-graph', op: 'restart' }, 'Restart the memory graph database?') }, 'Restart')
      : h('button', { class: 'btn sm', onclick: () => act({ action: 'docker', name: 'rosie-graph', op: 'start' }) }, 'Start')),
    procList('mem', m)));
  for (const j of a.others) {
    const dead = j.unit && j.state !== 'active';
    cards.push(card('job', '⚙', j.agent, j.unit ? `${j.unit} · ${j.state}${j.result && j.result !== 'success' ? ' (' + j.result + ')' : ''}` : (j.cwd ? j.cwd.replace(/^\/home\/[^/]+/, '~') : `seen ${ago(j.seen)}`),
      badge(dead ? 'stopped' : j.claims.length ? 'holds ' + j.claims.join(', ') : 'seen ' + ago(j.seen), dead ? 'down' : j.claims.length ? 'busy' : 'idle'),
      j.task ? h('div', { class: 'task' }, j.task) : null,
      dead && j.claims.length ? h('div', { class: 'muted' }, `It stopped but still holds ${j.claims.join(', ')}. End the claim on the board if it is not coming back.`) : null,
      j.count ? metrics(j) : null, procList('job-' + j.agent, j)));
  }
  $('#agents').replaceChildren(...cards);
  $('#agents-sum').textContent = `${a.claude.length} Claude session${a.claude.length === 1 ? '' : 's'} · Rosie ${r.up ? 'live' : 'down'} · Agent Zero ${z.status}`;
}

// ---------------------------------------------------------------- the Agent board tab
const KINDS = ['doing', 'done', 'blocked', 'note', 'task'];
const B = { data: null, kinds: store.get('bkinds', []), agent: '', q: '', old: store.get('bold', false), seen: store.get('bseen', Date.now() / 1000 - 86400) };
function who(agent, names) {
  if (agent === 'owner') return 'You';
  const n = (names || {})[agent];
  return n ? (n.title || 'Claude ' + (n.name || agent)) : agent;
}
function noteTo(agent) {
  showTab('board');
  $('#noteto').value = agent;
  if ($('#noteto').sync) $('#noteto').sync();
  $('#notetext').focus();
}
function setOptions(sel, opts) {
  const cur = sel.value;
  if (sel.options.length !== opts.length || [...sel.options].some((o, i) => o.value !== opts[i][0] || o.textContent !== opts[i][1])) {
    sel.replaceChildren(...opts.map(([v, t]) => h('option', { value: v }, t)));
    sel.value = opts.some((o) => o[0] === cur) ? cur : opts[0][0];
    if (sel.sync) sel.sync();
  }
}
function unread(posts) {
  const n = posts.filter((p) => p.t > B.seen && p.agent !== 'owner').length;
  $('#tabcount').textContent = n && S.tab !== 'board' ? String(n) : '';
}
function drawBoardTab(b) {
  const now = Date.now() / 1000, names = b.names;
  const around = b.sessions.filter((x) => !x.gone && now - x.seen < 6 * 3600 && (!names[x.agent] || names[x.agent].alive));
  $('#b-around').replaceChildren(...(around.length ? around.map((x) => {
    const n = names[x.agent], busy = n ? n.status === 'busy' : now - x.seen < 600;
    return h('div', { class: 'who' }, h('i', { class: 'dot ' + (busy ? '' : 'idle') }),
      h('b', { title: x.agent }, who(x.agent, names) + (n ? ` · ${n.name}` : '')), h('span', { title: x.task }, `${x.task || 'no task posted'} · ${ago(x.seen)}`));
  }) : [h('div', { class: 'muted' }, 'Nobody right now.')]));
  $('#b-claims').replaceChildren(...(b.claims.length ? b.claims.map((c) => h('div', { class: 'post' },
    h('span', { class: 'tx', style: 'flex:1' }, h('b', {}, c.thing + ' '), h('span', { class: 'muted' }, `${who(c.agent, names)} · ${ago(c.t)}${c.why ? ' · ' + c.why : ''}`)),
    h('button', { class: 'btn sm bad', onclick: () => act({ action: 'release', thing: c.thing }, `End the claim on ${c.thing}? ${who(c.agent, names)} may still be using it.`) }, 'End')))
    : [h('div', { class: 'muted' }, 'Nothing is claimed.')]));
  const live = Object.entries(names).filter(([, n]) => n.alive);
  setOptions($('#noteto'), [['all', 'Everyone'], ['claude', 'All Claude sessions'], ['rosie', 'Rosie / Agent Zero'], ...live.map(([a, n]) => [a, `${n.title || 'Claude'} (${n.name || a})`])]);
  const agents = [...new Set(b.posts.map((p) => p.agent))];
  setOptions($('#bagent'), [['', 'Every agent'], ...agents.map((a) => [a, who(a, names)])]);
  const q = B.q.toLowerCase();
  const list = b.posts.filter((p) => (B.old || !p.archived) && (!B.kinds.length || B.kinds.includes(p.kind)) && (!B.agent || p.agent === B.agent || p.to_agent === B.agent)
    && (!q || (p.text + ' ' + p.agent + ' ' + who(p.agent, names)).toLowerCase().includes(q))).slice(0, 300);
  const feed = [];
  let day = '';
  const today = new Date().toDateString(), yest = new Date(Date.now() - 86400000).toDateString();
  for (const p of list) {
    const d = new Date(p.t * 1000), ds = d.toDateString();
    if (ds !== day) {
      day = ds;
      feed.push(h('div', { class: 'day' }, ds === today ? 'Today' : ds === yest ? 'Yesterday' : d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })));
    }
    feed.push(h('div', { class: `glass fpost ${p.kind}${p.agent === 'owner' ? ' mine' : ''}` },
      h('span', { class: 'tm' }, clock(p.t)),
      h('div', { class: 'hd' }, h('span', { class: 'k ' + p.kind }, p.kind), h('b', { title: p.agent }, who(p.agent, names)),
        p.to_agent ? h('span', { class: 'to' }, '→ ' + (p.to_agent === 'all' ? 'everyone' : p.to_agent === 'claude' ? 'all Claude sessions' : who(p.to_agent, names))) : null,
        p.archived ? h('span', { class: 'arch' }, 'history') : null),
      h('div', { class: 'tx' }, p.text)));
  }
  $('#b-feed').replaceChildren(...(feed.length ? feed : [h('div', { class: 'muted' }, 'No posts match.')]));
  if (b.posts.length) { B.seen = Math.max(B.seen, b.posts[0].t); store.set('bseen', B.seen); }
  $('#tabcount').textContent = '';
}
$('#bkinds').replaceChildren(...['all', ...KINDS].map((k) => h('button', { type: 'button', 'data-k': k, onclick: () => {
  B.kinds = k === 'all' ? [] : B.kinds.includes(k) ? B.kinds.filter((x) => x !== k) : [...B.kinds, k];
  store.set('bkinds', B.kinds); kindButtons(); if (B.data) drawBoardTab(B.data);
} }, k)));
function kindButtons() { document.querySelectorAll('#bkinds button').forEach((b) => b.classList.toggle('on', b.dataset.k === 'all' ? !B.kinds.length : B.kinds.includes(b.dataset.k))); }
kindButtons();
$('#bsearch').addEventListener('input', (e) => { B.q = e.target.value; if (B.data) drawBoardTab(B.data); });
$('#bagent').addEventListener('change', (e) => { B.agent = e.target.value; if (B.data) drawBoardTab(B.data); });
$('#bold').checked = B.old;
$('#bold').addEventListener('change', (e) => { B.old = e.target.checked; store.set('bold', B.old); if (B.data) drawBoardTab(B.data); });
$('#noteform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('#notetext').value.trim();
  if (!text) return;
  await act({ action: 'note', to: $('#noteto').value, text });
  $('#notetext').value = '';
});

function drawMiniBoard(s) {
  const b = s.agents.board, now = Date.now() / 1000, names = Object.fromEntries(s.agents.claude.map((c) => [c.agent, { title: c.title, name: c.name, alive: true }]));
  const around = b.sessions.filter((x) => now - x.seen < 6 * 3600 && !(x.agent.startsWith('claude-') && !names[x.agent]));
  const posts = b.posts.slice(0, 10);
  $('#mboard').replaceChildren(
    h('div', { class: 'glass mbcol' }, h('h4', {}, 'Around now'), around.length ? around.map((x) => h('div', { class: 'post' }, h('span', { class: 'tm' }, clock(x.seen)),
      h('span', { class: 'tx' }, h('b', {}, who(x.agent, names) + ' '), h('span', { class: 'muted' }, x.task || 'no task posted')))) : h('div', { class: 'muted' }, 'Nobody right now.'),
      h('h4', { style: 'margin-top:10px' }, 'Claims'), b.claims.length ? b.claims.map((c) => h('div', { class: 'post' },
        h('span', { class: 'tx', style: 'flex:1' }, h('b', {}, c.thing + ' '), h('span', { class: 'muted' }, `${who(c.agent, names)} · ${ago(c.t)}`)),
        h('button', { class: 'btn sm bad', onclick: () => act({ action: 'release', thing: c.thing }, `End the claim on ${c.thing}? ${who(c.agent, names)} may still be using it.`) }, 'End')))
        : h('div', { class: 'muted' }, 'Nothing is claimed.')),
    h('div', { class: 'glass mbcol' }, h('div', { class: 'row', style: 'justify-content:space-between' }, h('h4', {}, 'Latest posts'),
      h('button', { class: 'btn sm acc', onclick: () => showTab('board') }, 'Open the board')),
      posts.map((p) => h('div', { class: 'post' }, h('span', { class: 'tm' }, clock(p.t)), h('span', { class: 'k ' + p.kind }, p.kind),
        h('span', { class: 'tx' }, h('b', {}, who(p.agent, names)), p.to_agent ? h('span', { class: 'muted' }, ' → ' + (p.to_agent === 'all' ? 'everyone' : who(p.to_agent, names))) : null, ' ', p.text)))));
  $('#mboard-sum').textContent = `${around.length} around · ${b.claims.length} claim${b.claims.length === 1 ? '' : 's'}`;
}

// ---------------------------------------------------------------- tabs
function showTab(t) {
  S.tab = t; store.set('tab', t);
  $('#tab-monitor').hidden = t !== 'monitor';
  $('#tab-board').hidden = t !== 'board';
  document.querySelectorAll('#tabs button').forEach((b) => { b.classList.toggle('on', b.dataset.tab === t); b.setAttribute('aria-selected', b.dataset.tab === t ? 'true' : 'false'); });
  tick(true);
}
document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

// ---------------------------------------------------------------- system
function spark(vals, max, color) {
  const n = vals.length, w = 300, ht = 42;
  if (n < 2) return h('div', { class: 'spark' });
  const pts = vals.map((v, i) => `${(i / (n - 1) * w).toFixed(1)},${(ht - 2 - Math.min(1, v / (max || 1)) * (ht - 4)).toFixed(1)}`).join(' ');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${ht}`); svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('class', 'spark');
  const pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  pl.setAttribute('points', pts); pl.setAttribute('fill', 'none'); pl.setAttribute('stroke', color); pl.setAttribute('stroke-width', '1.6');
  pl.setAttribute('vector-effect', 'non-scaling-stroke'); pl.style.filter = `drop-shadow(0 0 3px ${color})`;
  svg.append(pl);
  return svg;
}
function sysCard(key, icon, title, p, ...body) {
  return keepOpen(h('details', { class: 'glass syscard' }, h('summary', {}, h('span', { class: 'tw' }, '▾'), icon + ' ' + title, p != null ? h('span', { class: 'pct' }, typeof p === 'number' ? pct(p) : p) : null), ...body), 'sys:' + key, true);
}
const kv = (rows) => h('div', { class: 'kv' }, rows.filter(Boolean).map(([k, v]) => [h('span', {}, k), h('span', {}, v)]));
function drawSystem(s) {
  const H = S.hist, push = (k, v) => { H[k].push(v); if (H[k].length > 90) H[k].shift(); };
  const memUsed = s.mem.total - s.mem.available;
  push('cpu', s.cpu.total); push('mem', 100 * memUsed / s.mem.total); push('gpu', s.gpu ? s.gpu.util || 0 : 0);
  const real = s.net.filter((n) => !/^(veth|br-|docker|virbr)/.test(n.name));     // container bridges repeat the same traffic
  const rx = real.reduce((a, n) => a + n.rx_rate, 0), tx = real.reduce((a, n) => a + n.tx_rate, 0);
  push('rx', rx); push('tx', tx);
  const cards = [
    sysCard('cpu', '▣', 'CPU usage', s.cpu.total,
      h('div', { class: 'cores' }, s.cpu.cores.map((_, i) => i).map((i) => h('div', { class: 'core' }, h('span', { class: 'muted' }, 'Core ' + i), bar(s.cpu.cores[i]), h('span', {}, Math.round(s.cpu.cores[i]) + '%')))),
      spark(H.cpu, 100, '#00F0FF'), kv([s.cpu.temp ? ['Temperature', Math.round(s.cpu.temp) + ' °C'] : null, ['Cores', String(s.cpu.cores.length)]])),
    sysCard('mem', '▤', 'Memory', 100 * memUsed / s.mem.total, h('div', { class: 'bar bigbar' }, h('i', { class: level(100 * memUsed / s.mem.total), style: `width:${100 * memUsed / s.mem.total}%` })),
      kv([['Total', bytes(s.mem.total)], ['Used', bytes(memUsed)], ['Available', bytes(s.mem.available)], ['Free', bytes(s.mem.free)], ['Cache', bytes(s.mem.cached)],
        ['Buffers', bytes(s.mem.buffers)], ['Shared', bytes(s.mem.shared)], ['Swap', `${bytes(s.mem.swap_total - s.mem.swap_free)} / ${bytes(s.mem.swap_total)}`]]),
      spark(H.mem, 100, '#B04DFF')),
    s.gpu ? sysCard('gpu', '▦', 'GPU', s.gpu.util, h('div', { class: 'muted', style: 'font-size:11px;margin-bottom:6px' }, s.gpu.name),
      h('div', { class: 'bar bigbar' }, h('i', { class: level(100 * s.gpu.mem_used / s.gpu.mem_total), style: `width:${100 * s.gpu.mem_used / s.gpu.mem_total}%` })),
      kv([['VRAM', `${bytes(s.gpu.mem_used)} / ${bytes(s.gpu.mem_total)}`], ['Load', pct(s.gpu.util)], s.gpu.temp != null ? ['Temperature', s.gpu.temp + ' °C'] : null,
        s.gpu.power != null ? ['Power', `${Math.round(s.gpu.power)} W${s.gpu.power_limit ? ' / ' + Math.round(s.gpu.power_limit) + ' W' : ''}`] : null, s.gpu.fan != null ? ['Fan', s.gpu.fan + '%'] : null]),
      spark(H.gpu, 100, '#22E58A')) : null,
    sysCard('disk', '▥', 'Storage', s.disks.length ? 100 * s.disks[0].used / s.disks[0].total : null, s.disks.map((d) => h('div', { style: 'margin-bottom:8px' },
      h('div', { class: 'row', style: 'justify-content:space-between;font-size:12px' }, h('b', {}, d.mount), h('span', { class: 'muted' }, `${d.fs} · ${bytes(d.used)} / ${bytes(d.total)}`)),
      bar(100 * d.used / d.total), h('div', { class: 'muted', style: 'font-size:11px' }, `${bytes(d.free)} free`)))),
    sysCard('sys', '▧', 'System', null, kv([['Host', s.host], ['Kernel', s.kernel], ['Uptime', dur(s.uptime)], ['Load 1 m', s.load[0]], ['Load 5 m', s.load[1]], ['Load 15 m', s.load[2]],
      ['Processes', String(s.procs.length)], ['Threads', String(s.threads)]])),
    sysCard('net', '⇅', 'Network I/O', null, kv([['Receiving', rate(rx)], ['Sending', rate(tx)]]), spark(H.rx, Math.max(...H.rx, ...H.tx, 1), '#00F0FF'),
      spark(H.tx, Math.max(...H.rx, ...H.tx, 1), '#FF2BD6'),
      kv(s.net.map((n) => [n.name, `↓ ${rate(n.rx_rate)} · ↑ ${rate(n.tx_rate)}`]))),
  ];
  $('#system').replaceChildren(...cards.filter(Boolean));
  $('#sys-sum').textContent = `CPU ${pct(s.cpu.total)} · RAM ${bytes(memUsed)} of ${bytes(s.mem.total)} · load ${s.load[0]}`;
}

// ---------------------------------------------------------------- processes
function matcher(q) {
  const parts = q.trim().split(/\s+/).filter(Boolean), tests = [];
  for (const part of parts) {
    const m = part.match(/^(u|s|a|pid):(.+)$/i);
    if (m) {
      const v = m[2].toLowerCase(), k = m[1].toLowerCase();
      tests.push(k === 'u' ? (p) => p.user.toLowerCase().includes(v) : k === 's' ? (p) => p.status.toLowerCase().startsWith(v)
        : k === 'a' ? (p) => p.agent.toLowerCase().includes(v) : (p) => String(p.pid) === v);
      continue;
    }
    let re = null;
    try { re = new RegExp(part, 'i'); } catch { /* not a regex: plain words */ }
    const low = part.toLowerCase();
    tests.push(re ? (p) => re.test(p.name) || re.test(p.cmd) || re.test(p.user) || String(p.pid) === part
      : (p) => p.name.toLowerCase().includes(low) || p.cmd.toLowerCase().includes(low) || p.user.toLowerCase().includes(low));
  }
  return (p) => tests.every((t) => t(p));
}
function drawFilterMenu() {
  const f = S.filters, save = () => { store.set('filters', f); S.page = 0; drawHead(); drawProcs(); };
  const cb = (label, on, change) => h('label', {}, h('input', { type: 'checkbox', checked: on, onchange: (e) => { change(e.target.checked); save(); } }), label);
  $('#filtermenu').replaceChildren(h('h5', {}, 'Status (none = all)'),
    STATUSES.map((st) => cb(st, f.status.includes(st), (v) => { f.status = v ? [...f.status, st] : f.status.filter((x) => x !== st); })),
    h('h5', {}, 'Show'), cb('Only my processes', f.mine, (v) => { f.mine = v; }), cb('Only agent processes', f.agents, (v) => { f.agents = v; }),
    cb('Kernel threads', f.kthreads, (v) => { f.kthreads = v; }));
  $('#colmenu').replaceChildren(...Object.entries(COLS).map(([k, [label]]) => cb(label, S.cols.includes(k), (v) => {
    S.cols = Object.keys(COLS).filter((c) => (c === k ? v : S.cols.includes(c))); store.set('cols', S.cols);
  })));
}
// a narrow window (half a screen, a phone, inside Rosie) can't fit nine columns: Process (with PID, status, user and run time under the
// name), CPU and RAM, then the buttons
const NARROW = 720;
const isNarrow = () => { const w = $('#ptable') && $('#ptable').closest('.tablewrap'); return !!w && w.clientWidth > 0 && w.clientWidth < NARROW; };
const shownCols = () => (isNarrow() ? ['name', 'cpu', 'rss'] : S.cols);
function drawHead() {
  $('#ptable').classList.toggle('narrow', isNarrow());
  $('#ptable thead').replaceChildren(h('tr', {}, shownCols().map((k) => h('th', { class: S.sort.key === k ? 'on' : '', title: k === 'cpu' ? '% of all cores' : null, onclick: () => {
    S.sort = S.sort.key === k ? { key: k, dir: -S.sort.dir } : { key: k, dir: NUMERIC.has(k) ? -1 : 1 }; store.set('sort', S.sort); drawProcs();
  } }, COLS[k][0] + (S.sort.key === k ? (S.sort.dir < 0 ? ' ↓' : ' ↑') : ''))), h('th', {}, 'Actions')));
}
function cell(k, p, s) {
  switch (k) {
    case 'name': return h('td', { title: p.cmd }, h('div', { class: 'pn' }, p.name, p.agent ? h('span', { class: 'tag ' + p.agent.split(' ')[0] }, p.agent) : null),
      isNarrow() ? h('div', { class: 'psub' }, `${p.pid} · ${p.status} · ${p.user} · ${dur(p.age)}`) : null);
    case 'cpu': return h('td', { class: 'num', title: `${p.cores} of ${s.cpu.cores.length} cores` }, pct(p.cpu));
    case 'rss': return h('td', { class: 'num' }, bytes(p.rss));
    case 'mem': return h('td', { class: 'num' }, pct(100 * p.rss / s.mem.total));
    case 'virt': return h('td', { class: 'num' }, bytes(p.virt));
    case 'vram': return h('td', { class: 'num' }, p.vram ? bytes(p.vram) : '–');
    case 'disk': return h('td', { class: 'num' }, p.rd == null ? '–' : `${rate(p.rd)} / ${rate(p.wr)}`);
    case 'age': return h('td', { class: 'num' }, dur(p.age));
    case 'cmd': return h('td', { title: p.cmd, style: 'max-width:520px' }, p.cmd || `[${p.name}]`);
    default: return h('td', { class: NUMERIC.has(k) ? 'num' : '' }, String(COLS[k][1](p)));
  }
}
function drawProcs() {
  const s = S.snap;
  if (!s) return;
  const f = S.filters, ok = matcher(S.q);
  let rows = s.procs.filter((p) => (f.kthreads || !p.kthread) && (!f.mine || p.mine) && (!f.agents || p.agent) && (!f.status.length || f.status.includes(p.status)) && ok(p));
  const get = COLS[S.sort.key] ? COLS[S.sort.key][1] : COLS.cpu[1], dir = S.sort.dir;
  rows.sort((a, b) => { const x = get(a), y = get(b); return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir || a.pid - b.pid; });
  const pinKey = (p) => `${p.pid}:${p.name}`;
  const pinned = rows.filter((p) => S.pinned.has(pinKey(p)));
  rows = rows.filter((p) => !S.pinned.has(pinKey(p)));
  const per = Number(S.per) || rows.length || 1, pages = Math.max(1, Math.ceil(rows.length / per));
  S.page = Math.min(S.page, pages - 1);
  const shown = [...pinned, ...rows.slice(S.page * per, S.page * per + per)];
  $('#ptable tbody').replaceChildren(...shown.map((p) => h('tr', { class: [S.pinned.has(pinKey(p)) ? 'pin' : '', p.cores >= 2 ? 'hot' : p.cores >= 0.5 ? 'warm' : ''].join(' ') },
    shownCols().map((k) => cell(k, p, s)),
    h('td', {}, h('div', { class: 'acts' },
      h('button', { class: 'ib' + (S.pinned.has(pinKey(p)) ? ' pinned' : ''), title: 'Pin to the top', 'aria-label': 'Pin', onclick: () => {
        const k = pinKey(p); S.pinned.has(k) ? S.pinned.delete(k) : S.pinned.add(k); store.set('pinned', [...S.pinned]); drawProcs(); } }, '📌'),
      h('button', { class: 'ib', title: 'Details', 'aria-label': 'Details', onclick: () => info(p.pid) }, 'ⓘ'),
      h('button', { class: 'ib k', title: 'Stop this process', 'aria-label': 'Stop', onclick: () => killDialog(p) }, '✕'))))));
  $('#pageinfo').textContent = `Page ${S.page + 1} of ${pages} · ${rows.length + pinned.length} processes`;
  $('#proc-sum').textContent = `${s.procs.length} processes · ${s.threads} threads`;
}
let wasNarrow = null;
new ResizeObserver(() => { const n = isNarrow(); if (n !== wasNarrow) { wasNarrow = n; drawHead(); drawProcs(); } }).observe(document.querySelector('.tablewrap'));
$('#search').addEventListener('input', (e) => { S.q = e.target.value; S.page = 0; drawProcs(); });
$('#per').value = String(S.per);
$('#per').addEventListener('change', (e) => { S.per = Number(e.target.value); store.set('per', S.per); S.page = 0; drawProcs(); });
document.querySelectorAll('[data-pg]').forEach((b) => b.addEventListener('click', () => {
  const total = 1e9;
  S.page = { first: 0, prev: Math.max(0, S.page - 1), next: S.page + 1, last: total }[b.dataset.pg];
  drawProcs();
}));
document.addEventListener('click', (e) => {                                // close the Filters / Columns menus when clicking elsewhere
  document.querySelectorAll('details.dd[open]').forEach((d) => { if (!d.contains(e.target)) { d.open = false; drawHead(); } });
});

function killDialog(p) {
  const send = async (sig) => { closeModal(); await act({ action: 'kill', pid: p.pid, signal: sig }); };
  modal(h('h3', {}, `Stop ${p.name} (${p.pid})?`), h('pre', {}, p.cmd || p.name),
    p.agent ? h('p', { class: 'muted' }, `This belongs to ${p.agent}.`) : null,
    h('p', { class: 'muted' }, 'Ask it to exit first. Force stop only when it does not answer: unsaved work is lost.'),
    h('div', { class: 'row', style: 'justify-content:flex-end' }, h('button', { class: 'btn ghost', onclick: closeModal }, 'Cancel'),
      h('button', { class: 'btn', onclick: () => send(p.status === 'Stopped' ? 'CONT' : 'STOP') }, p.status === 'Stopped' ? 'Resume' : 'Pause'),
      h('button', { class: 'btn acc', onclick: () => send('TERM') }, 'Ask to exit'), h('button', { class: 'btn bad', onclick: () => send('KILL') }, 'Force stop')));
}
async function info(pid) {
  let d;
  try { d = await api('proc?pid=' + pid); } catch (e) { return toast(e.message, true); }
  if (d.error) return toast(d.error, true);
  const link = (x) => h('button', { class: 'btn sm ghost', onclick: () => info(x.pid) }, `${x.name} (${x.pid})`);
  modal(h('h3', {}, `${d.name} · ${d.pid}`), h('pre', {}, d.cmd || `[${d.name}]`),
    kv([['Status', d.status], ['User', d.user], ['Started', new Date(d.started * 1000).toLocaleString()], ['RAM', bytes(d.rss)], ['Peak RAM', d.VmHWM || '–'],
      ['Swapped out', d.VmSwap || '–'], ['Virtual', bytes(d.virt)], ['Threads', String(d.threads)], ['Nice', String(d.nice)], d.fds != null ? ['Open files', String(d.fds)] : null,
      d.cwd ? ['Working folder', d.cwd] : null, d.exe ? ['Program', d.exe] : null, d.io ? ['Disk read', bytes(Number(d.io.read_bytes))] : null,
      d.io ? ['Disk written', bytes(Number(d.io.write_bytes))] : null, ['Context switches', `${d.voluntary_ctxt_switches} / ${d.nonvoluntary_ctxt_switches}`], ['CPUs allowed', d.Cpus_allowed_list]]),
    h('p', { class: 'muted' }, 'Parents'), h('div', { class: 'row' }, d.parents.length ? d.parents.map(link) : '–'),
    h('p', { class: 'muted' }, 'Children'), h('div', { class: 'row' }, d.children.length ? d.children.map(link) : '–'),
    h('div', { class: 'row', style: 'justify-content:flex-end;margin-top:12px' }, h('button', { class: 'btn ghost', onclick: closeModal }, 'Close')));
}

// ---------------------------------------------------------------- loop
async function tick(force) {
  clearTimeout(S.timer);
  if (S.busy) return;
  if ((!S.paused && !document.hidden) || force) {
    S.busy = true;
    try {
      const s = await api('snapshot');
      S.snap = s;
      $('#live').className = 'dot';
      drawTop(s);
      if (S.tab !== 'board') {
        drawGlance(s);
        if (!document.activeElement || !document.activeElement.closest('#mboard')) drawMiniBoard(s);
        if (!document.activeElement || !document.activeElement.closest('.card')) drawAgents(s);
      }
      unread(s.agents.board.posts);
      if (S.tab === 'board') {
        B.data = await api('board');
        if (!document.activeElement || !document.activeElement.closest('#tab-board .bgrid, #noteform')) drawBoardTab(B.data);
      } else {
        drawSystem(s);
        if (!document.querySelector('details.dd[open]')) drawHead();
        drawProcs();
      }
    } catch (e) {
      $('#live').className = 'dot off';
    } finally { S.busy = false; }
  }
  S.timer = setTimeout(tick, S.every);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
$('#every').value = String(S.every);
$('#every').addEventListener('change', (e) => { S.every = Number(e.target.value); store.set('every', S.every); tick(); });
$('#pause').addEventListener('click', () => {
  S.paused = !S.paused;
  $('#pause').textContent = S.paused ? '▶' : '❚❚';
  $('#pause').setAttribute('aria-label', S.paused ? 'Resume updates' : 'Pause updates');
  $('#live').className = S.paused ? 'dot idle' : 'dot';
  if (!S.paused) tick();
});
// ---------------------------------------------------------------- dark drop-downs (GTK draws a <select> and its list in the light theme)
function fancy(sel) {
  const btn = h('button', { type: 'button', class: 'btn selbtn', 'aria-haspopup': 'listbox', 'aria-label': sel.getAttribute('aria-label') || '' });
  const wrap = h('div', { class: 'selwrap' }, btn);
  sel.after(wrap);
  wrap.prepend(sel);
  const sync = () => { const o = sel.options[sel.selectedIndex]; btn.textContent = o ? o.textContent : ''; };
  const close = () => { const m = wrap.querySelector('.selmenu'); if (m) m.remove(); btn.setAttribute('aria-expanded', 'false'); };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (wrap.querySelector('.selmenu')) return close();
    document.querySelectorAll('.selmenu').forEach((m) => m.remove());
    const menu = h('div', { class: 'selmenu', role: 'listbox' }, [...sel.options].map((o) => h('button', { type: 'button', role: 'option', class: o.selected ? 'on' : '',
      'aria-selected': o.selected ? 'true' : 'false', onclick: (ev) => { ev.stopPropagation(); sel.value = o.value; sync(); close(); sel.dispatchEvent(new Event('change')); btn.focus(); } }, o.textContent)));
    wrap.append(menu);
    btn.setAttribute('aria-expanded', 'true');
    (menu.querySelector('.on') || menu.firstChild).focus();
  });
  wrap.addEventListener('keydown', (e) => {
    const items = [...wrap.querySelectorAll('.selmenu button')], i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { close(); btn.focus(); }
    else if (items.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus(); }
  });
  document.addEventListener('click', close);
  new MutationObserver(sync).observe(sel, { childList: true, subtree: true });
  sel.addEventListener('change', sync);
  sel.sync = sync;
  sync();
}
document.querySelectorAll('select').forEach(fancy);

// ---------------------------------------------------------------- settings (this monitor scales everything up: zoom and text size help)
const SECTIONS = [['glance', 'At a glance'], ['agents', 'Agent details'], ['board', 'Agent board'], ['system', 'System'], ['procs', 'Processes']];
const SET = Object.assign({ zoom: 1, text: 13, density: 'normal', glow: 'medium', tokens: true, hide: [], order: [] }, store.get('settings', {}));
// the owner's own order of the Monitor sections (owner 2026-10-21: "make it so i can reorganize them myself"): drag a section by its ⋮⋮
// handle, or use ↑ ↓ in Settings. Sections added later go to the end.
const order = () => [...SET.order.filter((k) => SECTIONS.some(([s]) => s === k)), ...SECTIONS.map(([k]) => k).filter((k) => !SET.order.includes(k))];
function placeSections() {
  const box = $('#tab-monitor');
  for (const k of order()) box.append(box.querySelector(`details.sec[data-sec="${k}"]`));
}
function moveSection(k, to) {
  const o = order().filter((x) => x !== k);
  o.splice(Math.max(0, Math.min(o.length, to)), 0, k);
  SET.order = o; applySettings();
}
document.querySelectorAll('#tab-monitor details.sec').forEach((d) => {
  const k = d.dataset.sec, grip = h('span', { class: 'grip', draggable: 'true', title: 'Drag to move this section', 'aria-label': 'Move section' }, '⋮⋮');
  d.querySelector('summary').prepend(grip);
  grip.addEventListener('click', (e) => e.preventDefault());                       // the handle never opens or closes the section
  grip.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', k); e.dataTransfer.effectAllowed = 'move'; d.classList.add('dragging'); });
  grip.addEventListener('dragend', () => { d.classList.remove('dragging'); document.querySelectorAll('.dropbefore,.dropafter').forEach((x) => x.classList.remove('dropbefore', 'dropafter')); });
  d.addEventListener('dragover', (e) => {
    e.preventDefault();
    const b = d.getBoundingClientRect(), after = e.clientY > b.top + Math.min(60, b.height / 2);
    d.classList.toggle('dropafter', after); d.classList.toggle('dropbefore', !after);
  });
  d.addEventListener('dragleave', () => d.classList.remove('dropbefore', 'dropafter'));
  d.addEventListener('drop', (e) => {
    e.preventDefault();
    const src = e.dataTransfer.getData('text/plain'), after = d.classList.contains('dropafter');
    d.classList.remove('dropbefore', 'dropafter');
    if (!src || src === k) return;
    const o = order().filter((x) => x !== src);
    o.splice(o.indexOf(k) + (after ? 1 : 0), 0, src);
    SET.order = o; applySettings();
  });
});
// Zoom, text size, spacing and background light are Rosie's own Display settings (owner 2026-10-23: "add this setting to the main settings
// and make it control the whole ui"), shared through localStorage rosie.look; inside Rosie's frame her page zoom already scales this one.
const LOOK = 'rosie.look', FRAMED = window.parent !== window;
function readLook() {
  let v = null;
  try { v = JSON.parse(localStorage.getItem(LOOK)); } catch { /* none yet */ }
  if (!v || typeof v !== 'object') return;
  if (typeof v.zoom === 'number') SET.zoom = v.zoom;
  if (typeof v.text === 'number') SET.pct = v.text;
  if (['compact', 'normal', 'big'].includes(v.density)) SET.density = v.density;
  if (['off', 'low', 'medium', 'high'].includes(v.glow)) SET.glow = v.glow;
}
if (typeof SET.pct !== 'number') SET.pct = Math.round(SET.text / 13 * 20) * 5;   // the Deck's old text size in px
readLook();
window.addEventListener('storage', (e) => { if (e.key === LOOK) { readLook(); applySettings(false); } });
function applySettings(share = true) {
  SET.pct = Math.min(150, Math.max(70, SET.pct));
  document.documentElement.style.zoom = FRAMED ? '' : SET.zoom;
  document.documentElement.style.setProperty('--fs', 13 * SET.pct / 100 + 'px');
  document.documentElement.style.setProperty('--glow', { off: 0, low: 0.45, medium: 1, high: 1.6 }[SET.glow] ?? 1);
  document.body.classList.toggle('compact', SET.density === 'compact');
  document.body.classList.toggle('big', SET.density === 'big');
  for (const [k] of SECTIONS) document.querySelector(`details.sec[data-sec="${k}"]`).hidden = SET.hide.includes(k);
  placeSections();
  store.set('settings', SET);
  if (share) {
    let v = {};
    try { v = JSON.parse(localStorage.getItem(LOOK)) || {}; } catch { /* none yet */ }
    try { localStorage.setItem(LOOK, JSON.stringify({ ...v, zoom: SET.zoom, text: SET.pct, density: SET.density, glow: SET.glow })); } catch { /* private window */ }
  }
}
const zoomBy = (d) => { SET.zoom = Math.round(Math.min(2, Math.max(0.5, (d ? SET.zoom + d : 1))) * 100) / 100; applySettings(); if (!$('#modal').hidden) settings(); };
function settings() {
  const seg = (key, opts, after) => h('div', { class: 'seg' }, opts.map(([v, label]) => h('button', { type: 'button', class: SET[key] === v ? 'on' : '',
    onclick: () => { SET[key] = v; applySettings(); if (after) after(v); settings(); } }, label)));
  const stepper = (val, minus, plus, reset) => h('div', { class: 'step' }, h('button', { class: 'btn', type: 'button', onclick: minus, 'aria-label': 'Smaller' }, '−'),
    h('b', {}, val), h('button', { class: 'btn', type: 'button', onclick: plus, 'aria-label': 'Bigger' }, '+'), h('button', { class: 'btn ghost', type: 'button', onclick: reset }, 'Reset'));
  modal(h('h3', {}, 'Settings'),
    h('div', { class: 'set' },
      h('span', {}, 'Zoom'), stepper(Math.round(SET.zoom * 100) + '%', () => zoomBy(-0.05), () => zoomBy(0.05), () => zoomBy(0)),
      h('span', {}, 'Text size'), stepper(SET.pct + '%', () => { SET.pct -= 5; applySettings(); settings(); },
        () => { SET.pct += 5; applySettings(); settings(); }, () => { SET.pct = 100; applySettings(); settings(); }),
      h('span', {}, 'Spacing'), seg('density', [['compact', 'Compact'], ['normal', 'Normal'], ['big', 'Roomy']]),
      h('span', {}, 'Background light'), seg('glow', [['off', 'Off'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High']]),
      h('span', {}, 'Refresh every'), h('div', { class: 'seg' }, [1000, 2000, 3000, 5000, 10000].map((v) => h('button', { type: 'button', class: S.every === v ? 'on' : '',
        onclick: () => { $('#every').value = String(v); $('#every').dispatchEvent(new Event('change')); settings(); } }, v / 1000 + ' s'))),
      h('span', {}, 'Sections'), h('div', { class: 'checks' }, order().map((k, i, all) => h('div', { class: 'secrow' },
        h('label', {}, h('input', { type: 'checkbox', checked: !SET.hide.includes(k),
          onchange: (e) => { SET.hide = e.target.checked ? SET.hide.filter((x) => x !== k) : [...SET.hide, k]; applySettings(); } }), SECTIONS.find(([s]) => s === k)[1]),
        h('span', { class: 'secmove' }, h('button', { class: 'btn sm ghost', type: 'button', disabled: i === 0, 'aria-label': 'Move up', onclick: () => { moveSection(k, i - 1); settings(); } }, '↑'),
          h('button', { class: 'btn sm ghost', type: 'button', disabled: i === all.length - 1, 'aria-label': 'Move down', onclick: () => { moveSection(k, i + 1); settings(); } }, '↓')))),
        h('label', {}, h('input', { type: 'checkbox', checked: SET.tokens !== false, onchange: (e) => { SET.tokens = e.target.checked; applySettings(); tick(true); } }), 'Token counts on Claude cards'))),
    h('p', { class: 'muted', style: 'font-size:.85em' }, 'Zoom, text size, spacing and background light are Rosie\'s Display settings: they change every page. Shortcuts: Ctrl and + or − zoom, Ctrl and 0 resets.'),
    h('div', { class: 'row', style: 'justify-content:flex-end' }, h('button', { class: 'btn acc', onclick: closeModal }, 'Done')));
}
$('#gear').addEventListener('click', settings);
document.addEventListener('keydown', (e) => {
  if (!e.ctrlKey) return;
  if (e.key === '=' || e.key === '+') { e.preventDefault(); zoomBy(0.05); } else if (e.key === '-') { e.preventDefault(); zoomBy(-0.05); } else if (e.key === '0') { e.preventDefault(); zoomBy(0); }
});
applySettings();
drawFilterMenu();
drawHead();
showTab(S.tab);
