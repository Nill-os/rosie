'use strict';
// board #820 safety-text-escaped

const _f = window.fetch.bind(window);
let loginP = null;
async function doLogin() {
  for (let i = 0; i < 3; i++) {
    const code = prompt('Enter your access code (like ABCD-EFGH-JKMN):');
    if (!code) return false;
    const l = await _f('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
    if (l.ok) return true;
    alert((await l.json()).error || 'Wrong code');
  }
  return false;
}
window.fetch = async (u, o) => {
  let r = await _f(u, o);
  if (r.status === 401 && !String(u).includes('/api/login')) {
    if (!loginP) loginP = doLogin().finally(() => { loginP = null; });
    if (await loginP) r = await _f(u, o);
  }
  return r;
};

const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e[k] = v;
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected') e[k] = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) { if (kid == null || kid === false) continue; e.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
  return e;
}
async function J(url, opts) {
  const r = await fetch(url, opts);
  let d = null; try { d = await r.json(); } catch (e) { }
  if (!r.ok) throw new Error((d && d.error) || ('HTTP ' + r.status));
  return d;
}
const jopt = (m, b) => ({ method: m, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) });
const post = (u, b) => J(u, jopt('POST', b));
const put = (u, b) => J(u, jopt('PUT', b));
const patch = (u, b) => J(u, jopt('PATCH', b));
const del = (u) => J(u, { method: 'DELETE' });
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { } },
};
function fmt(x) { return String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/`([^`\n]+)`/g, '<code>$1</code>'); }
const mdNode = (t) => h('div', { class: 'md', html: fmt(t) });
function todayISO() { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function toast(msg, bad) { const d = h('div', { class: bad ? 'bad' : '' }, msg); $('#toast').append(d); setTimeout(() => d.remove(), 3600); }
const btn = (label, cls, fn, type) => h('button', { class: 'btn ' + (cls || ''), type: type || 'button', onclick: fn }, label);
function modal(title, body, { ok = 'Close', cancel = null } = {}) {
  return new Promise((res) => {
    const bg = h('div', { class: 'modalbg' });
    const done = (v) => { bg.remove(); res(v); };
    bg.append(h('div', { class: 'modal', role: 'dialog' }, h('div', { class: 'mh' }, title), h('div', { class: 'mb' }, body),
      h('div', { class: 'mf' }, cancel ? btn(cancel, 'dim', () => done(false)) : null, btn(ok, '', () => done(true)))));
    bg.onclick = (e) => { if (e.target === bg) done(false); };
    document.body.append(bg);
  });
}
function popMenu(anchor, items) {
  document.querySelectorAll('.menu').forEach((m) => m.remove());
  const r = anchor.getBoundingClientRect();
  const m = h('div', { class: 'menu' }, items.map((i) => h('button', { class: i.bad ? 'bad' : '', onclick: () => { m.remove(); i.fn(); } }, i.label)));
  document.body.append(m);
  const w = m.offsetWidth, hh = m.offsetHeight;
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  m.style.top = (r.bottom + hh > innerHeight ? Math.max(8, r.top - hh) : r.bottom) + 'px';
  setTimeout(() => document.addEventListener('click', () => m.remove(), { once: true }), 0);
}
const keepScroll = async (f) => { const y = $('#view').scrollTop; await f(); $('#view').scrollTop = y; };
const DOWN = 'Can\'t reach the assistant. Is the PC on?';

let chatId = store.get('chat_id', null), chatSpace = null, chatTitle = 'New chat';
let meetTimer = null;
let dirty = false, prevHash = location.hash, sideData = { chats: [], spaces: [] }, overdueCount = 0, unreadCount = 0, studyDue = 0, pagesOff = [], pagesHidden = [], loginHint = 'run ~/assistant/login.sh', gmailHint = 'run ~/assistant/login-gmail.sh main';
const log = $('#log'), t = $('#t');
const fit = () => { if (!t.value) { t.style.height = ''; return; } t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight + 2, innerHeight * 0.45) + 'px'; };
{ const d = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  Object.defineProperty(t, 'value', { get() { return d.get.call(t); }, set(v) { d.set.call(t, v); fit(); } }); }
t.addEventListener('input', fit); addEventListener('resize', fit);
t.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#bar').requestSubmit(); } });

function setHeader(title, actions) { $('#ttl').textContent = title; $('#actions').replaceChildren(...(actions || [])); }
function show(...nodes) { $('#view').replaceChildren(h('div', { class: 'page' }, ...nodes)); $('#view').scrollTop = 0; }
const NAV = [['-', 'My day'], ['home', '⌂', 'Home'], ['inbox', '🔔', 'Inbox'], ['calendar', '▦', 'Calendar'], ['tasks', '✓', 'Tasks'], ['mail', '📧', 'Mail'],
  ['-', 'Learn and grow'], ['study', '📚', 'Study'], ['lifequest', '★', 'Life Quest'], ['brief', '☀', 'Morning'],
  ['-', 'Work'], ['projects', '🧭', 'Projects'], ['meetings', '🎙', 'Meetings'], ['notes', '✎', 'Notes'],
  ['-', 'Rosie and helpers'], ['agentzero', '🧠', 'Agent Zero'], ['corner', '❀', "Rosie's corner"], ['memory', '◈', 'Memory'], ['skills', '✦', 'Skills'], ['vector', '🤖', 'Vector'],
  ['-', 'System'], ['deck', '◫', 'System'],
  ['-', ''], ['settings', '⚙', 'Settings']];
const TABS = { home: [['home', 'Home'], ['chats', 'Chats']], brief: [['brief', 'Daily Brief'], ['paper', 'Morning Paper']], memory: [['memory', 'Memory'], ['graph', 'Memory graph']],
  deck: [['deck', 'Agent Deck'], ['servers', 'Servers'], ['tokens', 'Tokens'], ['terminal', 'Terminal']] };
const TAB_GROUP = {}; for (const [g, list] of Object.entries(TABS)) for (const [k] of list) TAB_GROUP[k] = g;
let menuLayout = [];
function menuGroups(nav, layout) {
  const def = [], known = new Set(), home = {};
  for (const n of nav) { if (n[0] === '-') def.push({ label: n[1], pages: [] }); else { def[def.length - 1].pages.push(n[0]); known.add(n[0]); home[n[0]] = def[def.length - 1].label; } }
  if (!Array.isArray(layout) || !layout.length) return def;
  const seen = new Set(), groups = layout.map((g) => ({ label: String(g.label || ''), pages: (g.pages || []).filter((k) => known.has(k) && !seen.has(k) && seen.add(k)) }));
  for (const k of known) {
    if (seen.has(k)) continue;
    const g = groups.find((x) => x.label === home[k]) || groups[groups.length - 1], at = k === 'settings' ? -1 : g.pages.indexOf('settings');
    if (at < 0) g.pages.push(k); else g.pages.splice(at, 0, k);
  }
  return groups;
}
function menuFlat(groups, nav) {
  const info = {}; for (const n of nav) if (n[0] !== '-') info[n[0]] = n;
  return groups.flatMap((g) => [['-', g.label], ...g.pages.map((k) => info[k])]);
}
const navEff = () => menuFlat(menuGroups(NAV, menuLayout), NAV);
function menuMove(groups, key, gi, before) {
  const out = groups.map((g) => ({ label: g.label, pages: g.pages.filter((k) => k !== key) }));
  const to = out[gi].pages, at = before ? to.indexOf(before) : -1;
  if (at < 0) to.push(key); else to.splice(at, 0, key);
  return out;
}
function renderTabs(name) {
  const g = TAB_GROUP[name], box = $('#tabs');
  box.replaceChildren(...(g ? TABS[g].filter(([k]) => k === name || !(pagesOff.includes(k) || pagesHidden.includes(k))).map(([k, label]) => h('button', { type: 'button', class: 'tab' + (k === name ? ' on' : ''), 'aria-current': k === name ? 'page' : null,
    onclick: () => go('#/' + k) }, label)) : []));
  box.hidden = !g;
}
const GLOW = { off: 0, low: 0.45, medium: 1, high: 1.6 };
const THEMES = [['aurora', 'Aurora (modern)'], ['cyberdeck', 'Cyberdeck'], ['jetsons', 'Jetsons (easy to read)']];
let installTheme = store.get('rosie.theme.default', 'aurora');
function look() {
  const v = store.get('rosie.look', null);
  if (v && typeof v === 'object') return { zoom: 1, text: 100, density: 'normal', glow: 'medium', ...v };
  const d = store.get('deck.settings', {});
  return { zoom: Number(store.get('rosie.uiScale', 1)) || 1, text: d.text ? Math.round(d.text / 13 * 20) * 5 : 100,
    density: ['compact', 'normal', 'big'].includes(d.density) ? d.density : 'normal', glow: d.glow in GLOW ? d.glow : 'medium' };
}
function applyLook(v, save) {
  v.zoom = Math.min(2, Math.max(0.5, Math.round(v.zoom * 100) / 100));
  v.text = Math.min(150, Math.max(70, Math.round(v.text)));
  const r = document.documentElement;
  r.style.zoom = v.zoom === 1 ? '' : String(v.zoom);

  r.style.setProperty('--glow', String(GLOW[v.glow] ?? 1));
  document.body.classList.toggle('look-compact', v.density === 'compact');
  document.body.classList.toggle('look-roomy', v.density === 'big');
  const theme = THEMES.some(([k]) => k === v.theme) ? v.theme : installTheme;
  r.dataset.theme = theme;
  r.style.setProperty('--ts', String(v.text / 100 * (theme === 'jetsons' ? 1.12 : 1)));
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'jetsons' ? '#10203E' : '#000000');
  if (save) store.set('rosie.look', v);
  return v;
}
const setLook = (patch) => applyLook({ ...look(), ...patch }, true);
applyLook(look(), true);
window.addEventListener('storage', (e) => { if (e.key === 'rosie.look') applyLook(look(), false); });
document.addEventListener('keydown', (e) => {
  if (!e.ctrlKey || e.altKey || e.shiftKey && e.key !== '+') return;
  const z = look().zoom;
  if (e.key === '=' || e.key === '+') { e.preventDefault(); setLook({ zoom: z + 0.05 }); }
  else if (e.key === '-') { e.preventDefault(); setLook({ zoom: z - 0.05 }); }
  else if (e.key === '0') { e.preventDefault(); setLook({ zoom: 1 }); }
});
function menuBox(again) {
  const save = async (k, on) => {
    const hidden = on ? pagesHidden.filter((x) => x !== k) : [...pagesHidden, k];
    try { const r = await post('/api/settings', { hidden_pages: hidden }); if (r.error) throw new Error(r.error); pagesHidden = r.hidden_pages || hidden; renderNav('settings'); } catch (e) { toast(e.message, 1); }
  };
  let groups = menuGroups(NAV, menuLayout), timer = null, drag = null;
  const status = h('span', { class: 'muted mg-status', role: 'status', 'aria-live': 'polite' }, '');
  const list = h('div', { class: 'mg-list' });
  const payload = () => groups.map((g, i) => ({ label: g.label.trim().slice(0, 30) || (i === groups.length - 1 && g.pages.includes('settings') ? '' : 'Group ' + (i + 1)), pages: g.pages }));
  const push = async (layout) => {
    status.textContent = 'Saving…';
    try { const r = await post('/api/settings', { menu_layout: layout }); if (r.error) throw new Error(r.error); menuLayout = r.menu_layout || layout; renderNav('settings'); status.textContent = 'Saved'; }
    catch (e) { status.textContent = 'Not saved'; toast(e.message, 1); }
  };
  const changed = () => { status.textContent = ''; clearTimeout(timer); timer = setTimeout(() => push(payload()), 600); };
  const label = (g) => g.label.trim() || 'Settings group';
  const lastKey = (fk) => { const e = list.querySelector('[data-f="' + fk + '"]'); if (e) e.focus(); };
  const edit = (next, fk) => { groups = next; draw(); if (fk) lastKey(fk); changed(); };
  const rowOf = (g, gi, k) => {
    const n = NAV.find((x) => x[0] === k), i = g.pages.indexOf(k), f = (w) => 'p:' + k + ':' + w, dis = (on) => (on ? 'true' : null);
    const stepBtn = (txt, tip, fk, off, fn) => h('button', { type: 'button', class: 'btn dim sm', title: tip, 'aria-label': tip + ' ' + n[2], 'aria-disabled': dis(off), 'data-f': fk, onclick: () => { if (!off) fn(); } }, txt);
    const sel = h('select', { 'aria-label': 'Move ' + n[2] + ' to', 'data-f': f('to'), onchange: (e) => edit(menuMove(groups, k, Number(e.target.value), null), f('to')) },
      h('option', { value: '' }, 'Move to…'), ...groups.map((x, j) => h('option', { value: String(j), disabled: j === gi }, label(x))));
    const row = h('div', { class: 'mg-row', draggable: 'true', 'data-key': k,
      ondragstart: (e) => { drag = k; row.classList.add('mg-drag'); if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', k); } catch (x) { } } },
      ondragend: () => { drag = null; list.querySelectorAll('.mg-over,.mg-drag').forEach((x) => x.classList.remove('mg-over', 'mg-drag')); },
      ondragover: (e) => { if (drag && drag !== k) { e.preventDefault(); e.stopPropagation(); row.classList.add('mg-over'); } },
      ondragleave: () => row.classList.remove('mg-over'),
      ondrop: (e) => { if (!drag || drag === k) return; e.preventDefault(); e.stopPropagation(); edit(menuMove(groups, drag, gi, k)); } },
      h('span', { class: 'mg-handle', 'aria-hidden': 'true', title: 'Drag to move' }, '⠿'),
      k === 'settings' ? h('span', { class: 'mg-name' }, h('span', { class: 'mg-ic' }, n[1]), n[2])
        : h('label', { class: 'mg-name' }, h('input', { type: 'checkbox', checked: !pagesHidden.includes(k), 'aria-label': 'Show ' + n[2] + ' in the menu', onchange: (e) => save(k, e.target.checked) }), h('span', { class: 'mg-ic' }, n[1]), n[2]),
      h('span', { class: 'mg-btns' },
        stepBtn('↑', 'Move up', f('up'), i === 0, () => edit(menuMove(groups, k, gi, g.pages[i - 1]), f('up'))),
        stepBtn('↓', 'Move down', f('down'), i === g.pages.length - 1, () => edit(menuMove(groups, k, gi, g.pages[i + 2] || null), f('down'))), sel));
    return row;
  };
  const cardOf = (g, gi) => {
    const f = (w) => 'g:' + gi + ':' + w, swap = (a, b) => { const x = groups.map((y) => y); [x[a], x[b]] = [x[b], x[a]]; return x; };
    const shown = g.pages.filter((k) => !pagesOff.includes(k));
    const isLast = gi === groups.length - 1 && g.pages.includes('settings');
    const name = h('input', { type: 'text', class: 'mg-title', value: g.label, maxlength: '30', placeholder: isLast ? 'No name' : 'Group name', 'aria-label': 'Group ' + (gi + 1) + ' name',
      oninput: (e) => { g.label = e.target.value; changed(); }, onchange: () => draw() });
    const gb = (txt, tip, fk, off, fn) => h('button', { type: 'button', class: 'btn dim sm', title: tip, 'aria-label': tip + ' ' + label(g), 'aria-disabled': off ? 'true' : null, 'data-f': fk, onclick: () => { if (!off) fn(); } }, txt);
    const remove = () => {
      if (groups.length < 2) return;
      const to = gi > 0 ? gi - 1 : 1;
      if (g.pages.length && !confirm('Remove the group "' + label(g) + '"? Its ' + g.pages.length + ' page' + (g.pages.length > 1 ? 's' : '') + ' move to "' + label(groups[to]) + '".')) return;
      if (!g.pages.length && !confirm('Remove the empty group "' + label(g) + '"?')) return;
      const next = groups.map((x, j) => (j === to ? { label: x.label, pages: gi > 0 ? [...x.pages, ...g.pages] : [...g.pages, ...x.pages] } : x)).filter((x, j) => j !== gi);
      edit(next);
    };
    return h('div', { class: 'mg-card', 'data-group': String(gi),
      ondragover: (e) => { if (drag) { e.preventDefault(); } },
      ondrop: (e) => { if (!drag) return; e.preventDefault(); edit(menuMove(groups, drag, gi, null)); } },
      h('div', { class: 'mg-head' }, name, h('span', { class: 'mg-btns' },
        gb('↑', 'Move group up', f('up'), gi === 0, () => edit(swap(gi, gi - 1), f('up'))),
        gb('↓', 'Move group down', f('down'), gi === groups.length - 1, () => edit(swap(gi, gi + 1), f('down'))),
        gb('Remove', 'Remove group', f('rm'), groups.length < 2, remove))),
      shown.length ? shown.map((k) => rowOf(g, gi, k)) : h('div', { class: 'muted mg-empty' }, 'Empty. Drag a page here, or use Move to on a page.'));
  };
  function draw() {
    list.replaceChildren(...groups.map(cardOf), h('div', { class: 'mg-foot' },
      btn('Add group', 'sm', () => { const at = groups.length && groups[groups.length - 1].pages.includes('settings') ? groups.length - 1 : groups.length; const next = [...groups]; next.splice(at, 0, { label: 'New group', pages: [] }); edit(next); }),
      btn('Reset to default', 'dim sm', () => { if (!confirm('Put the menu back in its built-in order?')) return; clearTimeout(timer); groups = menuGroups(NAV, []); draw(); push([]); }), status));
  }
  draw();
  const tabKids = Object.values(TABS).flat().filter(([k]) => !NAV.some((n) => n[0] === k) && !pagesOff.includes(k));
  return h('div', {}, list,
    tabKids.length ? h('div', { class: 'mg-tabs' }, h('div', { class: 'muted', style: 'margin:8px 0 4px' }, 'Tabs inside a page'), h('div', { class: 'menu-pick' }, tabKids.map(([k, l]) =>
      h('label', {}, h('input', { type: 'checkbox', checked: !pagesHidden.includes(k), onchange: (e) => save(k, e.target.checked) }), ' ↳ ' + l)))) : null,
    h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'Drag a page by its handle, or use the arrows and Move to. Name a group to rename it. Untick a page to leave it out of the menu; its features keep working. A page with tabs moves as one. Saved by itself.'));
}
function lookBox(again) {
  const v = look(), set = (p) => { setLook(p); again(); };
  const step = (val, minus, plus, reset) => h('div', { class: 'form', style: 'gap:6px;align-items:center' }, btn('−', 'dim', minus), h('b', { style: 'min-width:52px;text-align:center' }, val),
    btn('+', 'dim', plus), btn('Reset', 'dim', reset));
  const seg = (key, opts) => h('div', { class: 'seg' }, opts.map(([k, label]) => h('button', { class: v[key] === k ? 'on' : '', onclick: () => set({ [key]: k }) }, label)));
  return h('div', {}, h('dl', { class: 'kv' },
    h('dt', {}, 'Zoom'), h('dd', {}, step(Math.round(v.zoom * 100) + '%', () => set({ zoom: v.zoom - 0.05 }), () => set({ zoom: v.zoom + 0.05 }), () => set({ zoom: 1 }))),
    h('dt', {}, 'Text size'), h('dd', {}, step(v.text + '%', () => set({ text: v.text - 5 }), () => set({ text: v.text + 5 }), () => set({ text: 100 }))),
    h('dt', {}, 'Spacing'), h('dd', {}, seg('density', [['compact', 'Compact'], ['normal', 'Normal'], ['big', 'Roomy']])),
    h('dt', {}, 'Background light'), h('dd', {}, seg('glow', [['off', 'Off'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High']])),
    h('dt', {}, 'Theme'), h('dd', {}, h('div', { class: 'seg' }, THEMES.map(([k, label]) => h('button', { class: (v.theme || installTheme) === k ? 'on' : '', onclick: () => set({ theme: k }) }, label))),
      h('div', { class: 'form', style: 'margin-top:6px;align-items:center' }, btn('Use on every device', 'sm dim', async () => {
        try { await post('/api/look/theme', { theme: v.theme || installTheme }); installTheme = v.theme || installTheme; store.set('rosie.theme.default', installTheme); toast('Every device that has not picked its own theme now uses this one.'); }
        catch (e) { toast(e.message, 1); } }), h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, 'Jetsons: larger, plainer text with stronger colors.')))),
    h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'For every page, the Agent Deck too. Each device keeps its own (your phone and this PC can differ). Shortcuts: Ctrl and + or − zoom, Ctrl and 0 resets.'));
}
let navFolded = store.get('nav.folded', ['Advanced']);
const navRail = () => matchMedia('(min-width:600px) and (max-width:899px)').matches && !document.body.classList.contains('side-open');
function navShown(active) {
  const out = new Set([...pagesOff, ...pagesHidden]);
  const items = navEff().filter(([k]) => k === '-' || !out.has(k));
  const kept = items.filter(([k], i) => k !== '-' || (items[i + 1] && items[i + 1][0] !== '-'));
  if (navRail()) return kept;
  let head = '';
  return kept.filter(([k, label]) => { if (k === '-') { head = label; return true; } return !navFolded.includes(head) || k === active; });
}
function navFold(label) {
  navFolded = navFolded.includes(label) ? navFolded.filter((x) => x !== label) : [...navFolded, label];
  store.set('nav.folded', navFolded);
  renderNav(navActive());
}
const navActive = () => { const n = (location.hash.replace(/^#\/?/, '') || 'home').split('/')[0].replace('chat', ''); return TAB_GROUP[n] || n; };
function renderNav(active) {
  $('#nav').replaceChildren(...navShown(active).map(([k, ic, label]) => k === '-' ? (ic && !navRail()
    ? h('button', { class: 'navhead', 'aria-expanded': String(!navFolded.includes(ic)), title: navFolded.includes(ic) ? 'Show this group' : 'Fold this group away', onclick: () => navFold(ic) },
        ic, h('span', { class: 'fold', 'aria-hidden': 'true' }, navFolded.includes(ic) ? '▸' : '▾'))
    : h('div', { class: 'navhead', role: 'presentation' }, ic)) : h('button', { class: 'nav' + (k === active ? ' on' : ''), title: label, 'aria-label': label, onclick: () => go('#/' + k) },
    h('span', { class: 'ic' }, ic), label, k === 'tasks' && overdueCount ? h('span', { class: 'cnt', title: 'Overdue tasks' }, overdueCount) : null,
    k === 'inbox' && unreadCount ? h('span', { class: 'cnt', style: 'background:var(--pur);color:#fff', title: 'Unread' }, unreadCount) : null,
    k === 'study' && studyDue ? h('span', { class: 'cnt', style: 'background:var(--acc);color:#000', title: 'Flash cards due' }, studyDue) : null)));
}
function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }
window.addEventListener('hashchange', () => {
  if (dirty && !confirm('Discard unsaved changes?')) { history.replaceState(null, '', prevHash || '#/home'); return; }
  dirty = false; prevHash = location.hash; route();
});
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
async function route() {
  const parts = (location.hash.replace(/^#\/?/, '') || 'home').split('/');
  const name = parts[0], arg = decodeURIComponent(parts.slice(1).join('/'));
  document.body.classList.remove('side-open');
  const inChat = name === 'chat';
  document.body.classList.toggle('in-chat', inChat);
  if (!inChat && talking) stopTalk();
  if (name !== 'terminal') closeTerm();
  if (name !== 'meetings') clearInterval(meetTimer);
  if (name !== 'study') walkStop();
  renderNav(inChat ? '' : TAB_GROUP[name] || name);
  renderTabs(inChat ? '' : name);
  try {
    if (inChat) await showChat(arg); else await (VIEWS[name] || VIEWS.home)(arg);
  } catch (e) {
    if (!inChat) show(h('div', { class: 'err' }, 'Something went wrong: ' + e.message));
  }
  drawChats();
}

async function refreshSide() {
  try { sideData = await J('/api/chats'); } catch (e) { $('#dot').className = 'off'; return; }
  $('#dot').className = '';
  try { const b = await J('/api/badges'); overdueCount = b.overdue; unreadCount = b.inbox; studyDue = b.study_due || 0; if (b.ui_theme && b.ui_theme !== installTheme) { installTheme = b.ui_theme; store.set('rosie.theme.default', installTheme); applyLook(look()); } pagesOff = b.pages_off || []; loginHint = b.google_login || loginHint; gmailHint = b.gmail_login || gmailHint; pagesHidden = b.pages_hidden || []; menuLayout = b.menu_layout || []; } catch (e) { }
  renderNav(navActive());
  renderTabs((location.hash.replace(/^#\/?/, '') || 'home').split('/')[0]);
  drawChats();
}
function drawChats() {
  const box = $('#chats'); if (!box) return;
  const open = store.get('open_spaces', {}), spaces = sideData.spaces || [], chats = sideData.chats || [];
  const inChat = document.body.classList.contains('in-chat');
  const known = new Set(spaces);
  const labels = [...spaces.map((s) => [s, s]), ['Inbox', null]];
  const extra = [...new Set(chats.map((c) => c.space).filter((s) => s && !known.has(s) && s !== 'Briefs'))];
  extra.forEach((s) => labels.push([s, s]));
  labels.push(['Briefs', 'Briefs']);
  const out = [];
  for (const [label, sp] of labels) {
    const list = chats.filter((c) => (c.space || null) === sp);
    if (!list.length && (sp === null || sp === 'Briefs')) continue;
    const closed = open[label] === undefined ? sp === 'Briefs' : open[label] === false;
    const showAll = store.get('all_' + label, false);
    out.push(h('div', { class: 'sp-h' + (closed ? ' closed' : ''), onclick: () => { open[label] = closed; store.set('open_spaces', open); drawChats(); } },
      h('span', { class: 'tw' }, '▾'), label, sp !== 'Briefs' ? h('button', { class: 'add', 'aria-label': 'New chat in ' + label, title: 'New chat in ' + label, onclick: (e) => { e.stopPropagation(); newChat(sp); } }, '＋') : null));
    if (closed) continue;
    list.slice(0, showAll ? 99 : 6).forEach((c) => out.push(h('div', { class: 'chat' + (inChat && c.id === chatId ? ' on' : ''), 'data-id': c.id },
      h('button', { class: 't', title: c.title, onclick: () => go('#/chat/' + c.id) }, c.title),
      h('button', { class: 'mn', 'aria-label': 'Chat menu', onclick: (e) => { e.stopPropagation(); chatMenu(c, e.currentTarget); } }, '⋯'))));
    if (list.length > 6) out.push(h('button', { class: 'more', onclick: () => { store.set('all_' + label, !showAll); drawChats(); } }, showAll ? 'show less' : `show ${list.length - 6} more`));
  }
  box.replaceChildren(...out);
}
function chatMenu(c, anchor) {
  popMenu(anchor, [
    { label: '✎ Rename', fn: async () => { const v = prompt('Rename chat:', c.title); if (!v) return; await patch('/api/chats/' + c.id, { title: v }); if (c.id === chatId) { chatTitle = v; setChatHeader(); } refreshSide(); } },
    { label: '→ Move to space…', fn: () => popMenu(anchor, [...(sideData.spaces || []).map((s) => ({ label: s, fn: async () => { await patch('/api/chats/' + c.id, { space: s }); if (c.id === chatId) { chatSpace = s; setChatHeader(); } refreshSide(); } })),
      { label: 'Inbox (no space)', fn: async () => { await patch('/api/chats/' + c.id, { clear_space: true }); if (c.id === chatId) { chatSpace = null; setChatHeader(); } refreshSide(); } }]) },
    { label: '🗑 Delete', bad: true, fn: async () => { if (!confirm('Delete this chat for good?')) return; await del('/api/chats/' + c.id); if (c.id === chatId) newChat(); refreshSide(); } },
  ]);
}

const selfRow = (r) => h('div', { class: 'item' },
  h('span', { class: 'chip ' + (r.ok ? 'ok' : 'bad') }, r.ok ? 'ok' : 'fix'),
  h('span', { class: 'tx' }, h('b', {}, r.name + '  '), h('span', { class: 'muted' }, r.detail)));
const wiz = { active: false, tried: new Set(), total: 0, done: 0, waiting: false };
const wizText = (r) => ({ phonectl: 'Turn on phone control', perm_mic: 'Allow the microphone', wake: 'Turn on "Hey Rosie"', battery: 'Let Rosie run in the background', overlay: 'Allow the popup over other apps', perm_notif: 'Allow notifications',
  perm_phone: 'Allow contacts, texts and calls', notif_access: 'Allow notification access (switch Rosie on)', accessibility: 'Turn on screen control (switch "Rosie screen control" on)', assistant: 'Make Rosie your assistant (headphone button)' }[r.fix] || r.name);
const wizShow = (msg) => { const out = document.getElementById('selftest-out'); if (!out) return; let b = document.getElementById('wiz-banner'); if (!b) { b = h('div', { id: 'wiz-banner', class: 'card', style: 'border-color:var(--pur);margin-bottom:10px' }); out.prepend(b); } b.replaceChildren(h('div', { style: 'font-weight:700' }, msg), h('div', { class: 'form', style: 'margin-top:8px' }, btn('Skip this one', 'sm dim', () => wizNext()), btn('Stop', 'sm', () => { wiz.active = false; b.remove(); }))); };
let wizRows = [];
function wizNext() {
  if (!wiz.active) return;
  const todo = wizRows.filter((r) => !r.ok && r.fix && !wiz.tried.has(r.fix));
  if (!todo.length) { wiz.active = false; wiz.waiting = false; const b = document.getElementById('wiz-banner'); if (b) b.remove(); AndroidBridge.log('wizard: all steps handled, final full check'); AndroidBridge.selfTestStart(); return; }
  const r = todo[0]; wiz.tried.add(r.fix); wiz.waiting = true;
  wizShow('Step ' + (wiz.total - todo.length + 1) + ' of ' + wiz.total + ': ' + wizText(r));
  AndroidBridge.log('wizard: step ' + r.fix);
  setTimeout(() => {
    AndroidBridge.fix(r.fix);
    let left = false, ticks = 0;
    const t = setInterval(() => {
      ticks++; const v = AndroidBridge.appVisible();
      if (!v) left = true;
      if (!wiz.active || !wiz.waiting) { clearInterval(t); return; }
      if ((left && v) || ticks > 170) { clearInterval(t); wiz.waiting = false; AndroidBridge.log('wizard: back from ' + r.fix + ' (left=' + left + '), re-checking'); setTimeout(() => AndroidBridge.selfTestQuick(), 500); }
    }, 700);
  }, 1200);
}
window.onAppResumeWizard = () => { };
window.runSelfTest = (tries = 0) => {
  const out = document.getElementById('selftest-out');
  if (!out) { if (tries < 20) setTimeout(() => window.runSelfTest(tries + 1), 800); return; }
  out.replaceChildren(h('div', { class: 'muted' }, 'Running… (about 20 seconds)'));
  wiz.tried = new Set(); wiz.active = false;
  window.onSelfTest = (rows, quick) => {
    wizRows = rows;
    const bad = rows.filter((r) => !r.ok).length;
    const head = h('div', { style: 'margin-bottom:8px;font-weight:700' }, bad ? bad + ' to fix, ' + (rows.length - bad) + ' ok' : 'All ' + rows.length + ' checks passed');
    const keep = document.getElementById('wiz-banner');
    out.replaceChildren(...(keep ? [keep] : []), head, h('div', { class: 'list' }, rows.map(selfRow)));
    if (!wiz.active && !quick && rows.some((r) => !r.ok && r.fix && !wiz.tried.has(r.fix))) { wiz.active = true; wiz.total = rows.filter((r) => !r.ok && r.fix).length; wizNext(); }
    else if (wiz.active) { wiz.waiting = false; wizNext(); }
  };
  AndroidBridge.selfTestStart();
};
window.startTraining = () => {
  const box = h('div', { style: 'position:fixed;inset:0;z-index:80;background:#000d;display:flex;align-items:center;justify-content:center;padding:24px' });
  const card = h('div', { class: 'card', style: 'max-width:420px;width:100%;text-align:center' }); box.append(card); document.body.append(box);
  const close = () => { box.remove(); window.onTrain = null; if (location.hash.startsWith('#/settings')) VIEWS.settings(); };
  const say = (title, line, extra) => { card.replaceChildren(h('h3', {}, title), h('div', { style: 'font-size:calc(20px*var(--ts,1));margin:14px 0;min-height:56px' }, line), extra || h('span')); };
  say('Train my voice', 'Get ready. Say "Hey Rosie" clearly each time you hear the tone.');
  window.onTrain = (m) => {
    if (m.state === 'ready') say('Attempt ' + m.step + ' of ' + m.of, 'Get ready…');
    else if (m.state === 'listening') say('Attempt ' + m.step + ' of ' + m.of, '🎤 Say: "Hey Rosie"');
    else if (m.state === 'heard') say('Attempt ' + m.step + ' of ' + m.of, (m.good ? '✅ ' : '⚠️ ') + 'I heard: ' + m.text);
    else if (m.state === 'done') say(m.saved ? 'Saved' : 'Not saved', (m.good + ' of ' + m.of + ' were clear.') + (m.learned ? ' I learned how you say it: ' + m.learned.split('|').filter(Boolean).join(', ') + '.' : '') + (m.saved ? ' She now ignores quieter or different speech.' : ' Try again somewhere quieter, closer to the phone.'), btn('Done', 'ok', close));
    else if (m.state === 'error') say('Something went wrong', m.text, btn('Close', 'sm', close));
  };
  AndroidBridge.trainStart();
};
const VIEWS = {};
function taskRow(tk, onChange) {
  const late = !tk.done && tk.due < todayISO();
  return h('div', { class: 'item' + (tk.done ? ' done' : '') + (late ? ' over' : '') },
    h('button', { class: 'check' + (tk.done ? ' on' : ''), 'aria-label': tk.done ? 'Mark not done' : 'Mark done', onclick: async () => {
      try { Object.assign(tk, await post(`/api/tasks/${tk.id}/done`, { done: !tk.done })); onChange(); } catch (e) { toast(e.message, 1); } } }, tk.done ? '✓' : ''),
    h('div', { class: 'tx', role: 'button', tabindex: '0', title: 'Details', style: 'cursor:pointer', onclick: () => taskDetails(tk, onChange),
      onkeydown: (e) => { if (e.key === 'Enter') taskDetails(tk, onChange); } }, tk.title),
    h('button', { class: 'chip' + (late ? ' bad' : ''), title: 'Change due date', onclick: async () => {
      const v = prompt('Move to (today, tomorrow, friday, or 2026-10-09):', tk.due); if (!v) return;
      try { await post(`/api/tasks/${tk.id}/move`, { due: v }); onChange(); } catch (e) { toast(e.message, 1); } } }, tk.due === todayISO() ? 'today' : tk.due),
    h('button', { class: 'x', 'aria-label': 'Delete task', onclick: async () => { if (!confirm('Delete this task?')) return; await del(`/api/tasks/${tk.id}`); onChange(); } }, '✕'));
}
async function taskDetails(tk, onChange) {
  let d;
  try { d = await J(`/api/tasks/${tk.id}`); } catch (e) { toast(e.message, 1); return; }
  const title = h('textarea', { rows: 2, 'aria-label': 'Title', style: 'width:100%;resize:vertical' });
  title.value = d.title;
  const due = h('input', { value: d.due || '', 'aria-label': 'Due', placeholder: 'today, friday, 2026-10-09', style: 'width:100%' });
  const notes = h('textarea', { rows: 4, 'aria-label': 'Notes', placeholder: 'Notes: what this is about, links, who it is for…', style: 'width:100%;resize:vertical' });
  notes.value = d.notes || '';
  const label = (t) => h('div', { class: 'muted', style: 'margin:10px 0 4px;font-size:calc(12px*var(--ts,1));letter-spacing:.1em;text-transform:uppercase' }, t);
  const o = d.origin, em = o && o.email;
  const origin = o ? h('div', { class: 'card', style: 'margin-top:4px;white-space:normal' },
    h('div', {}, o.from_email ? 'Suggested from an email' : 'Suggested by Rosie', h('span', { class: 'muted' }, ' · ' + (o.suggested || '').slice(0, 16).replace('T', ' '))),
    em && (em.from || em.subject) ? h('div', { style: 'margin-top:6px' }, h('div', {}, h('span', { class: 'muted' }, 'From: '), em.from || '?'), h('div', {}, h('span', { class: 'muted' }, 'Subject: '), em.subject || '(none)')) : null,
    em && em.summary ? h('div', { class: 'muted', style: 'margin-top:6px' }, em.summary) : (o.detail && !(em && em.subject) ? h('div', { class: 'muted', style: 'margin-top:6px' }, o.detail) : null),
    em && em.link ? h('a', { href: em.link, target: '_blank', rel: 'noopener noreferrer', class: 'btn sm dim', style: 'margin-top:8px;display:inline-flex;align-items:center;text-decoration:none' }, 'Open in Gmail') : null) : null;
  const body = h('div', { style: 'white-space:normal' }, label('Title'), title, label('Due'), due, label('Notes'), notes,
    d.project ? h('div', { style: 'margin-top:10px' }, h('span', { class: 'muted' }, 'Project: '), h('a', { href: '#', style: 'color:var(--acc)', onclick: (e) => { e.preventDefault(); document.querySelector('.modalbg')?.remove(); go('projects'); } }, d.project.name)) : null,
    o ? label('Where it came from') : null, origin,
    h('div', { class: 'muted', style: 'margin-top:10px;font-size:calc(12px*var(--ts,1))' }, 'Added ' + (d.created || '').slice(0, 16).replace('T', ' ') + (d.done_at ? ' · done ' + d.done_at.slice(0, 16).replace('T', ' ') : '')));
  if (!await modal('Task', body, { ok: 'Save', cancel: 'Close' })) return;
  try {
    const t = title.value.replace(/\s+/g, ' ').trim();
    if (t && t !== d.title) await post(`/api/tasks/${tk.id}/rename`, { title: t });
    if (due.value.trim() && due.value.trim() !== (d.due || '')) await post(`/api/tasks/${tk.id}/move`, { due: due.value });
    if (notes.value !== (d.notes || '')) await post(`/api/tasks/${tk.id}/notes`, { notes: notes.value });
    toast('Saved'); onChange();
  } catch (e) { toast(e.message, 1); }
}
function taskAdd(onChange, placeholder) {
  const title = h('input', { placeholder: placeholder || 'New task…', 'aria-label': 'New task' }), due = h('input', { placeholder: 'due (today)', style: 'max-width:140px;flex:none', 'aria-label': 'Due' });
  return h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); if (!title.value.trim()) return; try { await post('/api/tasks', { title: title.value, due: due.value }); onChange(); } catch (er) { toast(er.message, 1); } } },
    title, due, btn('Add', 'pur', null, 'submit'));
}
async function runRoutine(name, label) {
  toast(label + ' (can take up to a minute)…');
  try { const r = await post('/api/routine/' + name); await modal(label, r.text); await refreshSide(); VIEWS.home(); } catch (e) { toast(e.message, 1); }
}

VIEWS.home = async () => {
  setHeader('Home', [btn('＋ New chat', 'sm pur', () => newChat())]);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/home');
  const again = () => keepScroll(() => VIEWS.home());
  const ask = h('input', { placeholder: 'Ask me anything…', 'aria-label': 'Ask anything', id: 'homeask' });
  const hero = h('div', { class: 'card wide' },
    h('div', { style: 'font-size:calc(18px*var(--ts,1));color:#fff;margin-bottom:4px' }, d.date.pretty),
    h('div', { class: 'muted', style: 'margin-bottom:12px' }, `Day ${d.date.day_of_year} · Week ${d.date.week} · ${d.date.time}`),
    h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); const v = ask.value.trim(); if (!v) return; newChat(); send(v); } }, ask, btn('Ask', 'pur', null, 'submit')),
    h('div', { class: 'form', style: 'margin-top:10px' },
      btn('☀ Start my day', '', () => { newChat(); runCmd('start'); }), btn('🔄 Sync notes', 'dim', () => runRoutine('sync', 'Sync')),
      btn('🌙 Wrap up', 'dim', () => runRoutine('wrap-up', 'Wrap up')), btn('🗣 Talk', 'ok', () => { newChat(); toggleTalk(); })));
  const briefCard = h('div', { class: 'card wide' }, h('h3', {}, "Today's brief", h('a', { href: '#/brief' }, 'All briefs →')),
    d.brief ? [mdNode(d.brief.text), h('div', { class: 'form', style: 'margin-top:10px' }, btn('Discuss in chat', 'sm', () => go('#/chat/' + d.brief.chat_id)),
      btn('Regenerate', 'sm dim', async () => { try { await post('/api/briefs/generate', { force: true }); refreshSide(); again(); } catch (e) { toast(e.message, 1); } }))]
      : [h('div', { class: 'empty' }, 'No brief yet today. It is made automatically each morning; you can make it now.'),
        btn('Generate today\'s brief', '', async () => { toast('Making your brief…'); try { await post('/api/briefs/generate', {}); refreshSide(); again(); } catch (e) { toast(e.message, 1); } })]);
  const nowNext = h('div', { class: 'card' }, h('h3', {}, 'Now / next'),
    d.calendar_error ? h('div', { class: 'err' }, d.calendar_error) : [
      h('div', {}, h('span', { class: 'muted' }, 'NOW  '), d.right_now.length ? d.right_now.join(', ') : 'nothing scheduled'),
      h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted' }, 'NEXT '), d.next.length ? d.next.map((n) => `${n.time} ${n.title}`).join('  ·  ') : 'nothing else today')]);
  const inboxCard = h('div', { class: 'card' }, h('h3', {}, 'Inbox & reminders', d.inbox_unread ? h('span', { class: 'chip', style: 'color:var(--pur);border-color:var(--pur)' }, d.inbox_unread + ' new') : null, h('a', { href: '#/inbox' }, 'Open →')),
    d.inbox_items.length ? h('div', { class: 'list' }, d.inbox_items.map((i) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg);border-left:3px solid var(--pur)', onclick: () => go(i.link || '#/inbox') }, h('span', { class: 'tx' }, i.title)))) : h('div', { class: 'empty' }, 'All caught up.'),
    d.reminders.length ? h('div', { style: 'margin-top:8px' }, h('div', { class: 'muted' }, 'UPCOMING REMINDERS'), d.reminders.map((r) => h('div', {}, h('span', { class: 'time' }, r.due.slice(11, 16)), ' ', r.text))) : null,
    h('div', { style: 'margin-top:10px' }, remindForm(again)));
  const projCard = h('div', { class: 'card' }, h('h3', {}, 'Projects: next actions', h('a', { href: '#/projects' }, 'Open →')),
    d.projects.length ? h('div', { class: 'list' }, d.projects.map((p) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg)', onclick: () => go('#/projects') },
      h('span', { class: 'tx' }, h('b', {}, p.project), h('span', { class: 'muted' }, '  ' + p.next_action)), p.stalled ? h('span', { class: 'chip bad' }, 'quiet ' + p.quiet_days + 'd') : null)))
      : h('div', { class: 'empty' }, 'No project next actions yet. Open Projects to import your folders and set one next step each.'));
  const tl = [...d.overdue, ...d.tasks_today];
  const tasksCard = h('div', { class: 'card' }, h('h3', {}, 'Tasks', h('a', { href: '#/tasks' }, 'Open →')),
    tl.length ? h('div', { class: 'list' }, tl.slice(0, 8).map((x) => taskRow(x, again))) : h('div', { class: 'empty' }, 'Nothing due. Nice.'), tl.length > 8 ? h('div', { class: 'muted' }, `+${tl.length - 8} more`) : null,
    h('div', { style: 'margin-top:10px' }, taskAdd(again)));
  const calCard = h('div', { class: 'card' }, h('h3', {}, 'Calendar today', h('a', { href: '#/calendar' }, 'Week →')),
    d.calendar_error ? h('div', { class: 'err' }, d.calendar_error) : [
      d.all_day.length ? h('div', { style: 'margin-bottom:6px' }, d.all_day.map((a) => h('span', { class: 'chip' }, a))) : null,
      d.calendar_today.length ? h('div', { class: 'list' }, d.calendar_today.map((e) => h('div', { class: 'item' }, h('span', { class: 'time' }, e.time), h('span', { class: 'tx' }, e.title)))) : h('div', { class: 'empty' }, 'Nothing scheduled today.')]);
  const wf = h('div', { class: 'card' }, h('h3', {}, 'Waiting on / focus', h('a', { href: '#/notes/memory.md' }, 'Edit →')),
    d.waiting_on.length ? h('div', { class: 'list' }, d.waiting_on.slice(0, 6).map((w) => h('div', { class: 'item' }, h('span', { class: 'tx' }, w)))) : h('div', { class: 'empty' }, 'Nothing you are waiting on.'),
    d.focus.length ? h('div', { style: 'margin-top:8px' }, h('div', { class: 'muted' }, 'FOCUS'), d.focus.slice(0, 3).map((w) => h('div', {}, '• ' + w))) : null);
  const cap = h('input', { placeholder: 'Jot a thought…', 'aria-label': 'Quick capture' });
  const scratch = h('div', { class: 'card' }, h('h3', {}, 'Scratch pad', h('a', { href: '#/notes/' + encodeURIComponent('Scratch Pad.md') }, 'Open →')),
    h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); if (!cap.value.trim()) return; try { await post('/api/capture', { text: cap.value }); toast('Saved to scratch pad'); again(); } catch (er) { toast(er.message, 1); } } }, cap, btn('Add', 'pur', null, 'submit')),
    h('div', { class: 'muted', style: 'margin-top:8px' }, d.scratch.lines ? `${d.scratch.lines} unsorted note${d.scratch.lines === 1 ? '' : 's'}. Run Sync to turn them into tasks.` : 'Empty. Everything is sorted.'));
  const recent = h('div', { class: 'card' }, h('h3', {}, 'Recent chats'),
    d.chats.length ? h('div', { class: 'list' }, d.chats.map((c) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg)', onclick: () => go('#/chat/' + c.id) }, h('span', { class: 'tx' }, c.title), c.space ? h('span', { class: 'chip' }, c.space) : null))) : h('div', { class: 'empty' }, 'No chats yet.'));
  const sys = h('div', { class: 'card', id: 'syscard' }, h('h3', {}, 'System', h('a', { href: '#/settings' }, 'Settings →')), h('div', { class: 'muted' }, 'checking…'));
  const healthCard = d.health && d.health.bad && d.health.bad.length ? h('div', { class: 'card', style: 'border-color:var(--bad)' }, h('h3', {}, '⚠ Needs attention'), h('div', {}, d.health.bad.join(' · ')), h('div', { class: 'form', style: 'margin-top:10px' }, btn('See details', 'sm bad', () => go('#/settings')))) : null;
  show(h('div', { class: 'grid' }, healthCard, hero, briefCard, nowNext, inboxCard, projCard, tasksCard, calCard, wf, scratch, recent, sys));
  J('/api/status').then((s) => { const c = $('#syscard'); if (!c) return;
    c.append(h('div', { class: 'form' }, h('span', { class: 'chip ' + (s.model.where === 'GPU' ? 'ok' : '') }, 'AI: ' + (s.model.model || 'n/a') + ' · ' + (s.model.where || s.model.error || '?')),
      h('span', { class: 'chip ' + (s.google.ok ? 'ok' : 'bad') }, s.google.ok ? 'Google: connected' : 'Google: login needed'),
      h('span', { class: 'chip' }, 'Calendar writes → ' + (s.write_target === 'test' ? 'TEST calendar' : 'REAL calendar')))); c.querySelector('.muted')?.remove(); }).catch(() => {});
};

function ago(iso) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return 'just now'; if (m < 60) return m + ' min ago'; if (m < 1440) return Math.round(m / 60) + ' h ago';
  return iso.slice(0, 10) + ' ' + iso.slice(11, 16);
}
function remindForm(onDone) {
  const txt = h('input', { placeholder: 'Remind me to…', 'aria-label': 'Reminder text' }), when = h('input', { placeholder: 'in 20 minutes / at 3pm / tomorrow 9am', style: 'max-width:300px', 'aria-label': 'When' });
  return h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); if (!txt.value.trim()) return;
    try { const r = await post('/api/reminders', { text: txt.value, when: when.value }); toast('Reminder set for ' + r.due); onDone(); } catch (er) { toast(er.message, 1); } } },
    txt, when, btn('Remind', 'pur', null, 'submit'));
}
VIEWS.inbox = async () => {
  const [d, pr] = await Promise.all([J('/api/inbox'), J('/api/proposals')]);
  const again = async () => { await keepScroll(() => VIEWS.inbox()); refreshSide(); };
  setHeader('Inbox', [btn('Mark all read', 'sm dim', async () => { await post('/api/inbox/read-all'); again(); }), btn('Clear read', 'sm dim', async () => { await post('/api/inbox/clear'); again(); })]);
  const ICON = { brief: '☀', event: '▦', reminder: '⏰', nudge: '👋', sync: '🔄', wrapup: '🌙', system: '⚙' };
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Inbox'), h('span', { class: 'muted' }, 'Things I reach out to you about. Desktop pop-ups come from here too.')),
    pr.proposals.length ? h('div', { class: 'card', style: 'border-color:var(--warn);margin-bottom:14px' }, h('h3', { style: 'color:var(--warn)' }, 'Needs your OK · ' + pr.proposals.length),
      h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'Suggestions from me or from your mail. Nothing is added until you approve.'),
      h('div', { class: 'list' }, pr.proposals.map((p) => h('div', { class: 'item', style: 'align-items:flex-start' }, h('span', { style: 'width:24px' }, p.kind === 'event' ? '▦' : p.kind === 'plan' ? '🧭' : '✓'),
        h('div', { class: 'tx' }, h('div', { style: 'color:#fff' }, p.title), p.detail ? h('div', { class: 'muted md', style: 'font-size:calc(13px*var(--ts,1))' }, p.detail) : null, p.source.startsWith('email:') ? (p.email && p.email.link ? h('a', { class: 'chip', href: p.email.link, target: '_blank', rel: 'noopener noreferrer', title: [p.email.from, p.email.subject].filter(Boolean).join(' · ') }, 'from email: open it') : h('span', { class: 'chip' }, 'from email')) : null,
          p.email && (p.email.from || p.email.subject) ? h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, [p.email.from, p.email.subject].filter(Boolean).join(' · ')) : null),
        btn(p.kind === 'event' ? 'Add to calendar' : 'Approve', 'sm ok', async () => { try { await post(`/api/proposals/${p.id}/approve`); toast('Done'); again(); } catch (e) { toast(e.message, 1); } }),
        btn(p.kind === 'event' ? 'Skip' : 'Dismiss', 'sm dim', async () => { await post(`/api/proposals/${p.id}/dismiss`); again(); }))))) : null,
    h('div', { class: 'card' }, h('h3', {}, 'Reminders'), remindForm(again),
      d.reminders.length ? h('div', { class: 'list', style: 'margin-top:10px' }, d.reminders.map((r) => h('div', { class: 'item' }, h('span', { class: 'time' }, r.due.slice(5, 16).replace('T', ' ')), h('span', { class: 'tx' }, r.text),
        h('button', { class: 'x', 'aria-label': 'Cancel reminder', onclick: async () => { await del('/api/reminders/' + r.id); again(); } }, '✕')))) : h('div', { class: 'empty', style: 'margin-top:8px' }, 'No upcoming reminders. You can also just say "remind me in 20 minutes to…".')),
    h('div', { style: 'margin-top:14px' }, d.items.length ? h('div', { class: 'list' }, d.items.map((i) => h('div', { class: 'item', style: 'align-items:flex-start;cursor:pointer;' + (i.read ? 'opacity:.6' : 'border-left:3px solid var(--pur)'),
      onclick: async () => { if (!i.read) { await post(`/api/inbox/${i.id}/read`); refreshSide(); } if (i.link) go(i.link); else again(); } },
      h('span', { style: 'width:24px' }, ICON[i.kind] || '•'), h('div', { class: 'tx' }, h('div', { style: 'color:#fff' }, i.title), i.body ? h('div', { class: 'muted md' }, i.body) : null),
      h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));white-space:nowrap' }, ago(i.created)),
      h('button', { class: 'x', 'aria-label': 'Delete', onclick: async (e) => { e.stopPropagation(); await del('/api/inbox/' + i.id); again(); } }, '✕')))) : h('div', { class: 'empty' }, 'Nothing here yet. I\'ll post your daily brief, meeting reminders and nudges here.')));
};

function draftFlow(m) {
  const ins = prompt('What should the reply say? (leave empty for a neutral draft)', ''); if (ins === null) return;
  toast('Writing a draft…');
  post('/api/mail/draft', { account: m.account, id: m.id, instructions: ins }).then((r) => {
    const ta = h('textarea', { value: r.draft, style: 'width:100%;min-height:200px', 'aria-label': 'Draft reply' });
    modal('Draft reply (NOT sent)', h('div', {}, h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'To: ' + r.to + '\nSubject: ' + r.subject), ta,
      h('div', { class: 'form', style: 'margin-top:8px' }, btn('Copy', 'sm', () => { navigator.clipboard.writeText(ta.value).then(() => toast('Copied. Paste it into your email.'), () => toast('Select the text and copy it manually.', 1)); }),
        h('span', { class: 'muted' }, 'I cannot send email. You send it yourself.'))));
  }).catch((e) => toast(e.message, 1));
}
function mailRow(m, again) {
  const need = m.importance === 'high' || m.needs_reply;
  return h('div', { class: 'item', style: 'align-items:flex-start;' + (m.suspicious ? 'border-left:3px solid var(--bad)' : need ? 'border-left:3px solid var(--warn)' : '') },
    h('div', { class: 'tx' },
      h('div', {}, h('b', {}, m.sender_name), '  ', h('span', { class: 'muted' }, m.subject)),
      m.suspicious ? h('div', { style: 'color:var(--bad)' }, '⚠ Possible phishing: ' + m.reason) : [h('div', { class: 'muted' }, m.summary), m.reason ? h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, m.reason + (m.needs_reply ? ' · needs a reply' : '')) : null],
      h('div', { class: 'form', style: 'margin-top:8px' },
        h('a', { class: 'btn sm dim', href: m.link, target: '_blank', rel: 'noopener noreferrer', style: 'display:inline-flex;align-items:center;text-decoration:none' }, 'Open in Gmail'),
        m.suspicious ? null : btn('Draft reply', 'sm', () => draftFlow(m)),
        btn('Done', 'sm ok', async () => { await post('/api/mail/handled', { account: m.account, id: m.id }); again(); }))));
}
function mailTodoRow(t, again) {
  return h('div', { class: 'item' },
    h('input', { type: 'checkbox', 'aria-label': 'Done: ' + t.title, style: 'flex:none;width:auto', onchange: async () => { await post(`/api/mail/todos/${t.id}/done`, { done: true }); again(); } }),
    h('div', { class: 'tx' }, h('div', {}, t.title), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, [t.due ? 'due ' + t.due : '', t.note].filter(Boolean).join(' · ') || 'from email')),
    t.link ? h('a', { class: 'btn sm dim', href: t.link, target: '_blank', rel: 'noopener noreferrer', style: 'display:inline-flex;align-items:center;text-decoration:none' }, 'Open email') : null);
}
function organizeCard(again) {
  const box = h('div', { class: 'muted' }, 'Checking what could be filed…');
  const run = async (path, ok) => { try { const r = await post(path); toast(ok(r)); again(); } catch (e) { toast(e.message, 1); } };
  J('/api/mail/organize').then((p) => {
    if (p.error) { box.replaceChildren(p.error); return; }
    box.className = '';
    box.replaceChildren(
      p.total ? h('div', {}, p.groups.map((g) => h('div', { style: 'margin-bottom:8px' }, h('b', {}, g.label), h('span', { class: 'muted' }, ` · ${g.count}`),
        g.subjects.map((x) => h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, x))))) : h('div', { class: 'empty' }, 'Nothing waiting to be filed.'),
      h('div', { class: 'muted', style: 'margin:6px 0' }, p.note + (p.archive ? ' Filed mail will also leave the Inbox.' : ' Mail stays in the Inbox.') + (p.can_change_mail ? '' : ' Needs the one-time login that allows changing mail: run ~/assistant/login-gmail.sh.')),
      h('div', { class: 'form' }, p.total ? btn(`OK, label ${p.total} email${p.total === 1 ? '' : 's'}`, 'sm ok', () => run('/api/mail/organize/apply', (r) => `Labeled ${r.changed || 0}`)) : null,
        btn('Undo last', 'sm dim', () => run('/api/mail/organize/undo', (r) => `Undone: ${r.restored || 0}`))));
  }).catch((e) => box.replaceChildren(e.message));
  return h('div', { class: 'card', style: 'margin-top:16px' }, h('h3', {}, 'Organize'), box);
}
VIEWS.mail = async () => {
  setHeader('Mail', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/mail');
  const again = () => keepScroll(() => VIEWS.mail());
  const check = async () => { toast('Checking mail… (can take a minute)'); try { const r = await post('/api/mail/check'); toast(`${r.new} new · ${r.flagged} flagged · ${r.proposals} suggestions`); again(); refreshSide(); } catch (e) { toast(e.message, 1); } };
  if (!d.accounts.length) {
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Mail')),
      h('div', { class: 'card' }, h('h3', {}, 'Connect Gmail (read-only)'),
        h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'I can read your mail to flag what matters, find bills and deadlines, and draft replies. I can never send, delete or change anything: the only permission requested is "read".'),
        h('ol', { style: 'line-height:1.9;padding-left:20px' },
          h('li', {}, 'Open ', h('a', { href: 'https://console.cloud.google.com/apis/library/gmail.googleapis.com', target: '_blank', rel: 'noopener', style: 'color:var(--acc)' }, 'the Gmail API page'), ' (project "assistant") and click ', h('b', {}, 'Enable'), '.'),
          h('li', {}, 'Open ', h('a', { href: 'https://console.cloud.google.com/auth/scopes', target: '_blank', rel: 'noopener', style: 'color:var(--acc)' }, 'Data Access'), ', click ', h('b', {}, 'Add or remove scopes'), ', search "Gmail API", tick ', h('code', {}, 'gmail.readonly'), ', then Update and Save.'),
          h('li', {}, ...(gmailHint.startsWith('run ') ? ['In a terminal run ', h('code', {}, gmailHint.slice(4).replace(/ main$/, ''))] : [gmailHint[0].toUpperCase() + gmailHint.slice(1)]), '. A browser opens: pick your Gmail, click Advanced, Go to assistant (unsafe), and allow "View your email messages".'),
          h('li', {}, 'Come back here and press ', h('b', {}, 'Check now'), '.')),
        d.client_ready ? null : h('div', { class: 'err' }, 'Google credentials file not found (secrets/google_client.json).'),
        h('div', { class: 'form', style: 'margin-top:10px' }, btn('Check now', 'sm', check)))); return;
  }
  const need = d.items.filter((m) => !m.suspicious && (m.importance === 'high' || m.needs_reply)), fyi = d.items.filter((m) => !m.suspicious && !need.includes(m) && m.importance === 'normal'),
    low = d.items.filter((m) => !m.suspicious && m.importance === 'low'), sus = d.items.filter((m) => m.suspicious);
  const sec = (t, list, cls) => list.length ? h('div', { style: 'margin-top:16px' }, h('div', { class: 'dayhead ' + (cls || '') }, `${t} · ${list.length}`), h('div', { class: 'list' }, list.map((m) => mailRow(m, again)))) : null;
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Mail'), h('span', { class: 'muted' }, d.last_check ? 'checked ' + ago(d.last_check) : 'not checked yet'), h('div', { class: 'sp' }), btn('Check now', '', check)),
    h('div', { class: 'card' }, d.accounts.map((a) => h('div', { class: 'item' }, h('span', { class: 'tx' }, a.email), h('span', { class: 'chip ok' }, 'read-only'),
      h('button', { class: 'x', title: 'Disconnect', 'aria-label': 'Disconnect', onclick: async () => { if (!confirm('Disconnect ' + a.email + '? (It can be reconnected any time.)')) return; await del('/api/mail/accounts/' + a.name); again(); } }, '✕')))),
    d.items.length ? null : h('div', { class: 'empty', style: 'margin-top:16px' }, 'Nothing triaged yet. Press Check now.'),
    d.todos.length ? h('div', { style: 'margin-top:16px' }, h('div', { class: 'dayhead' }, `Action items from email · ${d.todos.length}`), h('div', { class: 'list' }, d.todos.map((t) => mailTodoRow(t, again)))) : null,
    d.organize ? organizeCard(again) : null,
    sec('Needs you', need, 'today'), sec('FYI', fyi), sec('Possible phishing (ignored)', sus), sec('Low priority', low));
};

VIEWS.meetings = async () => {
  clearInterval(meetTimer);
  setHeader('Meetings', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/meetings');
  const again = () => keepScroll(() => VIEWS.meetings());
  const recent = h('div', { style: 'margin-top:16px' }, h('div', { class: 'dayhead' }, 'Recent · ' + d.recent.length),
    d.recent.length ? h('div', { class: 'list' }, d.recent.map((m) => h('div', { class: 'item' }, h('div', { class: 'tx' }, h('div', {}, h('b', {}, m.title)), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, (m.started || '').replace('T', ' ').slice(0, 16) + (m.participants ? ' · ' + m.participants : ''))),
      h('span', { class: 'chip' + (m.consent === 'own_voice_only' ? '' : ' ok') }, m.consent === 'own_voice_only' ? 'my voice only' : m.consent ? 'everyone agreed' : ''), h('span', { class: 'chip' + (m.status === 'done' ? ' ok' : m.status.startsWith('failed') ? ' bad' : '') }, m.status),
      m.transcript ? h('a', { class: 'btn sm dim', href: '#/notes/' + encodeURIComponent(m.transcript), style: 'text-decoration:none;display:inline-flex;align-items:center' }, 'Open') : null))) : h('div', { class: 'empty' }, 'No recordings yet.'));
  if (d.active) {
    const a = d.active, el = h('span', {}, a.elapsed_min + ' min');
    meetTimer = setInterval(async () => { try { const x = await J('/api/meetings'); if (!x.active) { clearInterval(meetTimer); again(); } else el.textContent = x.active.elapsed_min + ' min'; } catch (e) { } }, 10000);
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Meetings')),
      h('div', { class: 'card', style: 'border:2px solid var(--bad)' }, h('h3', { style: 'color:var(--bad)' }, '● Recording now'),
        h('div', { style: 'font-size:calc(18px*var(--ts,1));color:#fff' }, a.title), h('div', { class: 'muted', style: 'margin:6px 0' }, 'Running for ', el, ' · ' + (a.tracks.includes('them') ? 'your microphone + the call audio' : 'your microphone only')),
        h('div', { class: 'muted', style: 'font-size:calc(13px*var(--ts,1))' }, 'Consent confirmed: ' + a.consent.text + (a.consent.note ? ' "' + a.consent.note + '"' : '')),
        h('div', { class: 'form', style: 'margin-top:12px' }, btn('■ Stop and transcribe', 'bad', async () => { try { await post('/api/meetings/stop'); toast('Stopped. Transcribing on this PC (about 1 minute per 6 minutes of meeting).'); again(); } catch (e) { toast(e.message, 1); } }))), recent);
    return;
  }
  const title = h('input', { placeholder: 'Meeting title (e.g. Team call)', 'aria-label': 'Title' }), who = h('input', { placeholder: 'Who is on it? (helps spell names right)', 'aria-label': 'Participants' });
  const kinds = Object.entries(d.consent_kinds), radios = kinds.map(([k, label], i) => h('label', { class: 'item', style: 'cursor:pointer;align-items:flex-start' }, h('input', { type: 'radio', name: 'ck', value: k, checked: i === 0, style: 'flex:none;width:18px;height:18px;margin-top:3px' }), h('span', { class: 'tx' }, label)));
  const sure = h('input', { type: 'checkbox', 'aria-label': 'I confirm consent', style: 'flex:none;width:20px;height:20px;margin-top:2px' });
  const note = h('input', { placeholder: 'How did everyone agree? (e.g. "said it at the start, everyone said yes")', 'aria-label': 'Consent note' });
  const startBtn = btn('● Start recording', 'bad', null, 'submit');
  const kind = () => document.querySelector('input[name=ck]:checked').value;
  const refresh = () => { const ev = kind() === 'all_parties_agreed'; note.style.display = ev ? '' : 'none'; startBtn.disabled = !sure.checked || (ev && note.value.trim().length < 5);
    sureLbl.textContent = ev ? 'I told everyone on this call that it is being recorded, and every one of them agreed. (Many places require everyone\'s consent. Recording people without it can be a crime.)' : 'I understand only my own microphone will be recorded, never the other people.'; };
  const sureLbl = h('span', { class: 'tx' }); radios.forEach((r) => r.querySelector('input').onchange = refresh); sure.onchange = refresh; note.oninput = refresh;
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Meetings'), h('span', { class: 'muted' }, 'Records Zoom, Teams, Meet, Discord, or in-person. Everything stays on this PC.')),
    h('form', { class: 'card', onsubmit: async (e) => { e.preventDefault(); try { await post('/api/meetings/start', { title: title.value || 'Meeting', participants: who.value, consent_kind: kind(), consent_note: note.value, confirmed: true }); toast('Recording started'); again(); } catch (er) { toast(er.message, 1); } } },
      h('h3', {}, 'Start a recording'), h('div', { class: 'form', style: 'flex-direction:column;align-items:stretch' }, title, who,
        h('div', { class: 'list' }, radios), note, h('label', { class: 'item', style: 'cursor:pointer;align-items:flex-start;border-color:var(--warn)' }, sure, sureLbl), startBtn),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:10px' }, 'Your mic: ' + (d.devices.mic_label || d.devices.mic || 'not found') + ' · Call sound from: ' + (d.devices.call_label || d.devices.call || 'not found') + '. Wearing a headset? Select it in your system sound settings BEFORE you start.',
        h('br'), 'Two tracks are kept apart so lines are labelled Me and Them. Audio is deleted after the transcript is made. Other sounds playing on this PC (music, notifications) are recorded as part of "Them", so mute them first. The assistant can never start a recording; only you can, here.')), recent);
  refresh();
};

let projFilter = store.get('proj_filter', 'active');
function gitChip(g) {
  if (!g) return null;
  const days = g.last_ts ? Math.round((Date.now() / 1000 - g.last_ts) / 86400) : null;
  return h('span', { class: 'chip', title: g.last_subject }, '⎇ ' + g.branch + (g.dirty ? ' · ' + g.dirty + ' uncommitted' : ' · clean') + (days != null ? ' · last commit ' + (days === 0 ? 'today' : days + 'd ago') : ''));
}
async function discoverModal(again) {
  toast('Looking for your project folders…');
  let d; try { d = await J('/api/projects/discover'); } catch (e) { toast(e.message, 1); return; }
  const boxes = d.found.map((f) => h('input', { type: 'checkbox', checked: !f.imported && (Date.now() / 1000 - f.last_ts) < 45 * 86400, disabled: f.imported, 'aria-label': f.name, style: 'flex:none;width:18px;height:18px' }));
  const rows = d.found.map((f, i) => h('label', { class: 'item', style: 'cursor:pointer' }, boxes[i], h('span', { class: 'tx' }, h('b', {}, f.name), h('span', { class: 'muted' }, '  ' + f.path.replace(/^\/home\/[^/]+/, '~') + (f.last_ts ? '  · ' + new Date(f.last_ts * 1000).toISOString().slice(0, 10) : ''))), f.imported ? h('span', { class: 'chip ok' }, 'already added') : null));
  const body = h('div', {}, h('div', { class: 'muted', style: 'margin-bottom:8px' }, `Found ${d.found.length} folders with git. Recently active ones are ticked. Recent = Active, older = Paused. Nothing in them is changed.`), h('div', { class: 'list', style: 'max-height:50vh;overflow:auto' }, rows));
  const ok = await modal('Add your existing projects', body, { ok: 'Add ticked', cancel: 'Cancel' });
  if (!ok) return;
  const paths = d.found.filter((f, i) => boxes[i].checked && !f.imported).map((f) => f.path);
  if (!paths.length) return;
  try { const r = await post('/api/projects/import', { paths }); toast(`Added ${r.imported.length} project${r.imported.length === 1 ? '' : 's'}`); again(); } catch (e) { toast(e.message, 1); }
}
VIEWS.projects = async (arg) => {
  setHeader('Projects', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  if (arg) return projectDetail(+arg);
  const d = await J('/api/projects');
  const again = () => keepScroll(() => VIEWS.projects());
  const shown = d.projects.filter((p) => projFilter === 'all' || p.status === projFilter);
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Projects'), h('div', { class: 'sp' }),
      btn('Add my existing folders', 'sm dim', () => discoverModal(again)),
      btn('＋ New project', 'pur', async () => { const n = prompt('Project name?'); if (!n) return; try { const p = await post('/api/projects', { name: n }); go('#/projects/' + p.id); } catch (e) { toast(e.message, 1); } })),
    h('div', { class: 'form', style: 'margin-bottom:14px' }, ['active', 'idea', 'paused', 'done', 'all'].map((f) => h('button', { class: 'chip', style: projFilter === f ? 'color:var(--acc);border-color:var(--acc)' : '', onclick: () => { projFilter = f; store.set('proj_filter', f); again(); } },
      f + (f === 'all' ? '' : ' · ' + d.projects.filter((p) => p.status === f).length)))),
    shown.length ? h('div', { class: 'grid' }, shown.map((p) => h('div', { class: 'card', style: 'cursor:pointer', onclick: () => go('#/projects/' + p.id) },
      h('h3', {}, p.name, h('span', { class: 'chip', style: 'margin-left:auto' }, p.status)),
      p.next_action ? h('div', {}, h('span', { class: 'muted' }, 'NEXT  '), p.next_action) : h('div', { class: 'muted' }, 'No next action yet. Open it to set one small step.'),
      h('div', { class: 'form', style: 'margin-top:10px;gap:6px' }, p.area ? h('span', { class: 'chip' }, p.area) : null, gitChip(p.stats.git),
        h('span', { class: 'chip' }, p.stats.open_tasks + ' open task' + (p.stats.open_tasks === 1 ? '' : 's')), p.stats.milestones_total ? h('span', { class: 'chip' }, p.stats.milestones_done + '/' + p.stats.milestones_total + ' milestones') : null,
        p.stats.stalled ? h('span', { class: 'chip bad' }, 'quiet ' + p.stats.quiet_days + ' days') : null)))) : h('div', { class: 'empty' }, d.projects.length ? 'No projects in this filter.' : 'No projects yet. Tap "Add my existing folders" to pull in the repos you already have, or start a new one.'));
};
async function projectDetail(pid) {
  let p; try { p = await J('/api/projects/' + pid); } catch (e) { show(h('div', { class: 'err' }, e.message)); return; }
  setHeader(p.name, [btn('← All projects', 'sm dim', () => go('#/projects'))]);
  const again = () => keepScroll(() => projectDetail(pid));
  const save = async (obj) => { try { await patch('/api/projects/' + pid, obj); toast('Saved'); } catch (e) { toast(e.message, 1); again(); } };
  const status = h('select', { 'aria-label': 'Status', onchange: (e) => save({ status: e.target.value }) }, ['idea', 'active', 'paused', 'done'].map((s) => h('option', { value: s, selected: s === p.status }, s)));
  const area = h('input', { value: p.area, placeholder: 'area (Business, Home…)', style: 'max-width:200px', 'aria-label': 'Area', onchange: (e) => save({ area: e.target.value }) });
  const goal = h('textarea', { value: p.goal, placeholder: 'Goal: what is this project for, in one or two sentences?', style: 'width:100%;min-height:70px', 'aria-label': 'Goal', onchange: (e) => save({ goal: e.target.value }) });
  const fin = h('input', { value: p.finish_line, placeholder: 'Done when… (how will you know it is finished?)', 'aria-label': 'Finish line', style: 'width:100%', onchange: (e) => save({ finish_line: e.target.value }) });
  const nxt = h('input', { value: p.next_action, placeholder: 'ONE small next step (under 12 words)', 'aria-label': 'Next action', style: 'border-color:var(--acc);width:100%', onchange: (e) => save({ next_action: e.target.value }) });
  const folder = h('input', { value: p.folder, placeholder: '/home/you/project-folder', 'aria-label': 'Folder', style: 'width:100%', onchange: (e) => save({ folder: e.target.value }).then(again) });
  const msT = h('input', { placeholder: 'New milestone (a visible result)', 'aria-label': 'Milestone' }), msD = h('input', { placeholder: 'due', style: 'max-width:120px;flex:none', 'aria-label': 'Milestone due' });
  const tkT = h('input', { placeholder: 'Add a task to this project', 'aria-label': 'Project task' }), tkD = h('input', { placeholder: 'due', style: 'max-width:120px;flex:none', 'aria-label': 'Task due' });
  const guide = h('input', { placeholder: 'Anything it should know? (time per week, deadline, constraints)', 'aria-label': 'Planning guidance' });
  const agentSel = h('select', { 'aria-label': 'Agent' }, ['claude', 'codex', 'gemini', 'opencode'].map((a) => h('option', { value: a }, a))), agentTask = h('input', { placeholder: 'What should the agent do here?', 'aria-label': 'Agent task' });
  const open = p.tasks.filter((t) => !t.done), done = p.tasks.filter((t) => t.done);
  show(h('div', { class: 'pagehead' }, h('h2', {}, p.name), status, area, h('div', { class: 'sp' }), p.stats.stalled ? h('span', { class: 'chip bad' }, 'quiet ' + p.stats.quiet_days + ' days') : null),
    h('div', { class: 'grid' },
      h('div', { class: 'card wide' }, h('h3', {}, 'Next action'), nxt, h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Just one. When you finish it, write the next one. This is what shows on Home and in your morning brief.')),
      h('div', { class: 'card' }, h('h3', {}, 'Goal & finish line'), goal, h('div', { style: 'margin-top:8px' }, fin)),
      h('div', { class: 'card' }, h('h3', {}, 'Plan with AI'), h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'Drafts milestones and small tasks from your goal, honest about risks. It goes to your Inbox; nothing is created until you approve it.'),
        h('div', { class: 'form' }, guide, btn('Draft a plan', 'pur', async () => { toast('Drafting a plan… (about 15 seconds)'); try { const r = await post(`/api/projects/${pid}/plan`, { guidance: guide.value });
          await modal('Plan drafted', h('div', {}, h('div', {}, h('b', {}, 'First step: '), r.next_action), h('div', { style: 'margin-top:8px' }, h('b', {}, 'Milestones')), r.milestones.map((m) => h('div', {}, '• ' + m)), h('div', { style: 'margin-top:8px' }, h('b', {}, 'Risks')), r.risks.map((m) => h('div', {}, '• ' + m)),
            h('div', { class: 'muted', style: 'margin-top:10px' }, 'It is waiting in your Inbox under "Needs your OK".')), { ok: 'Open Inbox' }); refreshSide(); go('#/inbox'); } catch (e) { toast(e.message, 1); } }))),
      h('div', { class: 'card' }, h('h3', {}, 'Milestones · ' + p.stats.milestones_done + '/' + p.stats.milestones_total),
        p.milestones.length ? h('div', { class: 'list' }, p.milestones.map((m) => h('div', { class: 'item' + (m.done ? ' done' : '') },
          h('button', { class: 'check' + (m.done ? ' on' : ''), 'aria-label': 'Toggle milestone', onclick: async () => { await post(`/api/milestones/${m.id}/toggle`); again(); } }, m.done ? '✓' : ''), h('span', { class: 'tx' }, m.title), m.due ? h('span', { class: 'chip' }, m.due) : null,
          h('button', { class: 'x', 'aria-label': 'Delete milestone', onclick: async () => { await del('/api/milestones/' + m.id); again(); } }, '✕')))) : h('div', { class: 'empty' }, 'None yet.'),
        h('form', { class: 'form', style: 'margin-top:10px', onsubmit: async (e) => { e.preventDefault(); if (!msT.value.trim()) return; await post(`/api/projects/${pid}/milestones`, { title: msT.value, due: msD.value }); again(); } }, msT, msD, btn('Add', 'pur', null, 'submit'))),
      h('div', { class: 'card' }, h('h3', {}, 'Tasks · ' + open.length + ' open' + (done.length ? ', ' + done.length + ' done' : '')),
        open.length ? h('div', { class: 'list' }, open.map((t) => taskRow(t, again))) : h('div', { class: 'empty' }, 'No open tasks.'),
        h('form', { class: 'form', style: 'margin-top:10px', onsubmit: async (e) => { e.preventDefault(); if (!tkT.value.trim()) return; try { await post(`/api/projects/${pid}/tasks`, { title: tkT.value, due: tkD.value }); again(); } catch (er) { toast(er.message, 1); } } }, tkT, tkD, btn('Add', 'pur', null, 'submit')),
        done.length ? h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Show done'), h('div', { class: 'list', style: 'margin-top:6px' }, done.map((t) => taskRow(t, again)))) : null),
      h('div', { class: 'card' }, h('h3', {}, 'Folder & agents'), folder,
        h('div', { class: 'form', style: 'margin-top:8px' }, gitChip(p.stats.git), p.folder ? btn('Open folder', 'sm dim', async () => { const r = await post(`/api/projects/${pid}/open`); toast(r.error || 'Opened on your desktop'); }) : null),
        p.folder ? h('div', { style: 'margin-top:10px' }, h('div', { class: 'muted', style: 'margin-bottom:6px' }, 'Launch an AI agent in this folder (it asks its own permissions; you answer them in the Terminal):'),
          h('div', { class: 'form' }, agentSel, agentTask, btn('Launch', 'ok', async () => { try { const r = await post('/api/terminals', { kind: 'agent', agent: agentSel.value, folder: p.folder, task: agentTask.value }); go('#/terminal/' + r.name); } catch (e) { toast(e.message, 1); } }))) : null,
        p.sessions.length ? h('div', { style: 'margin-top:10px' }, h('div', { class: 'muted' }, 'RUNNING HERE'), p.sessions.map((s) => h('div', {}, h('a', { href: '#/terminal/' + s.name, style: 'color:var(--acc)' }, s.name), ' ', h('span', { class: 'muted' }, s.agent || 'shell')))) : null),
      h('div', { class: 'card' }, h('h3', {}, 'Notes'), p.notes_path ? h('div', {}, h('a', { href: '#/notes/' + encodeURIComponent(p.notes_path), style: 'color:var(--acc)' }, 'Open the project notes file →'), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Plain text in your vault. The assistant can read and add to it.')) : h('div', { class: 'empty' }, 'No notes file yet.'),
        h('div', { style: 'margin-top:14px' }, btn('Delete project', 'sm bad', async () => { if (!confirm('Delete "' + p.name + '"? Its tasks stay in your task list; the notes file and folder are not touched.')) return; await del('/api/projects/' + pid); go('#/projects'); })))));
}

let xtermReady = null, term = null, termWs = null, termFit = null, termRO = null;
function loadXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  if (xtermReady) return xtermReady;
  xtermReady = new Promise((res, rej) => {
    document.head.append(h('link', { rel: 'stylesheet', href: '/vendor/xterm/xterm.css' }));
    const s1 = h('script', { src: '/vendor/xterm/xterm.js' });
    s1.onload = () => { const s2 = h('script', { src: '/vendor/xterm/addon-fit.js' }); s2.onload = res; s2.onerror = rej; document.head.append(s2); };
    s1.onerror = rej; document.head.append(s1);
  });
  return xtermReady;
}
function closeTerm() { try { termRO && termRO.disconnect(); } catch (e) { } try { termWs && termWs.close(); } catch (e) { } try { term && term.dispose(); } catch (e) { } term = termWs = termFit = termRO = null; }
const QUICK = [['Esc', '\x1b'], ['Tab', '\t'], ['↑', '\x1b[A'], ['↓', '\x1b[B'], ['←', '\x1b[D'], ['→', '\x1b[C'], ['Ctrl+C', '\x03'], ['Enter', '\r'], ['y', 'y'], ['n', 'n']];
async function openTerminal(name, pane) {
  const status = h('span', { class: 'chip' }, 'connecting…'), host = h('div', { id: 'termhost' });
  const send = (str) => { if (termWs && termWs.readyState === 1) termWs.send(new TextEncoder().encode(str)); if (term) term.focus(); };
  pane.replaceChildren(
    h('div', { class: 'bar2 form', style: 'margin-bottom:8px' }, btn('← Sessions', 'sm dim', () => go('#/terminal')), h('b', { style: 'color:var(--acc)' }, name), status,
      h('div', { class: 'sp', style: 'flex:1' }),
      btn('Summarize', 'sm', async () => { toast('Summarizing…'); try { const r = await post(`/api/terminals/${name}/summary`); modal('Summary of ' + name, r.summary); } catch (e) { toast(e.message, 1); } }),
      btn('Stop', 'sm bad', async () => { if (!confirm('Stop this session and anything running in it?')) return; await del('/api/terminals/' + name); go('#/terminal'); })),
    host,
    h('div', { class: 'quick' }, QUICK.map(([l, seq]) => h('button', { class: 'btn sm dim', type: 'button', onmousedown: (e) => e.preventDefault(), onclick: () => send(seq) }, l)),
      h('button', { class: 'btn sm dim', type: 'button', onclick: async () => { try { send(await navigator.clipboard.readText()); } catch (e) { toast('Allow clipboard access to paste.', 1); } } }, 'Paste')),
    h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'This session keeps running when you leave this page. If an agent asks for permission, answer it here.'));
  try { await loadXterm(); } catch (e) { status.textContent = 'failed to load terminal'; return; }
  term = new window.Terminal({ cursorBlink: true, fontSize: Math.round(14 * look().text / 100), scrollback: 5000, fontFamily: 'ui-monospace, "DejaVu Sans Mono", Menlo, Consolas, monospace',
    theme: { background: '#000000', foreground: '#E8E8F0', cursor: '#00F0FF', selectionBackground: '#9D00FF66', black: '#000000', brightBlack: '#4A607A' } });
  termFit = new window.FitAddon.FitAddon(); term.loadAddon(termFit); term.open(host); termFit.fit();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  termWs = new WebSocket(`${proto}://${location.host}/ws/term/${encodeURIComponent(name)}`); termWs.binaryType = 'arraybuffer';
  const sendSize = () => { if (termWs && termWs.readyState === 1 && term) termWs.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); };
  termWs.onopen = () => { status.textContent = 'connected'; status.className = 'chip ok'; termFit.fit(); sendSize(); term.focus(); };
  termWs.onmessage = (e) => term && term.write(new Uint8Array(e.data));
  termWs.onclose = (e) => { status.textContent = e.code === 4404 ? 'session ended' : 'disconnected'; status.className = 'chip bad'; if (term) term.write('\r\n\x1b[90m[' + status.textContent + ']\x1b[0m\r\n'); };
  term.onData((d) => send(d)); term.onResize(sendSize);
  let t = null; termRO = new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => { try { termFit.fit(); } catch (e) { } }, 120); }); termRO.observe(host);
}
VIEWS.terminal = async (arg) => {
  closeTerm();
  setHeader('Terminal', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/terminals');
  if (!d.available) { show(h('div', { class: 'card' }, h('h3', {}, 'Terminal needs tmux'), h('div', {}, 'tmux keeps sessions alive after you close the page. Install it once with ', h('code', {}, 'sudo apt install tmux'), ' in a normal terminal, then reload.'))); return; }
  const again = () => keepScroll(() => VIEWS.terminal(arg));
  const kind = h('select', { 'aria-label': 'Type' }, h('option', { value: 'shell' }, 'Shell (plain terminal)'), d.agents.map((a) => h('option', { value: a.id, disabled: !a.installed }, a.id + (a.installed ? '' : ' (not installed)'))));
  const folder = h('input', { value: '~/assistant', list: 'folders', 'aria-label': 'Folder' }), task = h('textarea', { placeholder: 'What should the agent do? (optional)', style: 'width:100%;min-height:70px', 'aria-label': 'Task' });
  const cmd = h('input', { placeholder: 'Command to run (optional)', 'aria-label': 'Command' });
  const sync = () => { const sh = kind.value === 'shell'; task.style.display = sh ? 'none' : ''; cmd.style.display = sh ? '' : 'none'; }; kind.onchange = sync;
  const newForm = h('form', { class: 'card', onsubmit: async (e) => { e.preventDefault(); try {
    const r = await post('/api/terminals', kind.value === 'shell' ? { kind: 'shell', folder: folder.value, command: cmd.value } : { kind: 'agent', agent: kind.value, folder: folder.value, task: task.value });
    go('#/terminal/' + r.name); } catch (er) { toast(er.message, 1); } } },
    h('h3', {}, 'New session'), h('div', { class: 'form', style: 'flex-direction:column;align-items:stretch' }, kind, folder, h('datalist', { id: 'folders' }, d.folders.map((f) => h('option', { value: f }))), task, cmd,
      btn('Start', 'ok', null, 'submit')), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:8px' }, 'Agents ask their own permission questions inside the session; you answer them here. I never start an agent in "skip permissions" mode.'));
  sync();
  const list = h('div', { class: 'list' }, d.sessions.length ? d.sessions.map((s) => h('div', { class: 'item', style: 'cursor:pointer' + (s.name === arg ? ';border-color:var(--acc)' : ''), onclick: () => go('#/terminal/' + s.name) },
    h('div', { class: 'tx' }, h('div', {}, h('b', {}, s.name), ' ', s.kind === 'agent' ? h('span', { class: 'chip', style: 'color:var(--pur);border-color:var(--pur)' }, s.agent) : null), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, s.folder.replace(/^\/home\/[^/]+/, '~') + ' · ' + (s.idle ? 'idle' : 'running ' + s.running))),
    h('button', { class: 'x', 'aria-label': 'Stop ' + s.name, onclick: async (e) => { e.stopPropagation(); if (!confirm('Stop ' + s.name + '?')) return; await del('/api/terminals/' + s.name); if (s.name === arg) go('#/terminal'); else again(); } }, '✕'))) : h('div', { class: 'empty' }, 'No sessions yet.'));
  const pane = h('div', { class: 'tpane' }, arg ? null : h('div', { class: 'card' }, h('div', { class: 'empty' }, 'Start a session on the left, or open one. Sessions keep running when you leave this page, and I can start agents for you too ("launch claude in my android project to fix …").')));
  show(h('div', { class: 'termpage' + (arg ? ' open' : '') }, h('div', { class: 'tl' }, newForm, h('div', { style: 'margin-top:14px' }, h('div', { class: 'dayhead' }, 'Sessions · ' + d.sessions.length), list)), pane));
  if (arg) { if (!d.sessions.some((s) => s.name === arg)) { pane.replaceChildren(h('div', { class: 'err' }, 'That session has ended.')); return; } await openTerminal(arg, pane); }
};

VIEWS.brief = async () => {
  setHeader('Daily Brief', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/briefs');
  const again = () => keepScroll(() => VIEWS.brief());
  const todays = d.briefs.find((b) => b.day === d.today);
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Daily Brief'), h('span', { class: 'muted' }, 'made automatically at ' + d.time + ' · built from your real calendar and tasks'), h('div', { class: 'sp' }),
    btn(todays ? 'Regenerate today' : 'Generate today', '', async () => { toast('Making your brief…'); try { await post('/api/briefs/generate', { force: !!todays }); refreshSide(); again(); } catch (e) { toast(e.message, 1); } })),
    d.briefs.length ? d.briefs.map((b) => h('div', { class: 'card', style: 'margin-bottom:14px' }, mdNode(b.text), h('div', { class: 'form', style: 'margin-top:10px' }, btn('Discuss in chat', 'sm', () => go('#/chat/' + b.chat_id))))) : h('div', { class: 'empty' }, 'No briefs yet.'));
};

VIEWS.tasks = async () => {
  setHeader('Tasks', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/tasks');
  const again = async () => { await keepScroll(() => VIEWS.tasks()); refreshSide(); };
  const sec = (title, list, cls) => list.length ? h('div', { style: 'margin-top:16px' }, h('div', { class: 'dayhead ' + (cls || '') }, `${title} · ${list.length}`), h('div', { class: 'list' }, list.map((x) => taskRow(x, again)))) : null;
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Tasks'), h('span', { class: 'muted' }, 'Tip: tap a task for its details and notes; tap the date to move it')),
    h('div', { class: 'card' }, taskAdd(again, 'Add a task… (due can be: tomorrow, friday, 2026-10-09)')),
    sec('Overdue', d.overdue, 'today'), sec('Today', d.due_today), sec('Upcoming', d.upcoming), sec('Done recently', d.done_recent),
    !(d.overdue.length + d.due_today.length + d.upcoming.length) ? h('div', { class: 'empty', style: 'margin-top:16px' }, 'Nothing open. Add one above, or just tell the assistant.') : null);
};

const STUDY_RATE = [[1, 'Again', 'bad'], [2, 'Hard', 'dim'], [3, 'Good', 'ok'], [4, 'Easy', 'pur']];
let walk = null;
const studyScope = (arg) => { const m = /^(?:review|walk)\/([cd])\/(\d+)/.exec(arg || ''); return m ? (m[1] === 'c' ? 'course=' : 'deck=') + m[2] : ''; };
async function studyXp() {
  try { const r = await post('/api/study/xp'); if (r.award) toast('+' + r.award.xp + ' XP to ' + r.award.stat + (r.award.level_up ? ' · level up!' : '')); } catch (e) { }
}
VIEWS.study = async (arg) => {
  if (/^review/.test(arg || '')) return studyReview(arg);
  if (/^walk/.test(arg || '')) return studyWalk(arg);
  if (/^deck\/\d+/.test(arg || '')) return studyDeck(+arg.split('/')[1]);
  if (/^test\/\d+/.test(arg || '')) return studyTest(+arg.split('/')[1]);
  if (/^tutor\/\d+/.test(arg || '')) return studyTutor(+arg.split('/')[1]);
  if (/^map\/\d+/.test(arg || '')) return studyMap(+arg.split('/')[1]);
  setHeader('Study', [btn('▶ Review', 'sm pur', () => go('#/study/review')), btn('🚶 Walk', 'sm', () => go('#/study/walk'))]);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/study');
  const again = async () => { await keepScroll(() => VIEWS.study()); refreshSide(); };
  const act = async (f, msg) => { try { await f(); if (msg) toast(msg); again(); } catch (e) { toast(e.message, 1); } };
  const decks = d.courses.flatMap((c) => c.decks.map((k) => ({ ...k, course: c.name })));
  const printBtn = (q) => h('a', { class: 'btn sm dim', href: '/api/study/print?' + q, target: '_blank', rel: 'noopener', style: 'display:inline-flex;align-items:center;text-decoration:none' }, 'Print');
  const courseCard = (c) => h('div', { class: 'card', style: 'margin-top:14px' },
    h('div', { style: 'display:flex;align-items:center;gap:8px;flex-wrap:wrap' }, h('h3', { style: 'margin:0;flex:1' }, c.name),
      h('button', { class: 'chip' + (c.days_to_exam != null && c.days_to_exam <= 3 && c.days_to_exam >= 0 ? ' bad' : ''), title: 'Set the exam day', onclick: () => {
        const v = prompt('Exam day (like 2026-10-26), or empty for none:', c.exam_day || ''); if (v === null) return;
        act(() => post(`/api/study/courses/${c.id}/exam`, { day: v.trim() }), 'Saved'); } },
        c.exam_day ? (c.days_to_exam > 0 ? `exam in ${c.days_to_exam} days` : c.days_to_exam === 0 ? 'exam today' : 'exam ' + c.exam_day) : '+ exam day'),
      h('span', { class: 'muted' }, `${c.due} due · ${c.cards} cards`)),
    h('div', { class: 'list', style: 'margin-top:8px' }, c.decks.map((k) => h('div', { class: 'item', style: 'flex-wrap:wrap' },
      h('div', { class: 'tx', role: 'button', tabindex: '0', style: 'cursor:pointer', onclick: () => go('#/study/deck/' + k.id) },
        h('div', { style: 'color:#fff' }, k.name), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' },
          [k.chapters, `${k.due} due of ${k.cards}`, k.drafts ? `${k.drafts} drafts to check` : ''].filter(Boolean).join(' · '))),
      btn('Review', 'sm ' + (k.due ? 'pur' : 'dim'), () => go('#/study/review/d/' + k.id)), btn('Walk', 'sm dim', () => go('#/study/walk/d/' + k.id)), btn('Download audio', 'sm dim', () => studyDownload('/api/study/audio?deck=' + k.id, 'study-audio.wav', 'Rosie is recording the deck…')),
      printBtn('deck=' + k.id), btn('Export', 'sm dim', () => studyDownload('/api/study/anki/export?deck=' + k.id, 'anki-deck.txt')), btn('Import', 'sm dim', () => studyAnkiImport(k.id, again))))),
    c.decks.length ? null : h('div', { class: 'empty' }, 'No decks yet: add one below or import cards.'),
    h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px' },
      btn('Review all', 'sm ' + (c.due ? 'pur' : 'dim'), () => go('#/study/review/c/' + c.id)), btn('Practice test', 'sm', () => go('#/study/test/' + c.id)),
      btn('Tutor', 'sm', () => go('#/study/tutor/' + c.id)), btn('Map', 'sm', () => go('#/study/map/' + c.id)), btn('Walk all', 'sm dim', () => go('#/study/walk/c/' + c.id)), printBtn('course=' + c.id),
      btn('Export for Anki', 'sm dim', () => studyDownload('/api/study/anki/export?course=' + c.id, 'anki-course.txt')),
      btn('Download audio', 'sm dim', () => studyDownload('/api/study/audio?course=' + c.id, 'study-audio.wav', 'Rosie is recording the cards that are due…')),
      btn('Save a copy', 'sm dim', async () => { const x = await J('/api/study/export/' + c.id); const a = h('a', { href: URL.createObjectURL(new Blob([JSON.stringify(x, null, 1)], { type: 'application/json' })), download: c.name.replace(/[^\w-]+/g, '_') + '-cards.json' }); a.click(); }),
      btn('Delete course', 'sm dim', () => { if (confirm(`Delete ${c.name} and all its cards and review history?`)) act(() => del('/api/study/courses/' + c.id), 'Deleted'); })),
    c.tests.length ? h('div', { class: 'muted', style: 'margin-top:8px' }, 'Recent tests: ' + c.tests.map((t) => `${t.score}/${t.total}`).join(', ')) : null,
    c.ideas.length ? h('div', { style: 'margin-top:10px' }, h('div', {}, `Ideas from your phone · ${c.ideas.length}`),
      h('div', { class: 'list', style: 'margin-top:6px' }, c.ideas.map((i) => h('div', { class: 'item' }, h('div', { class: 'tx' }, i.text),
        h('button', { class: 'x', 'aria-label': 'Delete idea', onclick: () => act(() => del('/api/study/ideas/' + i.id)) }, '✕')))),
      c.decks.length ? (() => { const sel = h('select', { 'aria-label': 'Deck for the cards' }, c.decks.map((k) => h('option', { value: k.id }, k.name)));
        return h('div', { class: 'form', style: 'margin-top:6px' }, sel, btn('Make cards from these ideas', 'sm pur', async (e) => { e.target.disabled = true; toast('Rosie is reading your ideas…');
          try { const r = await post(`/api/study/decks/${sel.value}/from-ideas`); toast(`${r.drafted} drafts ready` + (r.dropped ? `, ${r.dropped} dropped by the check` : '')); again(); } catch (er) { toast(er.message, 1); e.target.disabled = false; } })); })() : null) : null,
    c.notes ? h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Study tips'), h('div', { class: 'muted', style: 'white-space:pre-wrap;margin-top:6px' }, c.notes)) : null);
  const deckSel = h('select', { 'aria-label': 'Deck' }, decks.map((k) => h('option', { value: k.id }, `${k.course}: ${k.name}`)));
  const notes = h('textarea', { rows: 6, placeholder: 'Paste your class notes or a chapter section here (or pick a .txt or .md file).', 'aria-label': 'Notes', style: 'width:100%;resize:vertical' });
  const file = h('input', { type: 'file', accept: '.txt,.md,text/plain,text/markdown', 'aria-label': 'Notes file', onchange: async () => { if (file.files[0]) notes.value = (await file.files[0].text()).slice(0, 12000); } });
  const how = h('input', { type: 'number', value: 10, min: 1, max: 15, 'aria-label': 'How many cards', style: 'max-width:80px' });
  const draftBox = decks.length ? h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Make cards from your notes'),
    h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'Rosie drafts cards from your own notes. Each one quotes the line it came from, and a card whose answer isn\'t in your notes is dropped. You keep the good ones.'),
    h('div', { class: 'form' }, deckSel, how), notes, h('div', { class: 'form', style: 'margin-top:8px' }, file,
      btn('Draft cards', 'pur', async (e) => { const b = e.target; b.disabled = true; toast('Rosie is reading your notes (up to a minute)…');
        try { const r = await post('/api/study/draft', { deck_id: +deckSel.value, notes: notes.value, n: +how.value || 10 }); toast(`${r.drafted} drafts ready` + (r.dropped ? `, ${r.dropped} dropped by the check` : '')); notes.value = ''; again(); }
        catch (er) { toast(er.message, 1); } finally { b.disabled = false; } }))) : null;
  const drafts = d.drafts.length ? h('div', { class: 'card', style: 'margin-top:14px;border-color:var(--warn)' }, h('h3', { style: 'color:var(--warn)' }, 'Drafts to check · ' + d.drafts.length),
    h('div', { class: 'list' }, d.drafts.map((x) => h('div', { class: 'item', style: 'align-items:flex-start;flex-wrap:wrap' }, h('div', { class: 'tx' },
      h('div', { style: 'color:#fff' }, x.q), h('div', {}, x.a), x.clue ? h('div', { class: 'muted', style: 'font-style:italic' }, x.clue) : null,
      x.quote ? h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'From your notes: “' + x.quote + '”') : null, h('span', { class: 'chip' }, x.deck)),
      btn('Keep', 'sm ok', () => act(() => post(`/api/study/cards/${x.id}/keep`), 'Kept')), btn('Edit', 'sm dim', () => studyEdit(x, again)),
      btn('Drop', 'sm dim', () => act(() => del('/api/study/cards/' + x.id))))))) : null;
  const cname = h('input', { placeholder: 'New course, like BIOL 100', 'aria-label': 'Course name' });
  const dcourse = h('select', { 'aria-label': 'Course' }, d.courses.map((c) => h('option', { value: c.id }, c.name)));
  const dname = h('input', { placeholder: 'New deck, like Ecology', 'aria-label': 'Deck name' }), dch = h('input', { placeholder: 'chapters (optional)', 'aria-label': 'Chapters', style: 'max-width:160px' });
  const icourse = h('input', { placeholder: 'Course for the imported cards', 'aria-label': 'Course for import' });
  const ifile = h('input', { type: 'file', accept: '.json,application/json', 'aria-label': 'Cards file' });
  const addBox = h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Add'),
    h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); if (cname.value.trim()) act(() => post('/api/study/courses', { name: cname.value }), 'Course added'); } }, cname, btn('Add course', 'pur', null, 'submit')),
    d.courses.length ? h('form', { class: 'form', style: 'margin-top:8px', onsubmit: (e) => { e.preventDefault(); if (dname.value.trim()) act(() => post('/api/study/decks', { course_id: +dcourse.value, name: dname.value, chapters: dch.value }), 'Deck added'); } }, dcourse, dname, dch, btn('Add deck', 'pur', null, 'submit')) : null,
    h('div', { class: 'muted', style: 'margin:12px 0 6px' }, 'Import a cards file (the cards.json format: decks, cards with q, a and a clue t, and tips). Cards already there are skipped.'),
    h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); if (!ifile.files[0] || !icourse.value.trim()) return toast('Pick a file and name the course', 1);
      let data; try { data = JSON.parse(await ifile.files[0].text()); } catch (er) { return toast('That file is not JSON', 1); }
      act(async () => { const r = await post('/api/study/import', { course: icourse.value, data }); toast(`${r.added} cards added in ${r.decks} decks` + (r.skipped ? `, ${r.skipped} already there` : '')); }); } },
      icourse, ifile, btn('Import', 'pur', null, 'submit')));
  const ideaCourse = h('select', { 'aria-label': 'Course for the idea' }, d.courses.map((c) => h('option', { value: c.id }, c.name)));
  const ideaText = h('input', { placeholder: 'Jot an idea, a term, or something from class…', 'aria-label': 'Idea' });
  const ideaBox = d.courses.length ? h('div', { class: 'card', style: 'margin-top:12px' }, h('h3', {}, 'Quick idea'),
    h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); if (!ideaText.value.trim()) return; act(() => post('/api/study/ideas', { course_id: +ideaCourse.value, text: ideaText.value }), 'Saved for later'); } },
      ideaCourse, ideaText, btn('Save', 'pur', null, 'submit')),
    h('div', { class: 'muted', style: 'margin-top:6px;font-size:calc(12px*var(--ts,1))' }, 'Good for your phone. Later, on your PC, turn a course\'s ideas into flash cards.')) : null;
  const blockBox = h('div');
  J('/api/study/next-block').then((r) => { const b = r.block; if (!b) return;
    const t = (x) => new Date(x).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), day = new Date(b.start).toDateString() === new Date().toDateString() ? 'Today' : 'Tomorrow';
    blockBox.replaceChildren(h('div', { class: 'card', style: 'margin-top:12px' }, h('h3', {}, 'Next study block'),
      h('div', {}, `${day} ${t(b.start)} to ${t(b.end)} · ${b.title}`),
      b.todo.length ? h('ul', { style: 'margin:6px 0 0;padding-left:20px' }, b.todo.map((x) => h('li', {}, x[0].toUpperCase() + x.slice(1)))) : h('div', { class: 'muted' }, 'Nothing due: a good time for new notes or ideas.'))); }).catch(() => {});
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Study'), h('span', { class: 'muted' },
    d.courses.length ? `${d.due} cards due now · reviewed today: ${d.reviewed_today}` + (d.reviewed_today ? ` (${d.right_today} right)` : '') + '. Cards come back just before you would forget them.' : 'Flash cards that come back just before you would forget them.')),
    d.courses.length ? null : h('div', { class: 'empty', style: 'margin-top:12px' }, 'Start by adding a course, or import a cards file.'),
    window.matchMedia('(max-width:599px)').matches ? ideaBox : null, blockBox, drafts, ...d.courses.map(courseCard), draftBox,
    window.matchMedia('(max-width:599px)').matches ? null : ideaBox, addBox);
};
function studyEdit(x, onDone) {
  const q = h('textarea', { rows: 2, 'aria-label': 'Question', style: 'width:100%' }), a = h('textarea', { rows: 3, 'aria-label': 'Answer', style: 'width:100%' }), t = h('textarea', { rows: 2, 'aria-label': 'Clue', style: 'width:100%' });
  q.value = x.q; a.value = x.a; t.value = x.clue || '';
  const lab = (s) => h('div', { class: 'muted', style: 'margin:8px 0 4px' }, s);
  return modal('Card', h('div', { style: 'white-space:normal' }, lab('Question'), q, lab('Answer'), a, lab('Memory clue (optional)'), t), { ok: 'Save', cancel: 'Close' }).then(async (ok) => {
    if (!ok) return; try { await put('/api/study/cards/' + x.id, { q: q.value, a: a.value, clue: t.value }); toast('Saved'); onDone(); } catch (e) { toast(e.message, 1); } });
}
async function studyDeck(id) {
  const d = await J('/api/study/decks/' + id);
  const again = () => keepScroll(() => studyDeck(id));
  setHeader(d.deck.name, [btn('← Study', 'sm dim', () => go('#/study')), btn('▶ Review', 'sm pur', () => go('#/study/review/d/' + id))]);
  const q = h('input', { placeholder: 'Question', 'aria-label': 'Question' }), a = h('input', { placeholder: 'Answer', 'aria-label': 'Answer' }), t = h('input', { placeholder: 'Clue (optional)', 'aria-label': 'Clue' });
  show(h('div', { class: 'pagehead' }, h('h2', {}, d.deck.name), h('span', { class: 'muted' }, `${d.deck.course}${d.deck.chapters ? ' · ' + d.deck.chapters : ''} · ${d.cards.length} cards. Tap a card to change it.`)),
    h('div', { class: 'card' }, h('form', { class: 'form', style: 'flex-wrap:wrap', onsubmit: async (e) => { e.preventDefault(); if (!q.value.trim() || !a.value.trim()) return;
      try { await post('/api/study/cards', { deck_id: id, q: q.value, a: a.value, clue: t.value }); again(); } catch (er) { toast(er.message, 1); } } }, q, a, t, btn('Add card', 'pur', null, 'submit'))),
    h('div', { class: 'list', style: 'margin-top:14px' }, d.cards.map((x) => h('div', { class: 'item', style: 'align-items:flex-start' },
      h('div', { class: 'tx', role: 'button', tabindex: '0', style: 'cursor:pointer', onclick: () => studyEdit(x, again) }, h('div', { style: 'color:#fff' }, x.q), h('div', { class: 'muted' }, x.a),
        x.status === 'draft' ? h('span', { class: 'chip' }, 'draft') : null),
      h('button', { class: 'x', 'aria-label': 'Delete card', onclick: async () => { if (!confirm('Delete this card?')) return; await del('/api/study/cards/' + x.id); again(); } }, '✕')))));
}
async function studyReview(arg) {
  const scope = studyScope(arg);
  const cards = (await J('/api/study/due?limit=200&' + scope)).cards;
  let i = 0, right = 0, done = 0, shown = false;
  const finish = async () => { document.onkeydown = null; await studyXp(); refreshSide(); go('#/study'); };
  setHeader('Review', [btn('Done', 'sm dim', finish)]);
  const box = h('div', { class: 'card', style: 'min-height:260px;display:flex;flex-direction:column;justify-content:center;text-align:center;padding:24px' });
  const bar = h('div', { class: 'muted', style: 'margin-bottom:10px' });
  const draw = () => {
    if (i >= cards.length) {
      document.onkeydown = null;
      bar.textContent = '';
      box.replaceChildren(h('h2', {}, cards.length ? 'Done for now' : 'Nothing due'), h('div', { class: 'muted', style: 'margin:8px 0 16px' },
        cards.length ? `${done} cards, ${right} remembered. The ones you missed come back soon.` : 'Every card is resting until it is due again. Come back later, or study with Walk.'), btn('Back to Study', 'pur', finish));
      return;
    }
    const c = cards[i];
    bar.textContent = `${i + 1} of ${cards.length} · ${c.course}: ${c.deck}`;
    const kids = [h('div', { style: 'font-size:calc(22px*var(--ts,1));color:#fff;margin-bottom:16px' }, c.q)];
    if (shown) {
      kids.push(h('div', { style: 'font-size:calc(17px*var(--ts,1));margin-bottom:8px' }, c.a));
      if (c.clue) kids.push(h('div', { class: 'muted', style: 'font-style:italic;margin-bottom:8px' }, c.clue));
      kids.push(h('div', { style: 'display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:12px' },
        STUDY_RATE.map(([n, label, cls]) => btn(`${n} ${label}`, cls, () => rate(n)))));
    } else kids.push(btn('Show answer (space)', 'pur', () => { shown = true; draw(); }));
    box.replaceChildren(...kids);
  };
  const again = {};
  const rate = async (n) => {
    const c = cards[i];
    try { await post('/api/study/review', { card_id: c.id, rating: n }); } catch (e) { toast(e.message, 1); return; }
    done += 1; if (n > 1) right += 1;
    if (n === 1 && (again[c.id] = (again[c.id] || 0) + 1) <= 2) cards.push(c);  // board #820 why-missed-card-twice
    i += 1; shown = false; draw();
    if (done % 20 === 0) studyXp();
  };
  document.onkeydown = (e) => {
    if (e.target.closest && e.target.closest('input,textarea')) return;
    if (!shown && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); shown = true; draw(); }
    else if (shown && /^[1-4]$/.test(e.key)) rate(+e.key);
  };
  show(bar, box, h('div', { class: 'muted', style: 'margin-top:10px;font-size:calc(12px*var(--ts,1))' }, 'Again = forgot, Hard = barely, Good = got it, Easy = instant. Keys: space shows the answer, 1 to 4 rate it.'));
  draw();
}
async function studyTest(courseId) {
  setHeader('Practice test', [btn('← Study', 'sm dim', () => go('#/study'))]);
  show(h('div', { class: 'muted' }, 'Making your test…'));
  let t;
  try { t = await post('/api/study/tests', { course_id: courseId, n: 10 }); } catch (e) { show(h('div', { class: 'err' }, e.message)); return; }
  const inputs = t.questions.map((q, i) => q.kind === 'choice'
    ? h('div', { role: 'radiogroup', 'aria-label': 'Question ' + (i + 1) }, q.options.map((o) => h('label', { style: 'display:flex;gap:8px;align-items:flex-start;margin:6px 0;cursor:pointer' },
        h('input', { type: 'radio', name: 'q' + i, value: o, style: 'margin-top:5px' }), h('span', {}, o))))
    : h('textarea', { rows: 2, 'aria-label': 'Your answer to question ' + (i + 1), placeholder: 'Your answer, in your own words', style: 'width:100%' }));
  const value = (i) => { const el = inputs[i]; return el.tagName === 'TEXTAREA' ? el.value : (el.querySelector('input:checked') || {}).value || ''; };
  const submit = btn('Check my answers', 'pur', async () => {
    submit.disabled = true; toast('Checking your answers (up to a minute)…');
    try {
      const answers = {}; t.questions.forEach((q, i) => { answers[i] = value(i); });
      const r = await post(`/api/study/tests/${t.id}/grade`, { answers });
      const mark = { right: ['✓', 'var(--ok)'], partly: ['½', 'var(--warn)'], wrong: ['✗', 'var(--bad)'] };
      show(h('div', { class: 'pagehead' }, h('h2', {}, `${r.score} of ${r.total} right`), h('span', { class: 'muted' }, 'Every card here is now scheduled by how you did: missed ones come back soon.')),
        h('div', { class: 'list' }, r.results.map((x) => h('div', { class: 'item', style: 'align-items:flex-start' }, h('span', { style: `color:${mark[x.verdict][1]};font-size:calc(22px*var(--ts,1));width:28px` }, mark[x.verdict][0]),
          h('div', { class: 'tx' }, h('div', { style: 'color:#fff' }, x.q), x.given ? h('div', {}, 'You: ' + x.given) : h('div', { class: 'muted' }, 'No answer'),
            x.verdict !== 'right' ? h('div', { style: 'color:var(--acc)' }, 'Answer: ' + x.key) : null, x.why ? h('div', { class: 'muted' }, x.why) : null,
            x.verdict !== 'right' && x.clue ? h('div', { class: 'muted', style: 'font-style:italic' }, x.clue) : null)))),
        h('div', { class: 'form', style: 'margin-top:14px' }, btn('Another test', 'pur', () => studyTest(courseId)), btn('Back to Study', 'dim', () => go('#/study'))));
      studyXp(); refreshSide();
    } catch (e) { toast(e.message, 1); submit.disabled = false; }
  });
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Practice test'), h('span', { class: 'muted' }, `${t.questions.length} questions from your cards, mixed across decks. Answer in your own words; spelling doesn't count.`)),
    ...t.questions.map((q, i) => h('div', { class: 'card', style: 'margin-top:12px' }, h('div', { style: 'color:#fff;margin-bottom:8px' }, `${i + 1}. ${q.q}`), inputs[i])),
    h('div', { style: 'margin-top:14px' }, submit));
}
async function studyTutor(courseId) {
  const o = await J('/api/study');
  const course = o.courses.find((c) => c.id === courseId);
  if (!course) return go('#/study');
  setHeader('Tutor', [btn('← Study', 'sm dim', () => go('#/study'))]);
  const log = h('div', { class: 'list' }), history = [];
  const say = (who, text, sources) => {
    history.push({ who, text });
    log.append(h('div', { class: 'item', style: 'align-items:flex-start;' + (who === 'me' ? 'background:var(--card2)' : '') },
      h('span', { style: 'width:28px' }, who === 'me' ? '🙋' : '🤖'), h('div', { class: 'tx' }, h('div', { style: 'white-space:pre-wrap' }, text),
        sources && sources.length ? h('details', { style: 'margin-top:4px' }, h('summary', { class: 'muted' }, `From your ${sources.map((x) => x.kind === 'card' ? 'cards' : 'notes').filter((v, i, a) => a.indexOf(v) === i).join(' and ')}`),
          sources.map((x) => h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, x.text))) : null)));
    log.lastChild.scrollIntoView({ block: 'nearest' });
  };
  const box = h('textarea', { rows: 2, placeholder: 'Ask about anything in this course, or explain a topic back…', 'aria-label': 'Message', style: 'width:100%' });
  const send = async (text) => {
    text = (text || box.value).trim(); if (!text) return; box.value = '';
    say('me', text);
    const wait = h('div', { class: 'muted' }, 'Thinking…'); log.append(wait);
    try { const r = await post('/api/study/tutor', { course_id: courseId, message: text, history: history.slice(0, -1) }); wait.remove(); say('rosie', r.reply, r.sources); }
    catch (e) { wait.remove(); toast(e.message, 1); }
  };
  box.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } };
  const explain = btn('Explain it back', 'sm', async () => {
    const c = (await J(`/api/study/due?course=${courseId}&limit=1&every=1`)).cards[0];
    if (!c) return toast('No cards in this course yet.', 1);
    say('rosie', `In your own words, explain: ${c.q}\nThen press Send and I'll tell you what you got and what's missing.`);
  });
  show(h('div', { class: 'pagehead' }, h('h2', {}, `Tutor · ${course.name}`), h('span', { class: 'muted' }, 'I only use your own cards and notes, and I ask before I tell. Say "just tell me" when you want the answer.')),
    h('div', { class: 'card' }, log, h('div', { style: 'margin-top:10px' }, box), h('div', { class: 'form', style: 'margin-top:8px' }, btn('Send', 'pur', () => send()), explain)));
}
function walkStop() { if (walk) { walk.stop = true; try { walk.audio.pause(); } catch (e) { } walk = null; document.onkeydown = null; } }
async function walkSay(text) {
  const r = await fetch('/api/speak', jopt('POST', { text }));
  if (!r.ok) throw new Error('Rosie\'s voice is not available');
  const url = URL.createObjectURL(await r.blob());
  await new Promise((res) => { if (!walk) return res(); walk.audio.src = url; walk.audio.onended = res; walk.audio.onerror = res; walk.audio.play().catch(res); });
  URL.revokeObjectURL(url);
}
const walkWait = (ms) => new Promise((res) => { const t = setTimeout(res, ms); if (walk) walk.wake = () => { clearTimeout(t); res(); }; });
async function studyWalk(arg) {
  walkStop();
  const scope = studyScope(arg);
  const dueNow = (await J('/api/study/due?limit=200&' + scope)).cards;
  setHeader('Walk', [btn('Stop', 'sm dim', () => { walkStop(); studyXp(); go('#/study'); })]);
  const pause = h('input', { type: 'number', min: 2, max: 30, value: store.get('walk_pause', 6), 'aria-label': 'Seconds to answer', style: 'max-width:80px', onchange: () => store.set('walk_pause', +pause.value || 6) });
  const clue = h('input', { type: 'checkbox', checked: store.get('walk_clue', true), onchange: () => store.set('walk_clue', clue.checked) });
  const onlyDue = h('input', { type: 'checkbox', checked: dueNow.length > 0 });
  const now = h('div', { style: 'font-size:calc(22px*var(--ts,1));color:#fff;min-height:90px;text-align:center;margin:12px 0' },
    dueNow.length ? `${dueNow.length} cards due. Ready when you are.` : 'Nothing is due, so the walk goes through every card.');
  const info = h('div', { class: 'muted', style: 'text-align:center' });
  let pick = null;
  const big = (label, cls, n) => h('button', { class: 'btn ' + cls, style: 'flex:1;min-height:110px;font-size:calc(20px*var(--ts,1))', onclick: () => { pick = n; if (walk && walk.wake) walk.wake(); } }, label);
  const start = btn('▶ Start', 'pur', async () => {
    if (walk) return;
    const cards = onlyDue.checked ? dueNow.slice() : (await J('/api/study/due?limit=500&every=1&' + scope)).cards;
    if (!cards.length) return toast('No cards here yet.', 1);
    const again = {};
    walk = { stop: false, audio: new Audio(), wake: null };
    if ('mediaSession' in navigator) {
      navigator.mediaSession.setActionHandler('nexttrack', () => { pick = 3; if (walk && walk.wake) walk.wake(); });
      navigator.mediaSession.setActionHandler('previoustrack', () => { pick = 1; if (walk && walk.wake) walk.wake(); });
    }
    let done = 0, right = 0;
    try {
      for (let i = 0; walk && !walk.stop && i < cards.length; i++) {
        const c = cards[i]; pick = null;
        info.textContent = `${i + 1} of ${cards.length} · ${c.course}: ${c.deck}`;
        now.textContent = c.q;
        await walkSay(c.q); if (!walk || walk.stop) break;
        await walkWait((+pause.value || 6) * 1000); if (!walk || walk.stop) break;
        now.replaceChildren(h('div', {}, c.q), h('div', { style: 'font-size:calc(17px*var(--ts,1));color:var(--acc);margin-top:8px' }, c.a));
        await walkSay('Answer. ' + c.a + (clue.checked && c.clue ? '. Clue. ' + c.clue : '')); if (!walk || walk.stop) break;
        if (pick == null) await walkWait(5000);
        if (pick != null) {
          try { await post('/api/study/review', { card_id: c.id, rating: pick }); done += 1; if (pick > 1) right += 1; else if ((again[c.id] = (again[c.id] || 0) + 1) <= 2) cards.push(c); } catch (e) { }
        }
      }
    } catch (e) { toast(e.message, 1); }
    if (walk && !walk.stop) { now.textContent = `Done: ${done} rated, ${right} remembered.`; await walkSay(`Done. ${right} of ${done} remembered. Nice walk.`).catch(() => {}); }
    walkStop(); studyXp(); refreshSide();
  });
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Walk mode'), h('span', { class: 'muted' }, 'Rosie reads each question, gives you time to answer out loud, then reads the answer. Tap Got it or Missed, or use your headphone buttons (next = got it, back = missed).')),
    h('div', { class: 'card' }, h('div', { class: 'form', style: 'align-items:center' }, start, h('label', { class: 'muted' }, 'Seconds to answer '), pause,
      h('label', { class: 'muted', style: 'display:flex;align-items:center;gap:6px' }, clue, 'Read the clue'),
      h('label', { class: 'muted', style: 'display:flex;align-items:center;gap:6px' }, onlyDue, 'Only cards that are due')), info, now,
      h('div', { style: 'display:flex;gap:10px' }, big('✗ Missed', 'bad', 1), big('✓ Got it', 'ok', 3))));
}

async function studyDownload(url, fallback, wait) {
  if (wait) toast(wait);
  try {
    const r = await fetch(url);
    if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) { } throw new Error((d && d.error) || ('HTTP ' + r.status)); }
    const name = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '');
    const a = h('a', { href: URL.createObjectURL(await r.blob()), download: name ? name[1] : fallback });
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, 1); }
}
function studyAnkiImport(deckId, again) {
  const f = h('input', { type: 'file', accept: '.txt,.tsv,.csv,.apkg,.colpkg', style: 'display:none', onchange: async () => {
    const file = f.files[0]; if (!file) return;
    try {
      const r = await J('/api/study/anki/import?deck=' + deckId, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
      toast(`${r.added} cards added` + (r.skipped ? `, ${r.skipped} already there` : '') + (r.errors ? `, ${r.errors} could not be read` : ''), r.errors && !r.added);
      again();
    } catch (e) { toast(e.message, 1); }
  } });
  document.body.append(f); f.click(); setTimeout(() => f.remove(), 60000);
}
async function studyMap(courseId) {
  setHeader('Map', [btn('← Study', 'sm dim', () => go('#/study'))]);
  let m; try { m = await J('/api/study/map?course=' + courseId); } catch (e) { return show(h('div', { class: 'err' }, e.message)); }
  const kids = {}; m.edges.forEach((e) => (kids[e.from] = kids[e.from] || []).push(e.to));
  const byId = Object.fromEntries(m.nodes.map((n) => [n.id, n]));
  const leaves = (id) => (kids[id] ? kids[id].reduce((s, k) => s + leaves(k), 0) : 1);
  const total = leaves(m.nodes[0].id), R = [0, 90, 170, Math.max(270, total * 6.5)], size = (R[3] + 150) * 2, pos = {};
  const place = (id, depth, a0, a1) => {
    const a = (a0 + a1) / 2, r = R[Math.min(depth, 3)]; pos[id] = { x: size / 2 + r * Math.cos(a), y: size / 2 + r * Math.sin(a), a, depth };
    let at = a0; (kids[id] || []).forEach((k) => { const w = (a1 - a0) * leaves(k) / leaves(id); place(k, depth + 1, at, at + w); at += w; });
  };
  place(m.nodes[0].id, 0, -Math.PI / 2, Math.PI * 1.5);
  m.nodes.forEach((n) => { if (n.type === 'card' && pos[n.id].depth === 2) { const p = pos[n.id]; p.x = size / 2 + R[3] * Math.cos(p.a); p.y = size / 2 + R[3] * Math.sin(p.a); } });
  const S = (t, a, kids2) => { const e = document.createElementNS('http://www.w3.org/2000/svg', t); for (const k in a) e.setAttribute(k, a[k]); (kids2 || []).forEach((c) => e.append(c)); return e; };
  const fill = { course: 'var(--acc)', deck: 'var(--pur)', tag: 'var(--warn)', card: 'var(--ok)' }, rad = { course: 22, deck: 14, tag: 10, card: 5 };
  const svg = S('svg', { viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: 'img', 'aria-label': 'Concept map of ' + m.course.name, style: 'display:block;max-width:none' });
  m.edges.forEach((e) => svg.append(S('line', { x1: pos[e.from].x, y1: pos[e.from].y, x2: pos[e.to].x, y2: pos[e.to].y, style: 'stroke:var(--line);stroke-width:1.5' })));
  const again = () => keepScroll(() => studyMap(courseId));
  m.nodes.forEach((n) => {
    const p = pos[n.id], right = Math.cos(p.a) >= 0, card = n.type === 'card';
    const g = S('g', { style: card ? 'cursor:pointer' : '', tabindex: card ? '0' : '-1', role: card ? 'button' : 'img', 'aria-label': n.label }, [S('title', {}, [document.createTextNode(card ? n.q : n.label)])]);
    g.append(S('circle', { cx: p.x, cy: p.y, r: rad[n.type], style: 'fill:' + fill[n.type] }));
    const txt = n.type === 'course' ? n.label : n.type === 'card' ? (n.label.length > 28 ? n.label.slice(0, 27) + '…' : n.label) : n.label.slice(0, 22);
    const t = S('text', { x: n.type === 'course' ? p.x : p.x + (right ? 1 : -1) * (rad[n.type] + 4), y: n.type === 'course' ? p.y + 38 : p.y + 3, 'text-anchor': n.type === 'course' ? 'middle' : right ? 'start' : 'end', 'font-size': card ? 10 : 12, style: 'fill:var(--fg)' });
    t.textContent = txt; g.append(t);
    if (card) { const open = () => studyEdit({ id: n.card_id, q: n.q, a: n.a, clue: n.clue }, again); g.onclick = open; g.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }; }
    svg.append(g);
  });
  show(h('div', { class: 'pagehead' }, h('h2', {}, m.course.name + ' map'), h('span', { class: 'muted' },
    `Your course, decks, tags and cards, drawn from what you entered. Tap a card to open it.` + (m.truncated ? ' Showing the first cards only.' : ''))),
    m.nodes.length > 1 ? h('div', { class: 'card', style: 'overflow:auto;max-height:80vh;padding:6px' }, svg) : h('div', { class: 'empty' }, 'Add decks and cards to see a map.'));
}

const LQ_COLORS = ['var(--acc)', 'var(--pur)', 'var(--ok)', '#FFD23F', 'var(--pink)', 'var(--warn)', '#FF9F43', '#5EEAD4', '#FDA4AF', '#A3E635'];
let lqEdit = false, lqReview = false;
VIEWS.lifequest = async () => {
  setHeader('Life Quest', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  let d = await J('/api/lifequest');
  const color = (id) => LQ_COLORS[Math.max(0, d.stats.findIndex((s) => s.id === id)) % LQ_COLORS.length];
  const sname = (id) => (d.stats.find((s) => s.id === id) || { name: id }).name;
  const live = () => d.stats.filter((s) => !s.archived);
  const act = async (fn, ok) => {
    try {
      const r = await fn();
      d = r;
      if (r.level_up) toast('Level up. You are level ' + r.level_up.after + '.');
      else if (ok) toast(ok);
      draw();
    } catch (e) { toast(e.message, 1); }
  };
  const pick = new Set(), qxp = { v: 25 };
  const noteIn = h('input', { placeholder: 'What did you do? (like: ran 2 miles)', maxlength: 200, 'aria-label': 'What you did' });
  const draw = () => {
    const o = d.overall;
    const hero = h('div', { class: 'card wide lq-hero' },
      h('div', { class: 'lq-lvl' }, h('span', { class: 'lq-n' }, o.level), h('div', {}, h('div', { class: 'lq-t' }, 'Level ' + o.level + ' ' + o.title.toLowerCase()), h('div', { class: 'muted' }, o.to_next + ' XP to level ' + (o.level + 1)))),
      h('div', { class: 'bar lq-bar', role: 'progressbar', 'aria-valuenow': o.pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('i', { style: 'width:' + o.pct + '%' })),
      h('div', { class: 'lq-chips' }, [['Total XP', o.xp], ['Today', '+' + d.today_xp], ['Streak', d.streak ? d.streak + ' day' + (d.streak === 1 ? '' : 's') : 'start today'], ['This week', d.week_days + '/7 days'], ['Points', d.wallet]].map(([k, v]) =>
        h('div', { class: 'lq-chip' }, h('b', {}, v), h('span', {}, k)))),
      h('div', { class: 'lq-days', 'aria-hidden': 'true' }, Array.from({ length: 21 }, (_, i) => { const t = new Date(); t.setDate(t.getDate() - 20 + i); const k = t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0'); return h('i', { class: (d.active_days.includes(k) ? 'on' : '') + (k === d.today ? ' today' : ''), title: k }); })),
      h('div', { class: 'muted lq-soft' }, 'A missed day never ends the game. Pick it up whenever you are ready.'));
    const logWin = async (e) => {
      e.preventDefault();
      if (!noteIn.value.trim()) return toast('Say what you did first. XP is only for real, done actions.', 1);
      if (!pick.size) return toast('Pick the area it grew.', 1);
      const entries = [...pick].map((s) => ({ stat: s, xp: qxp.v }));
      await act(() => post('/api/lifequest/log', { note: noteIn.value, entries }), '+' + qxp.v * entries.length + ' XP');
      noteIn.value = ''; pick.clear();
    };
    const logCard = h('div', { class: 'card' }, h('h3', {}, 'Log a win'),
      h('form', { class: 'form col', onsubmit: logWin }, noteIn,
        h('div', { class: 'lq-pick', role: 'group', 'aria-label': 'Areas' }, live().map((s) => h('button', { type: 'button', class: 'lq-tag' + (pick.has(s.id) ? ' on' : ''), style: '--c:' + color(s.id), 'aria-pressed': pick.has(s.id) ? 'true' : 'false',
          onclick: (ev) => { pick.has(s.id) ? pick.delete(s.id) : pick.add(s.id); ev.target.classList.toggle('on'); ev.target.setAttribute('aria-pressed', pick.has(s.id)); } }, s.name))),
        h('div', { class: 'form' }, h('div', { class: 'seg', role: 'group', 'aria-label': 'XP' }, [10, 25, 50, 100].map((n) => h('button', { type: 'button', class: qxp.v === n ? 'on' : '', onclick: (ev) => { qxp.v = n; ev.target.parentNode.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === ev.target)); } }, '+' + n))),
          btn('Log it', 'ok', null, 'submit')),
        h('div', { class: 'muted lq-soft' }, 'XP is for things you really did. The more areas you pick, the more each one earns.')));
    const todayQ = d.quests.filter((q) => !q.archived && (q.kind === 'daily' || !q.done || q.done_day === d.today));
    const questCard = h('div', { class: 'card' }, h('h3', {}, 'Today’s quests'),
      todayQ.length ? h('div', { class: 'list' }, todayQ.map((q) => h('div', { class: 'item' + (q.done ? ' done' : '') },
        h('button', { class: 'check' + (q.done ? ' on' : ''), 'aria-label': (q.done ? 'Undo ' : 'Done: ') + q.title, 'aria-pressed': q.done ? 'true' : 'false', onclick: () => act(() => post('/api/lifequest/quests/' + q.id + '/done', { done: !q.done }), q.done ? 'Unchecked' : '+' + q.xp + ' XP') }, q.done ? '✓' : ''),
        h('div', { class: 'tx' }, q.title, h('div', { class: 'muted lq-sub' }, sname(q.stat) + (q.kind === 'boss' ? ' · boss' : q.kind === 'quest' ? ' · quest' : ''))),
        h('span', { class: 'lq-xp' }, '+' + q.xp)))) : h('div', { class: 'empty' }, 'No quests yet. Turn a goal into one with Edit.'),
      h('div', { class: 'muted lq-soft' }, 'Tick a quest only when you did it.'));
    const statCard = h('div', { class: 'card' }, h('h3', {}, 'Character stats'),
      h('div', { class: 'list' }, live().map((s) => h('div', { class: 'item lq-stat', style: '--c:' + color(s.id) },
        h('div', { class: 'tx' }, h('div', { class: 'lq-row' }, h('b', {}, s.name), h('span', { class: 'muted' }, 'Lv ' + s.level + ' · ' + s.xp + ' XP' + (s.week_xp ? ' · +' + s.week_xp + ' this week' : ''))),
          h('div', { class: 'bar' }, h('i', { style: 'width:' + s.pct + '%;background:var(--c)' })), s.note ? h('div', { class: 'muted lq-sub' }, s.note) : null)))));
    const claimBtn = (r) => { const b = btn('Claim', 'sm ok', () => act(() => post('/api/lifequest/rewards/' + r.id + '/claim'), 'Enjoy it.')); b.disabled = d.wallet < r.cost; return b; };
    const rewardCard = h('div', { class: 'card' }, h('h3', {}, 'Rewards'),
      h('div', { class: 'list' }, d.rewards.filter((r) => !r.archived).map((r) => h('div', { class: 'item' },
        h('div', { class: 'tx' }, r.title, h('div', { class: 'muted lq-sub' }, (d.wallet >= r.cost ? 'Ready to claim' : (r.cost - d.wallet) + ' more points') + (r.claimed ? ' · claimed ' + r.claimed + '×' : ''))),
        h('span', { class: 'lq-xp' }, r.cost), claimBtn(r)))),
      h('div', { class: 'muted lq-soft' }, 'Spending points never lowers your level.'));
    const winCard = h('div', { class: 'card' }, h('h3', {}, 'Recent wins'),
      d.wins.length ? h('div', { class: 'list' }, d.wins.slice(0, 10).map((w) => h('div', { class: 'item' },
        h('div', { class: 'tx' }, w.note, h('div', { class: 'muted lq-sub' }, w.day + ' · ' + w.stats.map((x) => sname(x.stat)).join(', ') + (w.source === 'chat' ? ' · via chat' : ''))),
        h('span', { class: 'lq-xp' }, '+' + w.xp),
        h('button', { class: 'x', 'aria-label': 'Remove this entry', title: 'Remove a mistake', onclick: async () => { if (!confirm('Remove this entry and its XP?')) return; await act(() => del('/api/lifequest/log/' + w.win), 'Removed'); } }, '✕')))) : h('div', { class: 'empty' }, 'Nothing logged yet. Your first win is one tick away.'));
    const rv = d.review, saved = rv.saved || {};
    const fld = (label, v) => { const t = h('textarea', { rows: 2, maxlength: 1000, 'aria-label': label, placeholder: label }); t.value = v || ''; return t; };
    const rWins = fld('What went well this week?', saved.wins), rLess = fld('What did you learn?', saved.lessons), rNext = fld('One thing for next week', saved.next);
    const reviewCard = (rv.due || lqReview) ? h('div', { class: 'card wide' }, h('h3', {}, 'Weekly review'),
      h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'This week you earned ' + d.week_xp + ' XP on ' + d.week_days + ' of 7 days' + (d.stats.some((s) => s.week_xp) ? ': ' + d.stats.filter((s) => s.week_xp).map((s) => s.name + ' +' + s.week_xp).join(', ') : '') + '. Be kind to yourself.'),
      h('div', { class: 'form col' }, rWins, rLess, rNext, btn(rv.saved ? 'Update review' : 'Save review', 'pur', () => act(() => post('/api/lifequest/review', { wins: rWins.value, lessons: rLess.value, next: rNext.value }), 'Review saved')))) : null;
    const lastR = rv.last && !reviewCard ? h('div', { class: 'muted lq-soft' }, 'Last review (week ending ' + rv.last.week + '): ' + (rv.last.next || rv.last.wins || rv.last.lessons)) : null;
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Life Quest'), h('div', { class: 'sp' }),
        btn(lqReview ? 'Hide review' : 'Weekly review', 'sm pur', () => { lqReview = !lqReview; draw(); }), btn(lqEdit ? 'Done editing' : 'Edit', 'sm', () => { lqEdit = !lqEdit; draw(); })),
      h('div', { class: 'grid lq-grid' }, hero, logCard, questCard, statCard, rewardCard, winCard, reviewCard, lqEdit ? lqManage() : null), lastR);
  };
  const lqManage = () => {
    const statRow = (s) => {
      const nm = h('input', { value: s.name, maxlength: 30, 'aria-label': 'Stat name' }), nt = h('input', { value: s.note, maxlength: 80, placeholder: 'What it covers (optional)', 'aria-label': 'Stat note' });
      return h('div', { class: 'form lq-edit' }, nm, nt, btn('Save', 'sm ok', () => act(() => post('/api/lifequest/stats', { stats: [{ id: s.id, name: nm.value, note: nt.value, archived: s.archived }] }), 'Saved')),
        s.id ? btn(s.archived ? 'Show' : 'Hide', 'sm dim', () => act(() => post('/api/lifequest/stats', { stats: [{ id: s.id, name: s.name, note: s.note, archived: !s.archived }] }))) : null);
    };
    const newStat = h('div', { class: 'form lq-edit' }, (() => { const n = h('input', { placeholder: 'New stat (like Reading)', maxlength: 30, 'aria-label': 'New stat name' }); return [n, btn('Add stat', 'sm pur', () => act(() => post('/api/lifequest/stats', { stats: [{ name: n.value }] }), 'Added'))]; })());
    const statSel = (v) => h('select', { 'aria-label': 'Stat' }, live().map((s) => h('option', { value: s.id, selected: s.id === v }, s.name)));
    const questRow = (q) => {
      const t = h('input', { value: q ? q.title : '', maxlength: 120, placeholder: q ? '' : 'New quest (like Study 45 min)', 'aria-label': 'Quest name' }), st = statSel(q && q.stat);
      const xp = h('input', { type: 'number', value: q ? q.xp : 25, min: 1, max: d.max_xp, 'aria-label': 'XP', style: 'max-width:80px;flex:none' });
      const k = h('select', { 'aria-label': 'Kind' }, d.kinds.map((x) => h('option', { value: x, selected: q && q.kind === x }, x)));
      const save = (arch) => act(() => post('/api/lifequest/quests', { id: q ? q.id : null, title: t.value, stat: st.value, xp: Number(xp.value), kind: k.value, archived: arch }), 'Saved');
      return h('div', { class: 'form lq-edit' + (q && q.archived ? ' off' : '') }, t, st, xp, k, btn(q ? 'Save' : 'Add quest', 'sm ' + (q ? 'ok' : 'pur'), () => save(q ? q.archived : false)), q ? btn(q.archived ? 'Show' : 'Archive', 'sm dim', () => save(!q.archived)) : null);
    };
    const rewardRow = (r) => {
      const t = h('input', { value: r ? r.title : '', maxlength: 80, placeholder: r ? '' : 'New reward', 'aria-label': 'Reward name' }), c = h('input', { type: 'number', value: r ? r.cost : 500, min: 1, 'aria-label': 'Cost in points', style: 'max-width:90px;flex:none' });
      const save = (arch) => act(() => post('/api/lifequest/rewards', { id: r ? r.id : null, title: t.value, cost: Number(c.value), archived: arch }), 'Saved');
      return h('div', { class: 'form lq-edit' + (r && r.archived ? ' off' : '') }, t, c, btn(r ? 'Save' : 'Add reward', 'sm ' + (r ? 'ok' : 'pur'), () => save(r ? r.archived : false)), r ? btn(r.archived ? 'Show' : 'Archive', 'sm dim', () => save(!r.archived)) : null);
    };
    return h('div', { class: 'card wide' }, h('h3', {}, 'Edit your game'),
      h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'Pick up to ' + d.max_stats + ' areas to grow. Hiding a stat or quest keeps the XP you earned.'),
      h('div', { class: 'dayhead' }, 'Stats'), d.stats.map(statRow), newStat,
      h('div', { class: 'dayhead' }, 'Quests (daily, quest, or boss for the hard thing you avoid)'), d.quests.map(questRow), questRow(null),
      h('div', { class: 'dayhead' }, 'Rewards'), d.rewards.map(rewardRow), rewardRow(null));
  };
  draw();
};

VIEWS.calendar = async () => {
  setHeader('Calendar', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/calendar?days=7');
  const title = h('input', { placeholder: 'New event title', 'aria-label': 'Event title' }), date = h('input', { type: 'date', value: todayISO(), 'aria-label': 'Date' });
  const st = h('input', { type: 'time', value: '09:00', 'aria-label': 'Start' }), en = h('input', { type: 'time', value: '10:00', 'aria-label': 'End' });
  const where = d.write_target === 'primary' ? 'your REAL calendar' : 'the TEST calendar (change in Settings)';
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Next 7 days'), h('div', { class: 'sp' }), btn('Ask assistant to change something', 'sm pur', () => { newChat(); t.value = 'On my calendar: '; t.focus(); })),
    d.error ? h('div', { class: 'err' }, d.error + ' (' + loginHint + ')') : null,
    h('div', { class: 'card' }, h('h3', {}, 'Add event'), h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'Saves to ' + where + '. To move or delete events, ask the assistant (it asks you to confirm).'),
      h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); try { const r = await post('/api/calendar/event', { title: title.value, date: date.value, start: st.value, end: en.value }); toast('Added to the ' + r.calendar.toUpperCase() + ' calendar'); VIEWS.calendar(); } catch (er) { toast(er.message, 1); } } },
        title, date, st, en, btn('Add', 'pur', null, 'submit'))),
    d.days.map((day) => h('div', {}, h('div', { class: 'dayhead' + (day.date === todayISO() ? ' today' : '') }, day.pretty + (day.date === todayISO() ? '  · today' : '')),
      day.events.length ? h('div', { class: 'list' }, day.events.map((e) => h('div', { class: 'item' }, h('span', { class: 'time' }, e.all_day ? 'all day' : e.start), h('span', { class: 'tx' }, e.title, e.location ? h('span', { class: 'muted' }, '  @ ' + e.location) : null), e.end ? h('span', { class: 'muted' }, '→ ' + e.end) : null))) : h('div', { class: 'empty' }, 'Nothing.'))));
};

let noteCur = null;
VIEWS.notes = async (arg) => {
  setHeader('Notes', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const tree = await J('/api/vault');
  const wrap = h('div', { class: 'notes' + (arg ? ' edit' : '') });
  const files = h('div', { class: 'files' });
  const found = h('div', { class: 'list' }), q = h('input', { placeholder: 'Search all notes…', 'aria-label': 'Search notes' });
  files.append(h('form', { class: 'form', style: 'margin-bottom:8px', onsubmit: async (e) => { e.preventDefault(); found.replaceChildren(); if (!q.value.trim()) return; try { const r = await J('/api/search?q=' + encodeURIComponent(q.value));
    found.replaceChildren(...(r.results.length ? r.results.map((x) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg);flex-direction:column;align-items:flex-start', onclick: () => go('#/notes/' + encodeURIComponent(x.path)) }, h('b', {}, x.title), h('span', { class: 'muted md', style: 'font-size:calc(12px*var(--ts,1))', html: fmt(x.snippet).replace(/\[/g, '<b style="color:var(--acc)">').replace(/\]/g, '</b>') }))) : [h('div', { class: 'empty' }, 'Nothing found.')])); } catch (er) { toast(er.message, 1); } } }, q, btn('Find', 'sm', null, 'submit')), found);
  tree.groups.forEach((g) => {
    files.append(h('div', { class: 'fgrp' }, g.name, g.can_create ? btn('＋', 'sm', async () => { const n = prompt('Name for the new ' + (g.name === 'Skills' ? 'skill' : 'note') + ':'); if (!n) return; try { const r = await post('/api/vault/new', { group: g.name, name: n }); go('#/notes/' + encodeURIComponent(r.path)); } catch (e) { toast(e.message, 1); } }) : null));
    if (!g.files.length) files.append(h('div', { class: 'empty', style: 'padding:4px 10px' }, 'empty'));
    g.files.forEach((f) => files.append(h('button', { class: 'file' + (f.path === arg ? ' on' : ''), title: f.path, onclick: () => go('#/notes/' + encodeURIComponent(f.path)) }, (f.readonly ? '🔒 ' : '') + f.name)));
  });
  const editor = h('div', { class: 'editor' });
  wrap.append(files, editor);
  show(wrap);
  if (!arg) { editor.append(h('div', { class: 'empty' }, 'Pick a file on the left. Everything here is plain text on your PC: your scratch pad, memory, daily notes, meeting notes and the assistant\'s skills.')); return; }
  let f; try { f = await J('/api/vault/file?path=' + encodeURIComponent(arg)); } catch (e) { editor.append(h('div', { class: 'err' }, e.message)); return; }
  noteCur = f;
  const ta = h('textarea', { value: f.content, spellcheck: false, 'aria-label': 'Editor', disabled: f.readonly });
  const status = h('span', { class: 'muted' }, f.readonly ? 'generated file' : 'saved');
  const save = async () => { if (f.readonly) return; try { await put('/api/vault/file', { path: f.path, content: ta.value }); f.content = ta.value; dirty = false; status.textContent = 'saved ' + new Date().toTimeString().slice(0, 5); toast('Saved'); } catch (e) { toast(e.message, 1); } };
  ta.oninput = () => { dirty = ta.value !== f.content; status.textContent = dirty ? 'unsaved changes' : 'saved'; };
  ta.onkeydown = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); } };
  editor.append(h('div', { class: 'bar2' }, btn('← Files', 'sm dim', () => go('#/notes')), h('span', { class: 'nm' }, f.path), f.readonly ? h('span', { class: 'pill' }, 'read-only') : null, status,
    f.readonly ? null : btn('Save', 'ok sm', save)), ta,
    f.path.startsWith('skills/') ? h('div', { class: 'muted' }, 'First line = when to use this skill. The assistant reads the rest as step-by-step instructions. Changes apply right away.') : null);
};

VIEWS.memory = async () => {
  setHeader('Memory', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/memory');
  const again = () => keepScroll(() => VIEWS.memory());
  const kind = h('select', { 'aria-label': 'Kind' }, d.kinds.map((k) => h('option', { value: k }, k))), txt = h('input', { placeholder: 'Something I should remember…', 'aria-label': 'Memory text' });
  const byKind = {}; d.notes.forEach((n) => (byKind[n.kind] = byKind[n.kind] || []).push(n));
  const W = [['focus', 'Current focus'], ['waiting_on', 'Waiting on'], ['people', 'People'], ['followups', 'Follow-ups'], ['decisions', 'Recent decisions']];
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'What I remember about you'), h('span', { class: 'muted' }, 'Saved in a database on this PC. Delete anything that is wrong.')),
    h('div', { class: 'card' }, h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); if (!txt.value.trim()) return; try { await post('/api/memory', { kind: kind.value, content: txt.value }); again(); } catch (er) { toast(er.message, 1); } } }, kind, txt, btn('Save', 'pur', null, 'submit'))),
    h('div', { class: 'grid', style: 'margin-top:14px' },
      d.kinds.filter((k) => byKind[k]).map((k) => h('div', { class: 'card' }, h('h3', {}, k), h('div', { class: 'list' }, byKind[k].map((n) => h('div', { class: 'item' }, h('span', { class: 'tx' }, n.content), h('span', { class: 'chip' }, n.created.slice(0, 10)),
        h('button', { class: 'x', 'aria-label': 'Forget', title: 'Forget this', onclick: async () => { await del('/api/memory/' + n.id); again(); } }, '✕')))))),
      !d.notes.length ? h('div', { class: 'card' }, h('div', { class: 'empty' }, 'Nothing saved yet. Tell the assistant facts about you, or add one above.')) : null,
      h('div', { class: 'card wide' }, h('h3', {}, 'Working memory', h('a', { href: '#/notes/memory.md' }, 'Edit file →')),
        h('div', { class: 'grid' }, W.map(([k, label]) => h('div', {}, h('div', { class: 'muted' }, label.toUpperCase()), d.working[k].length ? d.working[k].map((x) => h('div', {}, '• ' + x)) : h('div', { class: 'empty' }, '—')))))));
};

VIEWS.skills = async () => {
  setHeader('Skills', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/skills');
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Skills'), h('span', { class: 'muted' }, 'Playbooks the assistant follows. Plain text you can edit.'), h('div', { class: 'sp' }),
    btn('＋ New skill', 'pur', async () => { const n = prompt('Name for the new skill (e.g. weekly review):'); if (!n) return; try { const r = await post('/api/vault/new', { group: 'Skills', name: n }); go('#/notes/' + encodeURIComponent(r.path)); } catch (e) { toast(e.message, 1); } })),
    h('div', { class: 'grid' }, d.skills.map((s) => h('div', { class: 'card' }, h('h3', {}, s.name), h('div', { class: 'muted', style: 'margin-bottom:10px' }, s.description),
      h('div', { class: 'form' }, btn('Edit', 'sm', () => go('#/notes/' + encodeURIComponent(s.path))))))));
};

function labelPicker() {
  const box = h('div', { class: 'muted' }, 'Loading your Gmail labels…');
  J('/api/mail/labels').then((d) => {
    const rows = d.labels.map((l) => ({ l, tick: h('input', { type: 'checkbox', checked: l.allowed, 'aria-label': 'Use ' + l.name, style: 'flex:none;width:auto' }), hint: h('input', { value: l.hint, placeholder: 'hint (optional)', 'aria-label': 'Hint for ' + l.name, maxlength: 120 }) }));
    const save = async () => { try { await post('/api/mail/labels', { labels: Object.fromEntries(rows.filter((r) => r.tick.checked).map((r) => [r.l.name, r.hint.value])) }); toast('Saved'); } catch (e) { toast(e.message, 1); } };
    rows.forEach((r) => { r.tick.onchange = save; r.hint.onchange = save; });
    box.className = '';
    box.replaceChildren(...(rows.length ? rows.map((r) => h('div', { class: 'form', style: 'margin-bottom:4px' }, r.tick, h('span', { style: 'min-width:140px' }, r.l.name), r.hint)) : [h('div', { class: 'muted' }, 'No Gmail labels found (connect Gmail first).')]));
  }).catch((e) => box.replaceChildren(e.message));
  return box;
}
async function autoSet(obj) { try { await post('/api/automations', obj); toast('Saved'); } catch (e) { toast(e.message, 1); } }
const onOff = (v, fn) => { const w = h('div', { class: 'seg' }); const draw = (cur) => w.replaceChildren(h('button', { class: cur ? 'on' : '', onclick: () => { draw(true); fn(true); } }, 'On'), h('button', { class: cur ? '' : 'on', onclick: () => { draw(false); fn(false); } }, 'Off')); draw(v); return w; };
const modeSel = (v, fn) => { const w = h('div', { class: 'seg' }); const draw = (cur) => w.replaceChildren(...['off', 'nudge', 'auto'].map((m) => h('button', { class: cur === m ? 'on' : '', onclick: () => { draw(m); fn(m); } }, m))); draw(v); return w; };
const timeIn = (v, fn) => h('input', { type: 'time', value: v, 'aria-label': 'Time', style: 'flex:none;width:150px', onchange: (e) => e.target.value && fn(e.target.value) });
const numSel = (v, opts, fn, suffix) => h('select', { 'aria-label': 'Minutes', onchange: (e) => fn(+e.target.value) }, opts.map((o) => h('option', { value: o, selected: o === v }, o + (suffix || ''))));
const autoRow = (label, a, b, hint) => [h('dt', {}, label), h('dd', {}, h('div', { class: 'form' }, a, b), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, hint))];
const healthRow = (r) => h('div', { class: 'item' },
  h('span', { class: 'chip ' + (r.level === 'ok' ? 'ok' : r.level === 'off' ? '' : 'bad') }, r.level === 'ok' ? 'ok' : r.level === 'off' ? 'off' : r.level === 'warn' ? 'soon' : 'fix'),
  h('span', { class: 'tx' }, h('b', {}, r.name + '  '), h('span', { class: 'muted' }, r.detail + (r.fix ? '  →  ' + r.fix : ''))));
function modelPicker(md, live, again) {
  const where = live.model ? (live.loaded ? `loaded: ${live.model} · ${live.where}` : live.where) : (live.error || '');
  if (!md || md.error || !md.models) return h('div', {}, h('div', {}, live.model || 'unknown'), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, (md && md.error) || 'Model list unavailable.'));
  const label = (m) => `${m.name} · ${m.size_gb} GB` + (!m.usable ? ` (${m.why_not})` : m.fits_gpu ? '' : ' (too big for the GPU: slow)') + (m.name === md.default ? ' · default' : '');
  const sel = h('select', { 'aria-label': 'AI model', style: 'max-width:100%' }, md.models.map((m) => h('option', { value: m.name, selected: m.name === md.current, disabled: !m.usable }, label(m))));
  let note;
  const use = async (name) => {
    if (name === md.current) { toast('Rosie already uses ' + name); return; }
    const m = md.models.find((x) => x.name === name) || {};
    if (!m.fits_gpu && !confirm(name + ' is bigger than the GPU, so part of it runs on the CPU. Rosie will answer much more slowly. Switch anyway?')) return;
    toast('Testing ' + name + '. Loading a model can take a minute…'); sel.disabled = true; note.textContent = 'Testing ' + name + '…';
    try { const r = await post('/api/models', { model: name }); toast(`Rosie now uses ${r.model} (test answer in ${r.seconds} s)`); again(); } catch (e) { toast(e.message, 1); note.textContent = e.message; sel.disabled = false; }
  };
  return h('div', {},
    h('div', { class: 'form' }, sel, btn('Use this model', 'sm pur', () => use(sel.value)), md.current !== md.default ? btn('Back to ' + md.default, 'sm', () => use(md.default)) : null),
    note = h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, `Now: ${md.current}` + (where ? ' · ' + where : '') + '. Used for chat, her corner and her memory. Applies right away; no restart.'),
    md.problem ? h('div', { style: 'font-size:calc(12px*var(--ts,1));color:var(--bad)' }, md.problem) : null,
    md.scout ? scoutLine(md.scout) : null);
}
function scoutLine(sc) {
  const t = sc.last_test;
  const last = !t ? 'nothing tested yet' : `last tested ${t.id} (${t.tested.slice(0, 10)}): ` + (t.status === 'flagged' ? `better, ${t.score} vs ${t.current_score}; see the Inbox`
    : t.score ? `${t.score} vs ${t.current_score}, not better` : t.why);
  return h('div', { style: 'margin-top:8px' }, h('div', { class: 'form' }, h('b', { style: 'font-size:calc(13px*var(--ts,1))' }, 'Model scout'), onOff(sc.on, (v) => autoSet({ model_scout: v }))),
    h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, sc.doing ? sc.doing + '…' :
      `Looks for new abliterated models twice a day and tests the best one at night (0:30 to 1:45, while you're away) on her own everyday tasks. A better one is flagged in the Inbox; she never switches by herself. ` +
      `Looked at ${sc.looked_at} models, ${sc.candidates} waiting to be tested; ${last}.` + (sc.last_error ? ' Last problem: ' + sc.last_error : '')));
}
function secBox(sw, again) {
  if (!sw || sw.error) return h('div', { class: 'muted' }, (sw && sw.error) || 'unknown');
  const small = (t, st) => h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' + (st || '') }, t);
  const run = async (url, body) => { try { const r = await post(url, body || {}); toast(r.message || (r.started ? 'Started' : 'Already running')); setTimeout(again, 1500); } catch (e) { toast(e.message, 1); } };
  const last = !sw.ran_at ? 'Not checked yet.' : `Last checked ${sw.ran_at.slice(0, 16).replace('T', ' ')}: ${sw.counted.machines || 0} machines, ${sw.counted.packages || 0} packages, ${sw.counted.apps || 0} apps. ` +
    (sw.holes ? `${sw.holes} known holes (${sw.serious} high or critical)` + (sw.apps.length ? ': ' + sw.apps.join(', ') : '') + '; details in the Inbox.' : 'No known holes.');
  const waiting = Object.entries(sw.waiting).map(([k, n]) => `${k}: ${n}`).join(', ');
  const p = sw.patched.length ? sw.patched[sw.patched.length - 1] : null;
  const sp = sw.self_patch;
  const ssh = h('input', { value: sw.ssh || '', placeholder: 'root@192.168.1.10', 'aria-label': 'Home server SSH address' });
  const key = h('input', { value: sw.ssh_key || '', placeholder: '~/.ssh/id_ed25519', 'aria-label': 'SSH key file' });
  return h('div', {},
    h('div', { class: 'form' }, h('b', { style: 'font-size:calc(13px*var(--ts,1))' }, 'Nightly check'), onOff(sw.on, (v) => autoSet({ security_watch: v })),
      h('b', { style: 'font-size:calc(13px*var(--ts,1));margin-left:10px' }, 'Patch by herself'), onOff(sw.autopatch, (v) => autoSet({ security_autopatch: v }))),
    small(sw.doing ? sw.doing + '…' : last),
    sw.found.length ? small('Apps she found: ' + sw.found.join(', ') + '.') : null,
    waiting ? small('Security updates waiting: ' + waiting + '.') : null,
    p ? small(`Last patch (${p.at.slice(0, 16).replace('T', ' ')}): ${p.where}, ${p.what || ''} ${p.message || ''}`) : null,
    sp && sp.done_at ? small('Her own packages: ' + (sp.ok ? 'patched and tested.' : sp.why), sp.ok ? '' : ';color:var(--bad)') : sp && sp.started ? small('Patching her own packages…') : null,
    sw.last_error ? small('Last problem: ' + sw.last_error, ';color:var(--bad)') : null,
    sw.problems.length ? small("Couldn't check: " + sw.problems.join('; ')) : null,
    h('div', { class: 'form', style: 'margin-top:6px' }, btn('Check now', 'sm', () => run('/api/security/run')),
      sw.server ? sw.patches.map((x) => btn(x.label, 'sm', () => confirm(x.label + ' now? It runs on your home server and can take several minutes.') && run('/api/security/patch', { target: x.target }))) : null),
    h('div', { class: 'form', style: 'margin-top:8px' }, h('span', { style: 'font-size:calc(12px*var(--ts,1))' }, 'Home server'), ssh, key, btn('Save', 'sm', () => run('/api/security/server', { ssh: ssh.value, key: key.value }))),
    small('Optional: a Debian or Ubuntu server she can reach with an SSH key (no password). On a Proxmox host she also checks every running container. Leave it empty to check only this PC.'),
    small('Every night (3:00 to 6:00, while you\'re away) she checks her own packages, this PC, your home server and the apps she finds there against OSV, GitHub advisories, new CVEs and CISA\'s list of holes being exploited now. ' +
      'With Patch by herself on, she installs the containers\' security updates and fixes her own packages (tested, put back if anything fails). The server itself, app updates and this PC wait for you.'));
}
let a0Draft = { text: '', to: 'all' };
let ctxPollT = null;
function ctxBox(cx, again) {
  if (!cx || cx.error) return h('div', { class: 'muted' }, (cx && cx.error) || 'unknown');
  const c = cx.current || {}, st = cx.status || {};
  const how = !cx.tuned ? 'default, not tuned yet' : c.num_gpu ? 'every layer on the graphics card' : c.fits === false ? 'too big for the graphics card: part runs on the CPU' : 'Ollama decides where it runs';
  const line = h('div', {}, `${(c.num_ctx || 0).toLocaleString()} tokens`, h('span', { class: 'muted' }, ` · ${how}` + (c.tuned_at ? ` · tuned ${c.tuned_at.replace('T', ' ')}` : '')));
  const note = h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, st.running ? st.step : st.error ? 'Last tune failed: ' + st.error
    : `Finds the biggest window that keeps ${cx.model} fully on the graphics card. Takes 2 to 5 minutes; answers wait meanwhile. Agent Zero follows.`);
  const b = btn(st.running ? 'Tuning…' : 'Auto-tune', 'sm pur', async () => {
    try { await post('/api/ctx/tune', {}); toast('Tuning the context window. This takes a few minutes…'); b.disabled = true; clearTimeout(ctxPollT); poll(); } catch (e) { toast(e.message, 1); }
  });
  b.disabled = !!st.running;
  const here = () => /^#\/settings\/?$/.test(location.hash);
  const poll = async () => {
    if (!here()) return;
    const r = await J('/api/ctx').catch(() => null);
    if (!here()) return;
    if (!r) { ctxPollT = setTimeout(poll, 3000); return; }
    if (r.status.running) { note.textContent = r.status.step; b.textContent = 'Tuning…'; ctxPollT = setTimeout(poll, 2000); return; }
    toast(r.status.error ? 'Tune failed: ' + r.status.error : `Context window: ${r.current.num_ctx.toLocaleString()} tokens`, !!r.status.error); again();
  };
  clearTimeout(ctxPollT);
  if (st.running) ctxPollT = setTimeout(poll, 2000);
  return h('div', {}, line, h('div', { class: 'form', style: 'margin-top:6px' }, b), note);
}
function settingsTabs(cur) {
  const tabs = [['general', 'General']].concat(typeof ppSettings === 'function' ? [['paper', 'Newspaper']] : []);
  return tabs.length < 2 ? null : h('div', { class: 'seg', style: 'margin-bottom:14px' }, tabs.map(([k, l]) =>
    h('button', { class: cur === k ? 'on' : '', onclick: () => go(k === 'general' ? '#/settings' : '#/settings/' + k) }, l)));
}
async function settingsPaper() {
  setHeader('Settings', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/paper');
  const again = () => keepScroll(() => VIEWS.settings('paper'));
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Settings')), settingsTabs('paper'),
    h('div', { class: 'muted', style: 'margin-bottom:10px' }, `${d.settings.title} is made every morning at ${d.settings.time}. Today's paper is on the `,
      h('a', { href: '#/paper' }, 'Morning Paper'), ' page.'),
    ppSettings(d, again));
}
// board #820 safety-canvas-link-private
function canvasCard() {
  const box = h('div', { class: 'card' }, h('h3', {}, 'Canvas'), h('div', { class: 'muted' }, 'Loading…'));
  const draw = async () => {
    let c;
    try { c = await J('/api/canvas'); } catch (e) { box.replaceChildren(h('h3', {}, 'Canvas'), h('div', { class: 'err' }, e.message)); return; }
    const url = h('input', { type: 'password', placeholder: 'Paste the link from Canvas: Calendar > Calendar Feed', 'aria-label': 'Canvas calendar feed link', autocomplete: 'off' });
    const save = btn('Save', 'pur', async () => { try { await post('/api/canvas/url', { url: url.value }); toast('Saved. Syncing now…'); await post('/api/canvas/sync'); toast('Canvas due dates are in your calendar'); } catch (e) { toast(e.message, 1); } draw(); });
    box.replaceChildren(h('h3', {}, 'Canvas'),
      h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Puts every upcoming due date from Canvas in your calendar as an all-day event, with a reminder the day before. Runs every morning; nothing is ever deleted.'),
      c.on ? h('div', {}, h('div', {}, `Connected to ${c.feed}`),
        h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, c.last_run ? `Last sync ${c.last_run.replace('T', ' ')}: ${c.upcoming} upcoming, ${c.added} added, ${c.updated} updated.` : 'Not synced yet.'),
        c.error ? h('div', { style: 'color:var(--bad)' }, c.error) : null,
        h('div', { class: 'form', style: 'margin-top:10px' }, btn('Sync now', 'sm', async () => { try { await post('/api/canvas/sync'); toast('Synced'); } catch (e) { toast(e.message, 1); } draw(); }),
          btn('Turn off', 'sm dim', async () => { if (!confirm('Stop syncing Canvas? Events already in your calendar stay.')) return; await post('/api/canvas/url', { url: '' }); draw(); })))
        : h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); save.click(); } }, url, save));
  };
  draw();
  return box;
}
VIEWS.settings = async (arg) => {
  if (arg === 'paper' && typeof ppSettings === 'function') return settingsPaper();
  setHeader('Settings', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const KEYS_AI = [['Alt_R', 'Right Alt'], ['Control_R', 'Right Ctrl'], ['Super_R', 'Super_R'], ['Pause', 'Pause'], ['Scroll_Lock', 'Scroll Lock'], ['Menu', 'Menu key'], ['F8', 'F8'], ['F9', 'F9'], ['F10', 'F10'], ['Shift_L+Super_L+keycode:201', 'Copilot / Cortana key']];
  const [s, au, dc, mc, hl, md, cx, sw] = await Promise.all([J('/api/status'), J('/api/automations'), J('/api/dictation'), J('/api/mcp'), J('/api/health'), J('/api/models').catch((e) => ({ error: e.message })), J('/api/ctx').catch((e) => ({ error: e.message })), J('/api/security').catch((e) => ({ error: e.message }))]);
  const A = au.settings;
  const again = () => keepScroll(() => VIEWS.settings());
  const gpuPct = s.gpu && s.gpu.total_mb ? Math.round(100 * s.gpu.used_mb / s.gpu.total_mb) : 0;
  const bt = h('input', { type: 'time', value: s.brief_time, 'aria-label': 'Brief time' });
  const sp = h('input', { placeholder: 'New space name', 'aria-label': 'New space' });
  const on = h('input', { value: s.owner_name || '', maxlength: 40, placeholder: 'Your first name', 'aria-label': 'Your name' });
  const nn = h('textarea', { rows: 3, maxlength: 600, placeholder: 'For example: keep answers short and give me one next step at a time.', 'aria-label': 'How you like help' }, s.owner_notes || '');
  const setTarget = async (v) => {
    if (v === s.write_target) return;
    if (v === 'primary' && !confirm('The assistant will add, move and delete events on your REAL Google Calendar. Deleting, or changing more than 3 events, will still ask you first. Continue?')) return;
    try { await post('/api/settings', { write_target: v }); toast('Calendar writes now go to the ' + v.toUpperCase() + ' calendar'); again(); } catch (e) { toast(e.message, 1); }
  };
  const setBrain = async (v) => {
    if (v === s.brain) return;
    try { await post('/api/settings', { brain: v }); toast(v === 'agent_zero' ? 'Agent Zero now answers chat, with Rosie\'s tools' : 'Rosie\'s own model answers chat again'); again(); } catch (e) { toast(e.message, 1); }
  };
  const setPlanning = async (v) => {
    if (v === s.planning.on) return;
    try { await post('/api/settings', { claude_planning: v }); toast(v ? 'Planning requests now go to Claude' : 'Rosie plans with her own model again'); again(); } catch (e) { toast(e.message, 1); }
  };
  const setOrch = async (v) => {
    if (v === s.orchestrator) return;
    try { const r = await post('/api/settings', { orchestrator: v }); if (r.error) throw new Error(r.error); toast(v ? 'Claude now orchestrates: Rosie and Agent Zero do the work' : 'Claude works on its own again'); again(); } catch (e) { toast(e.message, 1); }
  };
  const saveSpaces = async (list) => { try { await put('/api/spaces', { spaces: list }); await refreshSide(); again(); } catch (e) { toast(e.message, 1); } };
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v)];
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Settings')), settingsTabs('general'),
    h('div', { class: 'grid' },
      h('div', { class: 'card' }, h('h3', {}, 'System'), h('dl', { class: 'kv' },
        row('Display', lookBox(again)),
        row('AI model', modelPicker(md, s.model, again)),
        row('Context window', ctxBox(cx, again)),
        row('Security', secBox(sw, again)),
        row('Brain', h('div', {}, h('div', { class: 'seg' }, h('button', { class: s.brain !== 'agent_zero' ? 'on' : '', onclick: () => setBrain('rosie') }, 'Rosie'), h('button', { class: s.brain === 'agent_zero' ? 'on' : '', onclick: () => setBrain('agent_zero') }, 'Agent Zero')), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'Who answers chat, voice and the phone app. Agent Zero is better at big tasks but slower; it uses Rosie\'s tools, and texts, calls, deletes and PC commands still wait for your Yes. If it is down, Rosie answers.'))),
        row('Planning', h('div', {}, h('div', { class: 'seg' }, h('button', { class: s.planning.on ? 'on' : '', onclick: () => setPlanning(true) }, 'Claude'), h('button', { class: s.planning.on ? '' : 'on', onclick: () => setPlanning(false) }, 'Rosie\'s own model')), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, s.planning.line + ' Claude only plans (it gets your calendar, tasks and projects for the week, never mail or notes); Rosie does the work, with her usual Yes cards. Spoken requests skip it.'))),
        s.orchestrator === null ? null : row('Orchestrator', h('div', {}, h('div', { class: 'seg' }, h('button', { class: s.orchestrator ? 'on' : '', onclick: () => setOrch(true) }, 'On'), h('button', { class: s.orchestrator ? '' : 'on', onclick: () => setOrch(false) }, 'Off')), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'On: your Claude Code sessions plan and check, and Rosie and Agent Zero do the work until Claude is satisfied. Same switch as "delegate on" and "delegate off".'))),
        row('GPU', s.gpu.name ? [s.gpu.name, h('div', { class: 'bar' }, h('i', { style: 'width:' + gpuPct + '%' })), h('span', { class: 'muted' }, `${s.gpu.used_mb} / ${s.gpu.total_mb} MB`)] : (s.gpu.error || 'not found')),
        row('Google Calendar', s.google.ok ? 'connected' : h('span', { style: 'color:var(--bad)' }, (s.google.error || 'login needed') + ' · ' + loginHint)),
        row('Phone address', s.tailscale.name ? s.tailscale.name : (s.tailscale.error || 'Tailscale not running')),
        row('Disk free', s.disk_free_gb + ' GB'))),
      h('div', { class: 'card', style: 'grid-column:1/-1' }, h('h3', {}, 'Menu'), menuBox(again)),
      h('div', { class: 'card' }, h('h3', {}, 'Health'),
        hl.rows.length ? h('div', { class: 'list' }, hl.rows.map(healthRow)) : h('div', { class: 'muted' }, 'Checking everything… open this page again in a few seconds.'),
        h('div', { class: 'form', style: 'margin-top:10px' }, btn('Check again', 'sm', async () => { toast('Checking…'); await J('/api/health?fresh=true'); again(); }))),
      h('div', { class: 'card' }, h('h3', {}, 'Calendar writes'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Where the assistant adds and changes events. Reading always uses your real calendar.'),
        h('div', { class: 'seg' + (s.write_target === 'primary' ? ' warn' : '') }, h('button', { class: s.write_target === 'test' ? 'on' : '', onclick: () => setTarget('test') }, 'Test calendar'), h('button', { class: s.write_target === 'primary' ? 'on' : '', onclick: () => setTarget('primary') }, 'Real calendar'))),
      canvasCard(),
      h('div', { class: 'card wide', id: 'autocard' }, h('h3', {}, 'Automations'),
        h('div', { class: 'muted', style: 'margin-bottom:12px' }, 'What I do on my own and when I reach out to you. By default I only notify you. The "auto" modes, where I do the work for you (sorting notes, wrapping up your day), are off until you choose them.'),
        h('div', { class: 'kv' },
          ...autoRow('Daily brief', onOff(A.brief_on, (v) => autoSet({ brief_on: v })), timeIn(A.brief_time, (v) => autoSet({ brief_time: v })), 'Made automatically at this time.'),
          ...autoRow('Meeting reminders', onOff(A.remind_events, (v) => autoSet({ remind_events: v })), numSel(A.remind_lead_min, [5, 10, 15, 30], (v) => autoSet({ remind_lead_min: v }), ' min before'), 'Pop-up shortly before each calendar event.'),
          ...autoRow('Overdue nudge', onOff(A.nudge_overdue, (v) => autoSet({ nudge_overdue: v })), timeIn(A.nudge_overdue_time, (v) => autoSet({ nudge_overdue_time: v })), 'Once a day if tasks are overdue.'),
          ...autoRow('Scratch pad', modeSel(A.scratch_mode, (v) => autoSet({ scratch_mode: v })), null, 'nudge = tell me when notes pile up; auto = sort them into tasks for me.'),
          ...autoRow('Evening wrap-up', modeSel(A.wrapup_mode, (v) => autoSet({ wrapup_mode: v })), timeIn(A.wrapup_time, (v) => autoSet({ wrapup_time: v })), 'nudge = remind me; auto = run the end-of-day routine for me.'),
          ...autoRow('Desktop pop-ups', onOff(A.desktop_notify, (v) => autoSet({ desktop_notify: v })), null, 'Reminders you set always pop up, even in quiet hours.'),
          ...autoRow('Mail monitoring', onOff(A.mail_on, (v) => autoSet({ mail_on: v })), numSel(A.mail_check_min, [5, 10, 15, 30, 60], (v) => autoSet({ mail_check_min: v }), ' min'), 'Checks Gmail (read-only) in the background.'),
          ...autoRow('Flag important mail', onOff(A.mail_flag, (v) => autoSet({ mail_flag: v })), null, 'Pop-up + Inbox item for mail that needs you.'),
          ...autoRow('Find tasks and dates', onOff(A.mail_find_tasks, (v) => autoSet({ mail_find_tasks: v })), null, 'Dates in mail become calendar requests you approve (an event is only added, nothing moves). To-dos go to the Mail page, not Tasks.'),
          ...autoRow('Sort mail into my labels', onOff(A.mail_organize, (v) => autoSet({ mail_organize: v })), null, 'Picks one of YOUR Gmail labels (chosen below) for new mail. You see a preview on the Mail page and press OK; nothing is deleted.'),
          ...autoRow('Take sorted mail out of the Inbox', onOff(A.mail_organize_archive, (v) => autoSet({ mail_organize_archive: v })), null, 'Off: mail is only labeled. On: it is also archived (kept in All Mail). Undo puts it back.'),
          ...autoRow('Labels Rosie may use', labelPicker(), null, 'Tick the labels and add a few words to help (for example "school and class mail"). None are used until you tick them.'),
          ...autoRow('Email in daily brief', onOff(A.mail_digest, (v) => autoSet({ mail_digest: v })), null, 'A short email section in the morning brief.'),
          ...autoRow('Safety: confirm after untrusted text', onOff(A.taint_guard, (v) => autoSet({ taint_guard: v })), null, 'After I read email, web pages or outside files, running commands, writing files or launching agents needs your OK. Recommended ON.'),
          ...autoRow('Weekly project review', onOff(A.weekly_review_on, (v) => autoSet({ weekly_review_on: v })), timeIn(A.weekly_review_time, (v) => autoSet({ weekly_review_time: v })), 'Sundays: which projects are stalled or have no next action.'),
          ...autoRow('Agent alerts', onOff(A.agents_notify, (v) => autoSet({ agents_notify: v })), null, 'Pop-up when a Claude/Codex/Gemini session finishes or is waiting for your answer.'),
          ...autoRow('Quiet hours', timeIn(A.quiet_from, (v) => autoSet({ quiet_from: v })), timeIn(A.quiet_to, (v) => autoSet({ quiet_to: v })), 'No pop-ups between these times (items still go to the Inbox).')),
        h('div', { class: 'form', style: 'margin-top:12px' }, btn('Send a test notification', 'sm', async () => { try { const r = await post('/api/automations/test'); toast(r.desktop_shown ? 'Sent. Look for the pop-up.' : 'Could not show a desktop pop-up.', !r.desktop_shown); } catch (e) { toast(e.message, 1); } }),
          h('span', { class: 'muted' }, au.last_tick ? 'engine last ran ' + ago(au.last_tick) : 'engine has not run yet' + (au.last_error ? ' · last problem: ' + au.last_error : '')))),
      h('div', { class: 'card' }, h('h3', {}, 'Dictation (hold to talk)'),
        h('div', { class: 'form', style: 'margin-bottom:8px' }, h('span', { class: 'chip ' + (dc.service === 'active' ? 'ok' : 'bad') }, dc.service === 'active' ? 'running' : dc.installed ? 'stopped' : 'not installed'),
          !dc.tools.xdotool || !dc.tools.xclip ? h('span', { class: 'chip bad' }, 'missing: ' + ['xdotool', 'xclip'].filter((t) => !dc.tools[t]).join(', ')) : null, dc.status && dc.status.latency ? h('span', { class: 'chip' }, 'last: ' + dc.status.latency + 's') : null),
        h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Hold the key anywhere you can type, speak, release: the text appears. Press any other key while holding to cancel (so Ctrl+C still works). Runs fully on this PC. X11 only.'),
        h('dl', { class: 'kv' }, ...autoRow('Enabled', onOff(A.dictation_on, (v) => autoSet({ dictation_on: v })), null, ''),
          ...autoRow('Hotkey', h('select', { 'aria-label': 'Hotkey', onchange: (e) => autoSet({ dictation_hotkey: e.target.value }) }, [['Control_R', 'Control_R (right Ctrl)'], ['Alt_R', 'Alt_R (right Alt)'], ['Super_R', 'Super_R'], ['Pause', 'Pause'], ['Scroll_Lock', 'Scroll_Lock'], ['Menu', 'Menu'], ['F8', 'F8'], ['F9', 'F9'], ['F10', 'F10'], ['Shift_L+Super_L+keycode:201', 'Copilot / Cortana key (Shift+Win+F23)'], ['Shift_L+Super_L+F23', 'Shift+Win+F23 (named key)']].map(([k, label]) => h('option', { value: k, selected: k === A.dictation_hotkey }, label))), null, 'Hold it to talk. Pick the Copilot/Cortana key if your keyboard has one.'),
          ...autoRow('Clean-up', onOff(A.dictation_cleanup, (v) => autoSet({ dictation_cleanup: v })), null, 'Removes um/uh and repeated words. Never rewrites what you said.')),
        h('div', { class: 'form', style: 'margin-top:10px' }, dc.installed ? btn('Restart (apply changes)', 'sm', async () => { const r = await post('/api/dictation/service', { action: 'restart' }); toast(r.ok ? 'Restarted' : r.output, !r.ok); again(); }) : btn('Install and start', 'ok', async () => { const r = await post('/api/dictation/service', { action: 'install' }); toast(r.ok ? 'Installed. Hold the hotkey in any text box.' : r.output, !r.ok); again(); }),
          dc.installed ? btn('Remove', 'sm bad', async () => { if (!confirm('Stop and remove dictation?')) return; await post('/api/dictation/service', { action: 'remove' }); again(); }) : null)),
      h('div', { class: 'card' }, h('h3', {}, 'Talk to the AI anywhere'),
        h('div', { class: 'form', style: 'margin-bottom:8px' }, h('span', { class: 'chip ' + (dc.overlay === 'active' ? 'ok' : 'bad') }, dc.overlay === 'active' ? 'octopus running' : dc.installed ? 'octopus stopped' : 'not installed'),
          dc.ai_status && dc.ai_status.latency ? h('span', { class: 'chip' }, 'last answer: ' + dc.ai_status.latency + 's') : null),
        h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Press and hold the key (or the round octopus button on your screen), ask out loud, let go: I answer out loud while a glitchy octopus shows over your screen. Press again while I talk to interrupt. Say "yes" or "no" to confirm things, "new chat" to start over. Everything stays in the "Voice (desktop)" chat.'),
        h('dl', { class: 'kv' }, ...autoRow('Enabled', onOff(A.ai_on, (v) => autoSet({ ai_on: v })), null, ''),
          ...autoRow('Hotkey', h('select', { 'aria-label': 'AI hotkey', onchange: (e) => autoSet({ ai_hotkey: e.target.value }) }, KEYS_AI.map(([k, label]) => h('option', { value: k, selected: k === A.ai_hotkey }, label))), null, 'Must be a different key from dictation.'),
          ...autoRow('Round button', onOff(A.ai_orb_on, (v) => autoSet({ ai_orb_on: v })), h('select', { 'aria-label': 'Button corner', onchange: (e) => autoSet({ ai_orb_corner: e.target.value }) }, ['bottom-right', 'bottom-left', 'top-right', 'top-left'].map((k) => h('option', { value: k, selected: k === A.ai_orb_corner }, k))), 'Press and hold it with the mouse instead of a key.'),
          ...autoRow('Octopus style', h('select', { 'aria-label': 'Octopus style', onchange: (e) => autoSet({ ai_overlay_mode: e.target.value }) }, [['full', 'Full screen glitch'], ['subtle', 'Small, corner only'], ['reduced', 'Reduced motion (no flicker)'], ['off', 'Off (button only)']].map(([k, label]) => h('option', { value: k, selected: k === A.ai_overlay_mode }, label))), null, 'Reduced motion is gentler if flashing bothers your eyes.'),
          ...autoRow('Speak answers', onOff(A.ai_speak, (v) => autoSet({ ai_speak: v })), null, 'Off: the octopus shows the answer text instead.'),
          ...autoRow('Use highlighted text', onOff(A.ai_use_selection, (v) => autoSet({ ai_use_selection: v })), null, 'Text you have selected on screen is sent along with your question, as something to read, never as instructions.')),
        h('div', { class: 'muted', style: 'margin-top:8px' }, 'Uses the same background service as dictation: use its Restart button above after changing keys.')),
      window.AndroidBridge ? (() => {
        return h('div', { class: 'card' }, h('h3', {}, 'Self-test (this phone)'),
          h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Checks everything Rosie needs on this phone in about 20 seconds, silently (no sound is played, nothing is sent), and tells you exactly what to fix.'),
          h('div', { class: 'form' }, btn('Run self-test', 'ok', () => window.runSelfTest()), btn('Check for app update', 'sm', async () => {
            try { const v = await J('/api/android-version'); const mine = AndroidBridge.appVersionCode();
              if (v.code > mine) { toast('Update ' + v.name + ' is ready. Downloading…'); location.href = '/api/android-app'; } else toast('You have the newest version (' + AndroidBridge.appVersionName() + ').'); } catch (e) { toast(e.message, 1); } })),
          h('div', { id: 'selftest-out', style: 'margin-top:10px' }));
      })() : null,
      window.AndroidBridge ? (() => {
        const p = JSON.parse(AndroidBridge.phoneStatus() || '{}');
        window.onAppResume = () => { if (location.hash.startsWith('#/settings')) again(); };
        const chip = (ok, t) => h('span', { class: 'chip ' + (ok ? 'ok' : 'bad') }, t);
        return h('div', { class: 'card' }, h('h3', {}, 'Phone control'),
          h('div', { class: 'form', style: 'margin-bottom:8px' }, chip(p.linked, p.linked ? 'connected to your PC' : p.on ? 'connecting…' : 'off'), chip(p.contacts, 'contacts'), chip(p.sms, 'texts'), chip(p.call, 'calls'), chip(p.notif_access, 'notifications'), chip(p.screen_control, 'screen')),
          h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Lets Rosie use this phone for you: add Google Keep notes, text and call people, read your texts, set alarms and timers, open apps, play and pause music, flashlight, volume, directions and searches. Texts and calls ALWAYS ask you first (a card, or say "yes"), and she tells you exactly who and what. Needs the phone connected to your PC (Tailscale on).'),
          h('div', { class: 'form' }, p.on ? btn('Turn off', 'sm bad', () => { AndroidBridge.setPhoneControl(false); setTimeout(again, 600); }) : btn('Let Rosie use my phone', 'ok', () => { AndroidBridge.setPhoneControl(true); setTimeout(again, 900); }),
            p.on && !(p.contacts && p.sms && p.readsms && p.call) ? btn('Allow contacts, texts and calls', 'sm pur', () => AndroidBridge.askPhonePermissions()) : null),
          p.on ? h('div', { style: 'margin-top:12px' },
            h('div', { class: 'form' }, p.notif_access ? null : btn('Allow notification access', 'sm pur', () => AndroidBridge.openNotificationAccess()), p.screen_control ? null : btn('Turn on screen control', 'sm pur', () => AndroidBridge.openScreenControl())),
            h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Notification access lets her read your notifications and answer messages with their reply box. Screen control lets her look at the screen and tap, type and scroll for you (find "Rosie screen control" under Android Settings > Accessibility; Android may ask you to allow restricted settings first). Banking, payment, password and security screens are blocked, she never types passwords, Send / Pay / Delete / Buy buttons always ask you, a "Rosie is controlling your screen" notice with a Stop button shows the whole time, and she gives up after 12 steps.')) : null,
          h('div', { class: 'muted', style: 'margin:14px 0 4px;font-weight:700' }, 'How she confirms (like Siri and Gemini)'),
          h('dl', { class: 'kv' },
            ...autoRow('Texts', h('select', { 'aria-label': 'Texts', onchange: (e) => autoSet({ sms_confirm: e.target.value }) }, [['ask', 'Read it back, wait for send / change / cancel'], ['headphones', 'Auto-send when headphones are on'], ['always', 'Always auto-send']].map(([k, t]) => h('option', { value: k, selected: k === A.sms_confirm }, t))), null, 'Auto-send says what it is sending, then waits 3 seconds for you to say "cancel" before sending.'),
            ...autoRow('Calls', h('select', { 'aria-label': 'Calls', onchange: (e) => autoSet({ call_confirm: e.target.value }) }, [['announce', 'Say who, call after 3 seconds (cancel any time)'], ['ask', 'Ask first'], ['now', 'Call right away']].map(([k, t]) => h('option', { value: k, selected: k === A.call_confirm }, t))), null, 'If a name matches more than one person she asks which one.'),
            ...autoRow('When I pause', h('select', { 'aria-label': 'Pause length', onchange: (e) => { AndroidBridge.setIntPref('endpoint_ms', parseInt(e.target.value, 10)); } }, [[800, 'Quick: answer after a short pause'], [1200, 'Normal'], [2000, 'Patient: wait while I think']].map(([k, t]) => h('option', { value: k, selected: k === AndroidBridge.getIntPref('endpoint_ms', 1200) }, t))), null, 'How long she waits after you stop talking before she starts answering.')),
          h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Notes, alarms, timers, music, navigation and searches are just done and she tells you what she did. Deleting things, mail cleanup and anything that spends money always asks first.'));
      })() : null,
      window.AndroidBridge ? (() => {
        const w = JSON.parse(AndroidBridge.wakeStatus() || '{}');
        window.onAppResume = () => { if (location.hash.startsWith('#/settings')) again(); };
        return h('div', { class: 'card' }, h('h3', {}, 'Hey Rosie (this phone)'),
          h('div', { class: 'form', style: 'margin-bottom:8px' }, h('span', { class: 'chip ' + (w.running ? 'ok' : 'bad') }, w.running ? 'listening for "Hey Rosie"' : w.on ? 'starting / needs permission' : 'off')),
          h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Say "Hey Rosie" and the app opens and starts listening, like "Hey Google". It listens only on this phone with a small offline model: nothing is sent or saved until you hear the chime. Android shows its green microphone dot and a notification while it is on, and it uses some battery.'),
          h('div', { class: 'form' }, w.on ? btn('Turn off', 'sm bad', () => { AndroidBridge.setWake(false); setTimeout(again, 600); }) : btn('Turn on "Hey Rosie"', 'ok', () => { AndroidBridge.setWake(true); setTimeout(again, 900); })),
          h('div', { class: 'muted', style: 'margin:12px 0 6px' }, (() => { const ti = JSON.parse(AndroidBridge.trainInfo() || '{}'); return ti.trained ? 'Trained on your voice (' + ti.trained + ' of 6 clear). Also accepts: ' + (ti.aliases.split('|').filter(Boolean).join(', ') || 'the standard phrase') + '.' : 'Not trained yet. Say "Hey Rosie" six times so she learns your voice and how loudly you speak; this makes her much less likely to wake by mistake.'; })()),
          h('div', { class: 'form' }, btn('Train my voice', 'sm pur', () => window.startTraining()), JSON.parse(AndroidBridge.trainInfo() || '{}').trained ? btn('Reset training', 'sm', () => { AndroidBridge.trainReset(); again(); }) : null),
          (() => {
            const flag = (name, label, hint) => { const on = AndroidBridge.getFlag(name); return h('div', { style: 'margin-top:12px' }, h('div', { class: 'form' }, btn(label + ': ' + (on ? 'ON' : 'OFF'), on ? 'ok' : 'sm', () => { AndroidBridge.setFlag(name, !on); setTimeout(again, 600); })), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, hint)); };
            const asst = AndroidBridge.isAssistant();
            return h('div', {}, h('div', { class: 'muted', style: 'margin-top:12px' }, 'Microphone in use: ' + AndroidBridge.micInfo()),
              h('div', { class: 'muted', style: 'margin-top:14px;font-weight:700' }, 'Sounds'),
              (() => {
                const style = (AndroidBridge.getPref('sound_style', 'game') === 'glitch' ? 'game' : AndroidBridge.getPref('sound_style', 'game')), vol = AndroidBridge.getPref('sound_vol', 'medium');
                const sel = (key, cur, opts, label) => h('select', { 'aria-label': label, onchange: (e) => { AndroidBridge.setPref(key, e.target.value); AndroidBridge.previewSound('wake', key === 'sound_style' ? e.target.value : AndroidBridge.getPref('sound_style', 'game')); } }, opts.map(([k, t]) => h('option', { value: k, selected: k === cur }, t)));
                const prev = (kind, label) => btn(label, 'sm', () => AndroidBridge.previewSound(kind, AndroidBridge.getPref('sound_style', 'game')));
                return h('div', {}, h('div', { class: 'form', style: 'margin-top:8px' }, sel('sound_style', style, [['game', 'Video game'], ['soft', 'Soft chime'], ['off', 'No sounds']], 'Sound style'), sel('sound_vol', vol, [['low', 'Quiet'], ['medium', 'Medium'], ['high', 'Loud']], 'Sound volume')),
                  h('div', { class: 'form', style: 'margin-top:8px' }, prev('on', '▶ Turned on'), prev('wake', '▶ Listening'), prev('thinking', '▶ Thinking'), prev('done', '▶ Done')),
                  h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'Listening: she heard you and is ready. Thinking: she stopped listening and is working. Done: the conversation ended. Tap a button to hear it.'));
              })(),
              (() => { const off = AndroidBridge.getFlag('novibe'); return h('div', { class: 'form', style: 'margin-top:10px' }, btn('Vibration: ' + (off ? 'OFF' : 'ON'), off ? 'sm' : 'ok', () => { AndroidBridge.setFlag('novibe', !off); setTimeout(again, 400); })); })(),
              (() => { const on = AndroidBridge.getFlagD('pocketrule', true); return h('div', { style: 'margin-top:12px' }, h('div', { class: 'form' }, btn('In a pocket, only use headphones: ' + (on ? 'ON' : 'OFF'), on ? 'ok' : 'sm', () => { AndroidBridge.setFlag('pocketrule', !on); setTimeout(again, 500); })), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'When the phone is covered (a pocket or bag) with the screen off, Rosie only listens through a Bluetooth or wired headset microphone. With no headset she stays off. Out of a pocket she works as normal.')); })(),
              flag('btmic', 'Listen through my Bluetooth headset', 'Hears you through the headset microphone even with the phone in a pocket. While on, music through the headset sounds like a phone call. A wired or USB headset is used automatically.'),
              h('div', { style: 'margin-top:12px' }, h('div', { class: 'form' }, asst ? h('span', { class: 'chip ok' }, 'headphone button opens Rosie') : btn('Use my headphone button for Rosie', 'sm pur', () => AndroidBridge.setAssistant())), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, asst ? 'Press and hold the button on your headphones (or the power-button assistant gesture) and Rosie starts listening. This replaces Google Assistant for those buttons, so "Hey Google" stops working.' : 'Makes Rosie your phone\'s assistant, so holding the headphone button starts her instead of Google. Android asks you to confirm.')));
          })(),
          w.battery === false ? h('div', { style: 'margin-top:10px' }, h('div', { class: 'form' }, btn('Let Rosie run in the background', 'sm pur', () => AndroidBridge.askBatteryUnrestricted())), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'Without this Android may stop her to save battery, so "Hey Rosie" and phone control would quietly stop working.')) : null,
          w.on && !w.overlay ? h('div', { class: 'muted', style: 'margin-top:8px' }, 'Tip: allow "Display over other apps" for Rosie to show the octopus popup over other apps. Without it she still listens and talks, just without the popup.') : null);
      })() : null,
      h('div', { class: 'card' }, h('h3', {}, 'Android app'),
        h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'The assistant as a real app: microphone, a Talk shortcut (long-press the icon), and Share > Rosie from any app. It connects to this assistant over Tailscale, so Tailscale must be on. Open this page on the phone, tap the button, and allow installing from this browser when Android asks.'),
        h('a', { class: 'btn ok', href: '/api/android-app', style: 'text-decoration:none;display:inline-block' }, 'Download Rosie.apk')),
      h('div', { class: 'card' }, h('h3', {}, 'Meetings and names'),
        h('dl', { class: 'kv' }, ...autoRow('Keep audio', onOff(A.meeting_keep_audio, (v) => autoSet({ meeting_keep_audio: v })), null, 'Off (recommended): audio is deleted once the transcript exists.'),
          ...autoRow('Max length', numSel(A.meeting_max_minutes, [30, 60, 120, 180, 240], (v) => autoSet({ meeting_max_minutes: v }), ' min'), null, 'A recording stops by itself at this limit.')),
        h('div', { class: 'muted', style: 'margin:10px 0 4px' }, 'Names and terms to spell right (comma separated) in dictation and transcripts:'),
        h('textarea', { value: A.glossary, placeholder: 'Siobhan, Kokoro, Ollama, Meshtastic…', style: 'width:100%;min-height:60px', 'aria-label': 'Glossary', onchange: (e) => autoSet({ glossary: e.target.value }) })),
      h('div', { class: 'card wide' }, h('h3', {}, 'Connect AI agents to your notes (MCP)'),
        h('div', { class: 'form', style: 'margin-bottom:8px' }, h('span', { class: 'chip ' + (mc.registered ? 'ok' : 'bad') }, mc.registered ? 'connected to Claude Code' : 'not registered with Claude Code'), h('span', { class: 'chip' }, 'read-only'), h('span', { class: 'chip' }, 'shares: ' + mc.root.replace(/^\/home\/[^/]+/, '~') + ' (all folders)')),
        h('div', { style: 'margin-bottom:8px', class: 'err' }, 'Heads-up: when an AI agent such as Claude Code reads a note or file through this, that text is sent to the cloud AI. It is NOT private any more. Keys, passwords, logins and app data are blocked and token-like text is hidden, but personal documents and meeting transcripts are readable.'),
        h('div', { class: 'muted' }, 'Tools: ' + mc.tools.join(', ')),
        h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Always blocked (' + mc.blocked.length + ')'), h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, mc.blocked.join('  ·  ') + '  ·  your .ssh keys, .env files, password/token/credential files, the assistant\'s secrets and databases')),
        h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Recent requests from agents (' + mc.audit.length + ')'), h('pre', { style: 'font-size:calc(12px*var(--ts,1));white-space:pre-wrap;color:var(--mute)' }, mc.audit.join('\n') || 'none yet')),
        h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Add to another agent (Cursor, VS Code…)'), h('pre', { style: 'font-size:calc(12px*var(--ts,1));white-space:pre-wrap' }, JSON.stringify({ mcpServers: { 'assistant-notes': { command: mc.command.split(' ')[0], args: [mc.command.split(' ')[1]] } } }, null, 2)))),
      h('div', { class: 'card' }, h('h3', {}, 'About you'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'What Rosie calls you, and how you like help. Both go into her prompts and Agent Zero\'s. They stay on this PC.'),
        h('form', { class: 'form col', onsubmit: async (e) => { e.preventDefault(); try { await post('/api/settings', { owner_name: on.value, owner_notes: nn.value }); toast('Saved'); } catch (er) { toast(er.message, 1); } } }, on, nn, btn('Save', 'pur', null, 'submit'))),
      h('div', { class: 'card' }, h('h3', {}, 'Memory'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'What Rosie learns from your chats: facts, and beliefs that grow as she hears more. She reads chats while you aren\'t using her, and it all stays on this PC. Ask her "what do you remember about…", or tell her to forget something.'),
        (() => {
          const d = h('div', {}, 'Loading…');
          const fill = (m) => {
            d.replaceChildren(h('div', {}, m.line),
              h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'What she remembers (' + m.recent.length + ' newest; beliefs first)'),
                h('div', { class: 'list' }, m.recent.map((x) => h('div', { class: 'item' },
                  h('span', { class: 'tx' }, (x.kind === 'observation' ? 'Belief (' + x.proof + ' facts): ' : '') + x.text + ' · ' + x.when),
                  h('button', { class: 'x', 'aria-label': 'Forget: ' + x.text.slice(0, 60), title: 'Forget this', onclick: async () => { try { await del('/api/mind/' + x.id); toast('Forgotten'); fill(await J('/api/mind')); } catch (er) { toast(er.message, 1); } } }, '×'))))));
          };
          J('/api/mind').then(fill).catch((er) => { d.textContent = er.message; });
          return d;
        })()),
      h('div', { class: 'card' }, h('h3', {}, 'Daily brief'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Made automatically at this time (when the PC is awake).'),
        h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); try { await post('/api/settings', { brief_time: bt.value }); toast('Saved'); } catch (er) { toast(er.message, 1); } } }, bt, btn('Save', 'pur', null, 'submit'))),
      h('div', { class: 'card' }, h('h3', {}, 'Spaces'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Folders for chats. A space also tells the assistant the topic (Food, Build, Ideas and Life load matching skills).'),
        h('div', { class: 'list' }, s.spaces.map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' }, x), h('button', { class: 'x', 'aria-label': 'Remove ' + x, onclick: () => { if (s.spaces.length > 1 && confirm('Remove the space "' + x + '"? Its chats move to Inbox view.')) saveSpaces(s.spaces.filter((y) => y !== x)); } }, '✕')))),
        h('form', { class: 'form', style: 'margin-top:10px', onsubmit: (e) => { e.preventDefault(); if (sp.value.trim()) saveSpaces([...s.spaces, sp.value]); } }, sp, btn('Add', 'pur', null, 'submit'))),
      h('div', { class: 'card' }, h('h3', {}, 'Security'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Your access code is in ~/assistant/secrets/access_code. Lost a phone? Sign out everywhere; every device must enter the code again.'),
        btn('Sign out all devices', 'bad', async () => { if (!confirm('Sign out every device, including this one?')) return; await post('/api/signout-all'); location.reload(); })),
      h('div', { class: 'card' }, h('h3', {}, 'Help'), h('div', {}, 'Start, stop and update steps are in ', h('code', {}, '~/assistant/README.md'), '. Type ', h('code', {}, '/help'), ' in any chat for commands.'))));
  if (window.AndroidBridge) {
    const pcOnly = ['Dictation (hold to talk)', 'Talk to the AI anywhere', 'Android app', 'Meetings and names', 'Connect AI agents to your notes (MCP)'];
    document.querySelectorAll('.card').forEach((c) => { const t = c.querySelector('h3'); if (t && pcOnly.includes(t.textContent.trim())) c.remove(); });
  }
};

const BIG = [['⏱ What now?', () => whatnow()], ['🏃 Running late', () => send("I'm running late. Ask me how many minutes behind I am, then fix my afternoon.")], ['➕ Add task', () => addTask()]];
$('#btns').replaceChildren(...BIG.map(([l, f]) => h('button', { type: 'button', onclick: f }, l)));
const GREETING = "Hi, I'm Rosie. Tell me what you need: type, or tap TALK and speak. Type / for commands. I'll tell you straight if something won't work.";
function add(txt, cls) { const d = document.createElement('div'); d.className = 'm ' + cls; if (cls.includes('bot')) d.innerHTML = fmt(String(txt)); else d.textContent = txt; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }
function setChatHeader() { setHeader(chatTitle + (chatSpace ? '  [' + chatSpace + ']' : ''), [...(a0Usable(a0Chat) ? [btn('Agent Zero', 'sm pur', () => { store.set('a0_classic', false); go('#/chat'); })] : []), ...chatId ? [btn('✎', 'sm dim', async () => { const v = prompt('Rename chat:', chatTitle); if (!v) return; await patch('/api/chats/' + chatId, { title: v }); chatTitle = v; setChatHeader(); refreshSide(); }),
  btn('🗑', 'sm bad', async () => { if (!confirm('Delete this chat for good?')) return; await del('/api/chats/' + chatId); newChat(); refreshSide(); })] : []]); }
function resetLog() { log.innerHTML = ''; add(GREETING, 'bot'); }
async function api(p, b) {
  if (['/api/chat', '/api/command', '/api/slash'].includes(p)) { b = { ...b, chat_id: chatId }; if (!chatId && chatSpace) b.space = chatSpace; }
  const r = await post(p, b);
  if (r.chat_id) {
    const isNew = r.chat_id !== chatId;
    chatId = r.chat_id; store.set('chat_id', chatId); chatSpace = r.space || null; if (r.title) chatTitle = r.title;
    if (isNew) { history.replaceState(null, '', '#/chat/' + chatId); prevHash = location.hash; refreshSide(); }
    setChatHeader();
  }
  return r;
}
async function loadChat(id) {
  let d; try { d = await J('/api/chats/' + id); } catch (e) { return false; }
  chatId = id; store.set('chat_id', id); chatTitle = d.title; chatSpace = d.space || null; log.innerHTML = '';
  if (!d.messages.length) add(GREETING, 'bot');
  d.messages.forEach((m) => add(m.text, m.role === 'user' ? 'me' : 'bot'));
  log.scrollTop = log.scrollHeight; return true;
}
let a0Chat = null, a0ChatAt = 0, classicOnce = false;
const a0Classic = () => store.get('a0_classic', false) === true;
async function a0ChatInfo() {
  if (a0Chat && Date.now() - a0ChatAt < 8000) return a0Chat;
  try { a0Chat = await J('/api/a0/proxy'); } catch (e) { a0Chat = null; }
  a0ChatAt = Date.now(); return a0Chat;
}
const a0Usable = (m) => !!(m && m.brain === 'agent_zero' && m.chat_ui && m.on && m.answers);
async function showA0Chat(m) {
  document.body.classList.add('a0-chat');
  const url = `${location.protocol}//${location.hostname}:${m.port}/`;
  const classic = btn('Classic chat', 'sm dim', () => { store.set('a0_classic', true); route(); });
  setHeader('Agent Zero', [btn('⟳', 'sm dim', () => { const f = $('#a0frame'); if (f) f.src = f.src; }), classic]);
  let box = $('#a0box');
  if (!box) { box = h('div', { id: 'a0box' }); $('#chatview').appendChild(box); }
  if (box.dataset.url === url && $('#a0frame')) return;
  box.dataset.url = url; box.replaceChildren();
  const ok = await new Promise((res) => {
    const c = new AbortController(); const tm = setTimeout(() => c.abort(), 4000);
    fetch(url, { mode: 'no-cors', credentials: 'omit', cache: 'no-store', signal: c.signal }).then(() => { clearTimeout(tm); res(true); }, () => { clearTimeout(tm); res(false); });
  });
  if (!ok) {
    box.dataset.url = '';
    box.appendChild(h('div', { class: 'card a0note' }, h('h3', {}, 'This device can\'t reach Agent Zero\'s page yet'),
      h('div', { class: 'muted' }, m.phone_served === false ? 'On the PC, run this once so Tailscale serves the page over HTTPS, then reload:' : 'Agent Zero\'s page is on port ' + m.port + '. If this is your phone, run this once on the PC, then reload:'),
      h('pre', { class: 'a0-log' }, m.phone_command),
      h('div', { class: 'form' }, btn('Classic chat', 'sm pur', () => { store.set('a0_classic', true); route(); }), btn('Try again', 'sm', () => { box.dataset.url = ''; showA0Chat(m); }))));
    return;
  }
  box.appendChild(h('iframe', { id: 'a0frame', src: url, title: 'Agent Zero: chat with all its tools, projects and settings', allow: 'clipboard-read; clipboard-write; microphone' }));
}
async function showChat(arg) {
  const m = await a0ChatInfo(), once = classicOnce; classicOnce = false;
  if (!arg && !once && !a0Classic() && a0Usable(m)) return showA0Chat(m);
  document.body.classList.remove('a0-chat');
  if (arg) {
    const id = +arg;
    if (id !== chatId || !log.children.length) { const ok = await loadChat(id); if (!ok) { toast('That chat no longer exists', 1); newChat(); return; } }
  } else if (!log.children.length) resetLog();
  setChatHeader(); setTimeout(() => { log.scrollTop = log.scrollHeight; }, 0);
}
function newChat(space) {
  if (talking) stopTalk();
  chatId = null; chatSpace = space || null; chatTitle = 'New chat'; store.del('chat_id'); pendingConf = null;
  classicOnce = !!space;
  resetLog(); setChatHeader(); go('#/chat'); setTimeout(() => t.focus(), 50);
}
function replyExtras(r) {
  if (r.ask) askCard(r.ask);
  if (r.suggestions && r.suggestions.length) suggestChips(r.suggestions);
  if (r.reply && !(r.pending || []).length) reactBar();
}
function dropExtras() { document.querySelectorAll('.chips,.react,.askcard').forEach((x) => x.remove()); }
function suggestChips(list) {
  const d = h('div', { class: 'chips' }, ...list.map((s) => h('button', { type: 'button', class: 'chip', onclick: () => { dropExtras(); send(s); } }, s)));
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
function askCard(a) {
  const d = h('div', { class: 'askcard' }, h('b', {}, a.question), h('div', { class: 'b' }, ...(a.options || []).map((o) => h('button', { type: 'button', onclick: () => { d.remove(); send(o); } }, o))));
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
function reactBar() {
  if (!chatId) return;
  const cid = chatId;
  const mk = (v, label, tip) => h('button', { type: 'button', class: 'rx', title: tip, 'aria-label': tip, onclick: async (e) => {
    try { await api('/api/chats/' + cid + '/react', { value: v }); d.querySelectorAll('.rx').forEach((x) => x.classList.remove('on')); e.currentTarget.classList.add('on'); } catch (x) {}
  } }, label);
  const d = h('div', { class: 'react' }, mk(1, '\u{1F44D}', 'Good reply'), mk(-1, '\u{1F44E}', 'Bad reply'));
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
function confirmBox(p) {
  const d = h('div', { class: 'conf' }, h('b', {}, 'Please confirm'), h('div', {}, p.summary), p.risk_text ? h('div', { class: 'muted' }, p.risk_text) : null, h('div', { class: 'b' },
    h('button', { class: 'yes', type: 'button', onclick: async () => { try { const r = await api('/api/confirm', { token: p.token }); d.remove(); add(r.status === 'done' ? '✅ Done.' : '⚠️ ' + (r.message || 'Failed'), 'bot'); } catch (e) { add('⚠️ Could not reach the server. The card is still here: try again.', 'bot'); } } }, 'Yes, do it'),
    h('button', { class: 'no', type: 'button', onclick: async () => { try { await api('/api/cancel', { token: p.token }); d.remove(); add('Cancelled. Nothing changed.', 'bot'); } catch (e) { add('⚠️ Could not reach the server. The card is still here: try again.', 'bot'); } } }, 'No')));
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
async function send(text) {
  if (text.trim().startsWith('/')) return handleSlash(text.trim());
  dropExtras(); add(text, 'me'); const w = add('Thinking…', 'bot dim');
  try { const r = await api('/api/chat', { text, mode: 'auto', voice: false }); w.remove(); add(r.reply || '(no answer)', 'bot'); (r.pending || []).forEach(confirmBox); replyExtras(r); }
  catch (e) { w.remove(); add(DOWN, 'bot'); }
}
async function whatnow() { const w = add('Checking…', 'bot dim'); try { const r = await J('/api/whatnow'); w.remove(); add(r.text, 'bot'); } catch (e) { w.remove(); add(DOWN, 'bot'); } }
async function runCmd(name) {
  add('/' + name, 'me'); const w = add(name === 'start' ? 'Looking at your day…' : 'Working on it (can take up to a minute)…', 'bot dim');
  try { const r = await api('/api/command', { name }); w.remove(); add(r.reply || '(no answer)', 'bot'); (r.pending || []).forEach(confirmBox); return r.reply; }
  catch (e) { w.remove(); add(DOWN, 'bot'); }
}
function addTask() { const x = prompt('New task?'); if (x) send('Add a task: ' + x); }
$('#bar').addEventListener('submit', (e) => { e.preventDefault(); const v = t.value.trim(); $('#slash').style.display = 'none'; if (v) { t.value = ''; send(v); } });

const CMDS = [['/new', 'Start a fresh chat', 0], ['/home', 'Open the home dashboard', 0], ['/brief', "Open today's daily brief", 0], ['/search', 'Search past chats: /search words', 1], ['/start', 'Morning standup', 0],
  ['/sync', 'Sort scratch pad + meetings', 0], ['/wrap-up', 'End-of-day routine', 0], ['/tasks', "Today's + overdue tasks", 0], ['/memory', 'What I remember about you', 0],
  ['/remember', 'Save a fact: /remember text', 1], ['/skills', 'My playbooks', 0], ['/notes', 'Open notes', 0], ['/settings', 'Open settings', 0], ['/help', 'All commands', 0]];
async function handleSlash(text) {
  const name = text.split(/\s+/)[0].toLowerCase();
  const NAVMAP = { '/home': '#/home', '/brief': '#/brief', '/notes': '#/notes', '/settings': '#/settings' };
  if (name === '/new') return newChat();
  if (NAVMAP[name]) return go(NAVMAP[name]);
  if (['/start', '/sync', '/wrap-up', '/wrapup'].includes(name)) return runCmd(name.slice(1).replace('wrapup', 'wrap-up'));
  add(text, 'me'); const w = add('…', 'bot dim');
  try { const r = await api('/api/slash', { text }); w.remove(); add(r.reply, 'bot'); } catch (e) { w.remove(); add(DOWN, 'bot'); }
}
t.addEventListener('input', () => {
  const v = t.value.toLowerCase(), m = $('#slash');
  if (v.startsWith('/') && !v.includes(' ')) {
    const list = CMDS.filter((c) => c[0].startsWith(v)); m.replaceChildren();
    list.forEach((c) => m.append(h('button', { type: 'button', onclick: () => { m.style.display = 'none'; if (c[2]) { t.value = c[0] + ' '; t.focus(); } else { t.value = ''; handleSlash(c[0]); } } }, h('b', {}, c[0]), h('span', {}, c[1]))));
    m.style.display = list.length ? 'block' : 'none';
  } else m.style.display = 'none';
});

const mic = $('#mic'); let rec;
async function startRec() {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true }); const chunks = [];
    rec = new MediaRecorder(s); rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = async () => {
      s.getTracks().forEach((x) => x.stop()); mic.classList.remove('rec');
      const fd = new FormData(); fd.append('audio', new Blob(chunks, { type: rec.mimeType }), 'a.webm'); const w = add('Listening…', 'bot dim');
      try { const r = await (await fetch('/api/transcribe', { method: 'POST', body: fd })).json(); w.remove(); if (r.text) send(r.text); else add('Didn\'t catch that. Try again.', 'bot'); } catch (e) { w.remove(); add('Voice failed.', 'bot'); }
    };
    rec.start(); mic.classList.add('rec');
  } catch (e) { add('Microphone blocked. Allow it in the browser site settings (needs the https link).', 'bot'); }
}
mic.addEventListener('click', () => { rec && rec.state === 'recording' ? rec.stop() : startRec(); });


const octo = (() => {
  const PAL = { listening: ['#00e5ff', '#9d4dff'], thinking: ['#9d4dff', '#00e5ff'], speaking: ['#bf4dff', '#1ae6cc'] };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cv = document.createElement('canvas'); cv.id = 'octo'; cv.setAttribute('aria-hidden', 'true');
  cv.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:60;pointer-events:none;opacity:0;transition:opacity .25s';
  document.body.appendChild(cv);
  const img = new Image(); img.src = '/overlay/octopus-sprite.svg';
  const o = { state: 'idle', level: 0, timer: null, ghosts: {}, gw: 0 };
  let seed = 1; const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  function ghost(state, i, w, h) {
    const key = state + i + w; if (o.ghosts[key]) return o.ghosts[key];
    const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d'); x.imageSmoothingEnabled = false;
    x.drawImage(img, 0, 0, w, h); x.globalCompositeOperation = 'source-in'; x.fillStyle = PAL[state][i]; x.fillRect(0, 0, w, h); return (o.ghosts[key] = c);
  }
  function mainLayer(w, h) { const key = 'main' + w; if (o.ghosts[key]) return o.ghosts[key]; const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d'); x.imageSmoothingEnabled = false; x.drawImage(img, 0, 0, w, h); return (o.ghosts[key] = c); }
  function draw() {
    const t = performance.now() / 1000, dpr = Math.min(2, devicePixelRatio || 1), W = Math.round(innerWidth * dpr), H = Math.round(innerHeight * dpr);
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const x = cv.getContext('2d'); x.clearRect(0, 0, W, H); x.imageSmoothingEnabled = false;
    const st = PAL[o.state] ? o.state : 'listening'; if (!img.complete || !img.naturalWidth) return;
    seed = Math.floor(t * 15) + 7;
    const ow = Math.round(Math.min(W * 0.86, H * 0.5 * 612 / 492)), oh = Math.round(ow * 492 / 612), ox = Math.round((W - ow) / 2), oy = Math.round(H / 2 - oh / 2 - H * 0.04 + 6 * dpr * Math.sin(t * 1.6));
    const burst = ((t * 1.1) % 1) < 0.2 ? 1 : 0, g = reduced ? 0 : Math.min(1, 0.12 + 0.5 * o.level + 0.7 * burst), split = reduced ? 3 * dpr : (3 + 22 * g) * dpr;
    const wash = x.createRadialGradient(W / 2, H / 2, oh * 0.1, W / 2, H / 2, Math.max(W, H) * 0.6); wash.addColorStop(0, PAL[st][0] + '29'); wash.addColorStop(1, PAL[st][0] + '00'); x.fillStyle = wash; x.fillRect(0, 0, W, H);
    if (!reduced) { x.fillStyle = PAL[st][0] + '09'; for (let yy = 0; yy < H; yy += 6 * dpr) x.fillRect(0, yy, W, dpr);
      for (let i = 0; i < 2 + 6 * g; i++) { x.fillStyle = PAL[st][rnd() < 0.5 ? 0 : 1] + '33'; x.fillRect(rnd() * W * 0.8, rnd() * H, W * (0.15 + rnd() * 0.5), (2 + Math.floor(rnd() * 8)) * dpr); } }
    const layers = [[ghost(st, 0, ow, oh), -split, 0.55], [ghost(st, 1, ow, oh), split, 0.55], [mainLayer(ow, oh), 0, 1]];
    for (let sy = 0; sy < oh;) {
      const sh = g > 0.2 ? [6, 10, 14, 22, 40][Math.floor(rnd() * 5)] * dpr : 60 * dpr, hh = Math.min(sh, oh - sy), dx = rnd() < 0.15 + 0.5 * g ? (rnd() * 2 - 1) * 46 * g * dpr : 0;
      for (const [src, offx, a] of layers) { x.globalAlpha = a; x.drawImage(src, 0, sy, ow, hh, ox + dx + offx, oy + sy, ow, hh); }
      sy += hh;
    }
    x.globalAlpha = 1;
    if (!reduced) for (let i = 0; i < 3 + 12 * g; i++) { const s2 = [6, 9, 12, 18][Math.floor(rnd() * 4)] * dpr; x.fillStyle = PAL[st][rnd() < 0.5 ? 0 : 1] + '99'; x.fillRect(ox + (rnd() * 1.2 - 0.1) * ow, oy + (rnd() * 1.2 - 0.1) * oh, s2, s2); }
    x.font = 'bold ' + 26 * dpr + 'px monospace'; x.textAlign = 'center'; const label = st + '.'.repeat(st === 'speaking' ? 0 : Math.floor(t * 2.5) % 4);
    x.fillStyle = '#000a'; x.fillRect(W / 2 - 150 * dpr, oy + oh + 14 * dpr, 300 * dpr, 44 * dpr); x.fillStyle = PAL[st][0]; x.fillText(label, W / 2, oy + oh + 46 * dpr);
  }
  o.set = (state) => {
    o.state = state; clearInterval(o.timer);
    if (state === 'idle') { cv.style.opacity = '0'; return; }
    cv.style.opacity = '1'; draw(); o.timer = setInterval(() => { if (o.state === 'speaking') o.level = 0.45 + 0.3 * Math.sin(performance.now() / 120); else if (o.state === 'thinking') o.level = 0; draw(); }, 66);
  };
  return o;
})();

const talkBtn = $('#talk');
let talking = false, tstream, tctx, analyser, player = new Audio(), curRec = null, curIv = null, pendingConf = null, wake = null, skipSpeech = false;
const setTalk = (txt, cls) => { talkBtn.textContent = txt; talkBtn.className = cls || ''; octo.set(!talking && !cls ? 'idle' : cls === 'on' ? 'listening' : /Thinking/.test(txt) ? 'thinking' : cls === 'speak' ? 'speaking' : 'idle'); };
function rms() { const b = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(b); let s = 0; for (const x of b) s += x * x; return Math.sqrt(s / b.length); }
async function toggleTalk() {
  if (talking && player.currentTime > 0 && !player.paused) { skipSpeech = true; player.pause(); player.dispatchEvent(new Event('ended')); return; }
  if (talking) { stopTalk(); return; }
  try { tstream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch (e) { add('Microphone blocked. Allow it in the browser site settings.', 'bot'); return; }
  tctx = new AudioContext(); analyser = tctx.createAnalyser(); analyser.fftSize = 1024; tctx.createMediaStreamSource(tstream).connect(analyser);
  player.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA='; player.play().catch(() => {});
  try { wake = await navigator.wakeLock.request('screen'); } catch (e) { }
  window.AndroidBridge && AndroidBridge.keepAwake(true);
  talking = true; add('Conversation on. I answer out loud and keep listening. Say "Start new chat" for a fresh chat, or "stop" to end.', 'bot dim'); listenTurn();
}
function stopTalk() {
  window.AndroidBridge && AndroidBridge.keepAwake(false);
  talking = false; clearInterval(curIv); if (curRec && curRec.state === 'recording') { curRec.onstop = null; curRec.stop(); }
  player.pause(); tstream && tstream.getTracks().forEach((x) => x.stop()); tctx && tctx.close(); wake && wake.release().catch(() => {}); setTalk('🗣 Talk', '');
}
async function listenTurn() {
  if (!talking) return; setTalk('👂 Listening… (tap to stop)', 'on');
  const r = new MediaRecorder(tstream); curRec = r; const chunks = []; r.ondataavailable = (e) => chunks.push(e.data);
  let spoke = false, last = 0; const started = Date.now(), amb = []; r.start();
  const iv = curIv = setInterval(() => {
    const v = rms(), n = Date.now(); octo.level = Math.min(1, v * 9);
    if (n - started < 500) { amb.push(v); return; }
    const thr = Math.max(0.025, (amb.reduce((a, b) => a + b, 0) / Math.max(1, amb.length)) * 3);
    if (v > thr) { spoke = true; last = n; }
    if (spoke && n - last > 1400) { clearInterval(iv); r.stop(); }
    else if (!spoke && n - started > 90000) { clearInterval(iv); add('Still here if you need me. Tap Talk again.', 'bot dim'); stopTalk(); }
  }, 100);
  r.onstop = async () => {
    clearInterval(iv); if (!talking || !spoke) return; setTalk('💭 Thinking…', 'speak');
    try { const fd = new FormData(); fd.append('audio', new Blob(chunks, { type: r.mimeType }), 'a.webm');
      const text = (await (await fetch('/api/transcribe', { method: 'POST', body: fd })).json()).text;
      if (!text) return listenTurn(); await handleSpeech(text); }
    catch (e) { add('Lost connection. Is the PC on?', 'bot'); stopTalk(); }
  };
}
async function handleSpeech(text) {
  add(text, 'me'); const low = text.toLowerCase().replace(/[^a-z ]/g, '').trim();
  if (/^(stop|goodbye|bye|that is all|thats all|end conversation)( now| please)?$/.test(low)) { await say("Okay. I'm here when you need me."); return stopTalk(); }
  if (/^(start a new chat|start new chat|start a new conversation|start new conversation|new chat|new conversation|start over)( please)?$/.test(low)) { newChat(); talking = true; await say('Okay, fresh start.'); return listenTurn(); }
  const vc = low.match(/^(start my day|start|good morning|sync|sync my notes|wrap up|wrap up my day|end my day)$/);
  if (vc) { const n = /sync/.test(vc[1]) ? 'sync' : /wrap|end/.test(vc[1]) ? 'wrap-up' : 'start';
    const r = await api('/api/command', { name: n }); add(r.reply, 'bot'); (r.pending || []).forEach(confirmBox); await say(r.reply); return listenTurn(); }
  if (pendingConf) {
    const yes = /^(yes|yeah|yep|do it|go ahead|confirm|sure|okay|ok)\b/.test(low), no = /^(no|nope|cancel|dont|do not|stop that)\b/.test(low);
    if (yes || no) { const p = pendingConf; pendingConf = null; document.querySelectorAll('.conf').forEach((x) => x.remove());
      if (yes) { const r = await api('/api/confirm', { token: p.token }); const m = r.status === 'done' ? 'Done.' : (r.message || 'That did not work.'); add(m, 'bot'); await say(m); }
      else { await api('/api/cancel', { token: p.token }); add('Cancelled. Nothing changed.', 'bot'); await say('Okay, cancelled. Nothing changed.'); }
      return listenTurn(); }
  }
  const r = await api('/api/chat', { text, mode: 'auto', voice: true }); add(r.reply || '(no answer)', 'bot');
  if ((r.pending || []).length) { pendingConf = r.pending[0]; r.pending.forEach(confirmBox); }
  await say(r.reply); if (talking) listenTurn();
}
function clean(x) { return x.replace(/[*_#`>]/g, '').replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}✨]/gu, '').replace(/\s+/g, ' ').trim(); }
async function say(text) {
  if (!text || !talking) return;
  const parts = clean(text).match(/[^.!?]+[.!?]*/g) || [clean(text)];
  const fetches = parts.map((p) => fetch('/api/speak', jopt('POST', { text: p })).then((r) => r.blob()));
  for (const f of fetches) { if (skipSpeech || !talking) break; const url = URL.createObjectURL(await f);
    await new Promise((res) => { player.onended = res; player.src = url; player.play().catch(res); }); URL.revokeObjectURL(url); }
}
talkBtn.addEventListener('click', toggleTalk);

async function refreshBadges() {
  try {
    const b = await J('/api/badges'); const changed = b.overdue !== overdueCount || b.inbox !== unreadCount;
    overdueCount = b.overdue; unreadCount = b.inbox;
    if (changed) { renderNav(navActive()); if (location.hash.startsWith('#/inbox') && !dirty) keepScroll(() => VIEWS.inbox()); }
  } catch (e) { $('#dot').className = 'off'; }
}
setInterval(() => { if (!document.hidden) refreshBadges(); }, 45000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshBadges(); });
(async function init() {
  $('#menu').onclick = () => document.body.classList.toggle('side-open');
  $('#scrim').onclick = () => document.body.classList.remove('side-open');
  $('#newchat').onclick = () => newChat();
  renderNav('home');
  await refreshSide();
  if (!location.hash || location.hash === '#') { history.replaceState(null, '', '#/home'); }
  prevHash = location.hash; route();
})();


async function shutdownAll() {
  if (!confirm('Shut down everything?\n\nThis stops Agent Zero, Rosie\'s memory graph, push-to-talk, the talking overlay and the AI models, then Rosie herself. Nothing is lost.\n\nTo start it all again, open Rosie from the desktop.')) return;
  try {
    const r = await post('/api/power/shutdown', {});
    if (!r.started) throw new Error(r.error || r.note || 'The shutdown did not start.');
  } catch (e) { toast(e.message, 1); return; }
  document.body.replaceChildren(h('div', { class: 'goodbye' }, h('h2', {}, 'Rosie is shutting down'),
    h('p', { class: 'muted' }, 'Everything stops in a few seconds. To start again, open Rosie from the desktop (or run ~/rosie-ops/power.sh start).')));
}
$('#foot').append(h('button', { class: 'btn sm bad shut', type: 'button', title: 'Stop Rosie, Agent Zero and the AI models', onclick: shutdownAll }, '⏻ Shut down all'));

let a0Timer = null;
let serversPollT = null;
VIEWS.servers = async () => {
  setHeader('Servers', []);
  const here = () => /^#\/servers\/?$/.test(location.hash);
  const copy = (t) => navigator.clipboard.writeText(t).then(() => toast('Copied ' + t), () => toast(t));
  const dot = (up) => h('span', { title: up ? 'Answering' : 'Not answering from this PC', style: `display:inline-block;width:9px;height:9px;border-radius:50%;flex:none;background:${up ? 'var(--ok,#3fdc8a)' : 'var(--bad)'};box-shadow:0 0 8px ${up ? '#3fdc8a90' : '#ff4d6d80'}` });
  const draw = async (first) => {
    if (!here()) return;
    let d;
    try { d = await J('/api/servers'); } catch (e) { if (first) show(h('div', { class: 'muted' }, e.message)); return; }
    if (!here()) return;
    const card = (m) => h('div', { class: 'card' },
      h('h3', {}, dot(m.up), m.name, h('a', { href: '#', onclick: (e) => { e.preventDefault(); copy(m.ip); } }, m.ip || 'no address')),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin:-4px 0 8px' }, m.kind + (m.running ? '' : ' · stopped') + (m.note ? ' · ' + m.note : '')),
      m.services.length ? m.services.map((s) => h('div', { class: 'form', style: 'gap:8px;margin:4px 0;flex-wrap:nowrap' }, dot(s.up),
        h('span', { style: 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap', title: s.program || '' }, s.label),
        h('a', { href: '#', class: 'muted', style: 'font-size:calc(12px*var(--ts,1));font-family:var(--mono,monospace)', title: 'Copy', onclick: (e) => { e.preventDefault(); copy(`${m.ip}:${s.port}`); } }, `${m.ip}:${s.port}`),
        s.url ? h('a', { class: 'btn sm pur', href: s.url, target: '_blank', rel: 'noopener' }, 'Open') : s.hint ? btn('Copy', 'sm', () => copy(s.hint)) : null,
        m.manual ? null : h('button', { class: 'btn sm dim', type: 'button', title: 'Rename', onclick: async () => {
          const v = prompt('Name for ' + m.ip + ':' + s.port + ' (empty = the found name)', s.label);
          if (v === null) return;
          try { await post('/api/servers/label', { ip: m.ip, port: s.port, label: v }); draw(); } catch (e) { toast(e.message, 1); } } }, '✎'))) : h('div', { class: 'muted' }, 'Nothing to open here.'),
      m.manual ? h('div', { style: 'margin-top:6px' }, btn('Remove', 'sm dim', async () => { if (!confirm('Remove ' + m.name + '?')) return; try { await del('/api/servers/' + m.id); draw(); } catch (e) { toast(e.message, 1); } })) : null);
    const nm = h('input', { placeholder: 'Name, like Router', 'aria-label': 'Name' });
    const ad = h('input', { placeholder: '192.168.1.1', 'aria-label': 'Address' });
    const pt = h('input', { type: 'number', min: 1, max: 65535, placeholder: 'Port', 'aria-label': 'Port', style: 'max-width:110px' });
    const nt = h('input', { placeholder: 'Note (optional)', 'aria-label': 'Note' });
    const add = async () => { try { await post('/api/servers', { name: nm.value, address: ad.value, port: pt.value ? +pt.value : null, note: nt.value }); toast('Added'); draw(); } catch (e) { toast(e.message, 1); } };
    const up = d.machines.filter((m) => m.up).length;
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Servers'), h('span', { class: 'muted' }, `${up} of ${d.machines.length} answering`), h('span', { class: 'sp' }),
        d.server ? btn(d.looking ? 'Looking…' : 'Look again', 'sm', async () => { try { await post('/api/servers/refresh'); toast('Looking at your server…'); setTimeout(draw, 8000); } catch (e) { toast(e.message, 1); } }) : null),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-bottom:10px' }, (d.server ? 'Found on your home server' + (d.found_at ? ' at ' + d.found_at.slice(0, 16).replace('T', ' ') : '') + '. ' : 'Set up your home server in Settings > Security and Rosie finds its machines and apps by herself. ') +
        'Green answers from this PC, red doesn\'t (stopped, or a firewall). Click an address to copy it. Ask Rosie "what\'s the IP of …" in chat too.' + (d.last_error ? ' Last problem: ' + d.last_error : '')),
      h('div', { class: 'grid' }, d.machines.map(card)),
      h('div', { class: 'card', style: 'margin-top:12px' }, h('h3', {}, 'Add one by hand'), h('div', { class: 'form' }, nm, ad, pt, nt, btn('Add', 'sm pur', add)),
        h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:4px' }, 'For things Rosie can\'t find on her own: the router, a NAS, a printer, another PC.')));
    clearTimeout(serversPollT);
    serversPollT = setTimeout(() => draw(), d.looking ? 5000 : 30000);
  };
  show(h('div', { class: 'muted' }, 'Checking your servers…'));
  draw(true);
};
VIEWS.chats = async () => {
  setHeader('Chats', [btn('＋ New chat', 'sm pur', () => newChat())]);
  let q = '', space = '', timer = null;
  const list = h('div', {}), count = h('span', { class: 'muted' });
  const search = h('input', { type: 'search', placeholder: 'Search every chat…', 'aria-label': 'Search chats', style: 'flex:1;min-width:160px' });
  const pick = h('select', { 'aria-label': 'Filter by space' });
  const draw = async () => {
    const r = await J('/api/chats/all?q=' + encodeURIComponent(q) + '&space=' + encodeURIComponent(space));
    pick.replaceChildren(h('option', { value: '' }, 'All spaces'), h('option', { value: '__none__' }, 'Inbox (no space)'), ...(r.spaces || []).map((s) => h('option', { value: s }, s)));
    pick.value = space;
    count.textContent = `${r.chats.length} chat${r.chats.length === 1 ? '' : 's'} · saved in ${r.db}`;
    let day = '';
    list.replaceChildren(...(r.chats.length ? r.chats.flatMap((c) => {
      const d = c.updated.slice(0, 10), out = [];
      if (d !== day) { day = d; out.push(h('div', { class: 'dayhead' }, d)); }
      out.push(h('div', { class: 'card', style: 'display:flex;gap:10px;align-items:center;margin-top:6px' },
        h('div', { style: 'flex:1;min-width:0;cursor:pointer', role: 'link', tabindex: '0', onclick: () => go('#/chat/' + c.id), onkeydown: (e) => { if (e.key === 'Enter') go('#/chat/' + c.id); } },
          h('div', {}, c.title || 'New chat', c.space ? h('span', { class: 'muted' }, '  [' + c.space + ']') : ''),
          h('div', { class: 'muted', style: 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis' }, `${c.count} message${c.count === 1 ? '' : 's'} · ${c.updated.slice(11, 16)}` + (c.last ? ' · ' + c.last : ''))),
        btn('🗑', 'sm bad', async () => { if (!confirm('Delete this chat for good?')) return; await del('/api/chats/' + c.id); refreshSide(); keepScroll(draw); })));
      return out;
    }) : [h('div', { class: 'muted', style: 'margin-top:12px' }, q || space ? 'No chat matches.' : 'No chats yet.')]));
  };
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { q = search.value; draw(); }, 250); });
  pick.addEventListener('change', () => { space = pick.value; draw(); });
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Chats'), count), h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' }, search, pick), list);
  await draw();
};
let tokensPollT = null;
const tkFmt = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? Math.round(n / 1e3) + 'k' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(Math.round(n));
VIEWS.tokens = async () => {
  setHeader('Tokens', []);
  const here = () => /^#\/tokens\/?$/.test(location.hash);
  const FAM = [['opus', 'Opus', 'var(--pur)'], ['sonnet', 'Sonnet', 'var(--acc)'], ['haiku', 'Haiku', 'var(--ok)'], ['other', 'Other', 'var(--mute)']];
  const draw = async () => {
    clearTimeout(tokensPollT);
    if (!here()) return;
    if (document.hidden) { tokensPollT = setTimeout(draw, 60000); return; }
    let d;
    try { d = await J('/api/tokens'); } catch (e) { if (here()) show(h('div', { class: 'card' }, 'Couldn\'t read the token report: ' + e.message)); return; }
    if (!here()) return;
    const big = (label, val, sub, bad) => h('div', { class: 'tk-big' + (bad ? ' bad' : '') }, h('div', { class: 'tk-n' }, val), h('div', { class: 'tk-l' }, label), sub ? h('div', { class: 'tk-s muted' }, sub) : null);
    const goal = d.goals.token_goal_main_share;
    const row = h('div', { class: 'tk-row' },
      big('Output today', tkFmt(d.today.out), d.today.n + ' responses'),
      big('Output, 7 days', tkFmt(d.week.out), 'Cache read ' + d.week.cache_ratio + '% of input'),
      big('Main model share', d.week.main_share + '%', 'Goal ' + goal + '% or less', d.week.out >= 50000 && d.week.main_share > goal),
      big('Kept out by local model', tkFmt(d.ledger.week), '7 days; ' + tkFmt(d.ledger.all) + ' in all'));
    const flags = d.flags.length
      ? h('div', { class: 'card tk-flags' }, h('h3', {}, 'What to do'), h('ul', {}, d.flags.map((f) => h('li', {}, f.text))))
      : h('div', { class: 'card tk-ok' }, h('span', { class: 'pill' }, 'On track'), ' Token use is inside your goals.');
    const max = Math.max(1, ...d.series.map((r) => r.out));
    const bars = h('div', { class: 'tk-chart', role: 'img', 'aria-label': 'Output tokens per day for the last 14 days, by model' },
      d.series.map((r) => h('div', { class: 'tk-col', title: r.day + ': ' + tkFmt(r.out) + ' output (' + FAM.filter(([k]) => r.fam[k]).map(([k, l]) => l + ' ' + tkFmt(r.fam[k])).join(', ') + ')' },
        h('div', { class: 'tk-val' }, r.out ? tkFmt(r.out) : ''),
        h('div', { class: 'tk-well' }, h('div', { class: 'tk-stack', style: `height:${Math.round(100 * r.out / max)}%` }, FAM.filter(([k]) => r.fam[k]).map(([k, , c]) => h('div', { style: `flex:${r.fam[k]};background:${c}` })))),
        h('div', { class: 'tk-day' }, r.day.slice(8)))));
    const legend = h('div', { class: 'tk-legend' }, FAM.map(([k, l, c]) => h('span', {}, h('i', { style: 'background:' + c }), l + ' ' + tkFmt(d.by_model[k] || 0))));
    const top = d.top_sessions.length
      ? h('table', { class: 'rs-tbl tk-tbl' }, h('thead', {}, h('tr', {}, ['Project', 'Session', 'Output', 'Sub-agent', 'Models'].map((t, i) => h('th', { class: i >= 2 && i < 4 ? 'num' : '' }, t)))),
          h('tbody', {}, d.top_sessions.map((s) => h('tr', {}, h('td', {}, s.project.replace(/^-home-[^-]+-?/, '') || 'home'), h('td', {}, h('code', {}, s.session)), h('td', { class: 'num' }, tkFmt(s.out)), h('td', { class: 'num' }, tkFmt(s.sub_out)),
            h('td', {}, Object.entries(s.models).map(([k, p]) => k + ' ' + p + '%').join(', '))))))
      : h('div', { class: 'muted' }, 'No sessions in the last 7 days.');
    const when = d.updated ? new Date(d.updated * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'never';
    show(row, flags,
      h('div', { class: 'card', style: 'margin-top:12px' }, h('h3', {}, 'Output tokens per day, last 14 days'), bars, legend),
      h('div', { class: 'card', style: 'margin-top:12px' }, h('h3', {}, 'Top sessions this week'), h('div', { class: 'tk-scroll' }, top)),
      h('div', { class: 'muted', style: 'font-size:var(--t0,13px);margin-top:10px' }, 'Report updated ' + when + (d.pending_bytes ? '; still reading older logs.' : '.') + ' Run python app/tokens.py to refresh now. Numbers only: Rosie never stores what was said.'));
    tokensPollT = setTimeout(draw, 60000);
  };
  show(h('div', { class: 'muted' }, 'Reading the token report…'));
  draw();
};

VIEWS.deck = async () => {
  setHeader('Agent Deck', []);
  show(h('iframe', { class: 'deckframe', src: '/deck/index.html', title: 'Agent Deck: what every agent is doing, with controls' }));
};

const a0Here = () => location.hash.replace(/^#\/?/, '').startsWith('agentzero');
function a0Ago(sec) { const m = Math.floor(sec / 60); return m ? `${m} min ${sec % 60} s` : `${sec} s`; }
VIEWS.agentzero = async () => {
  clearInterval(a0Timer);
  setHeader('Agent Zero', []);
  const act = async (what, label, sure) => {
    if (sure && !confirm(sure)) return;
    toast(label + '…');
    try { const r = await post('/api/a0/' + what, {}); if (!r.ok) throw new Error(r.error || 'It did not work.'); toast(label + ': done'); } catch (e) { toast(e.message, 1); }
    draw();
  };
  const setChatUi = async (v) => { try { await post('/api/settings', { a0_chat_ui: v }); a0Chat = null; toast(v ? 'Chat shows Agent Zero\'s page' : 'Chat is Rosie\'s own again'); } catch (e) { toast(e.message, 1); } draw(); };
  let busy = false;
  const typingNote = () => {
    const el = document.activeElement;
    return !!(el && el.tagName === 'INPUT' && el.closest && el.closest('.a0-board-form') && (el.value || '').trim());
  };
  const draw = async () => {
    if (!a0Here()) { clearInterval(a0Timer); return; }
    if (busy || document.hidden) return;
    busy = true;
    let d;
    try { d = await J('/api/a0/status'); } catch (e) { busy = false; if (a0Here() && !typingNote()) show(h('div', { class: 'err' }, 'Could not read Agent Zero\'s status: ' + e.message)); return; }
    busy = false;
    if (!a0Here()) return;
    const c = d.container || {}, up = c.state === 'running';
    const row = (k, v) => [h('dt', {}, k), h('dd', {}, v)];
    const status = h('div', { class: 'card' }, h('h3', {}, 'Status'),
      h('dl', { class: 'kv' },
        row('Container', h('span', { class: 'chip ' + (up ? 'ok' : 'bad') }, c.state || 'unknown')),
        row('Web page', up ? (d.answers ? 'answers' : h('span', { style: 'color:var(--warn)' }, 'starting or not answering yet')) : '–'),
        row('Running since', up ? (c.started || '?') + (c.restarts ? ` · ${c.restarts} restarts` : '') : '–'),
        row('Brain', d.brain === 'agent_zero' ? 'Agent Zero answers Rosie\'s chat' : h('span', {}, 'Rosie answers herself (', h('a', { href: '#/settings' }, 'Settings'), ')')),
        row('Model', `${d.model} · ${(d.ctx.num_ctx || 0).toLocaleString()} tokens` + (d.models.some((m) => m.name === d.model && m.on_gpu) ? ' · loaded on the graphics card' : ' · not loaded right now')),
        row('Saved chats', d.chats == null ? '–' : String(d.chats))),
      c.error ? h('div', { class: 'err', style: 'margin-top:8px' }, c.error) : null,
      h('div', { class: 'form', style: 'margin-top:10px' },
        btn('Talk to it in Rosie\'s chat', 'sm pur', () => go('#/chat')),
        up ? btn('Restart', 'sm', () => act('restart', 'Restarting Agent Zero', 'Restart Agent Zero? Anything it is working on stops.')) : null,
        up ? btn('Stop', 'sm bad', () => act('stop', 'Stopping Agent Zero', 'Stop Agent Zero? Rosie answers chat herself until it runs again.')) : btn('Start', 'sm ok', () => act('start', 'Starting Agent Zero'))),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Rosie\'s chat is the one place to type: Agent Zero answers there, with all of Rosie\'s tools. A turn stops by itself after 50 steps.'));
    const t = d.turn;
    const px = await J('/api/a0/proxy').catch(() => null);
    const chatCard = !px ? null : h('div', { class: 'card' }, h('h3', {}, 'Agent Zero as the Chat'),
      h('dl', { class: 'kv' },
        row('Its page', px.on ? h('span', { class: 'chip ok' }, 'served on port ' + px.port) : h('span', { class: 'chip bad' }, 'off')),
        row('Use as Chat', h('div', { class: 'seg' }, h('button', { class: px.chat_ui ? 'on' : '', onclick: () => setChatUi(true) }, 'On'), h('button', { class: px.chat_ui ? '' : 'on', onclick: () => setChatUi(false) }, 'Off'))),
        row('Phone', px.phone_served === true ? h('span', { class: 'chip ok' }, 'ready') : h('span', { class: 'chip bad' }, px.phone_served === false ? 'one step left' : 'needs Tailscale'))),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin:6px 0' }, 'Chat shows Agent Zero\'s own page (its tool steps, files, projects, scheduler, memory and settings) while the brain is Agent Zero. Rosie\'s login guards it. Classic chat stays one click away. To open it on your phone, run this once on the PC:'),
      h('pre', { class: 'a0-log' }, px.phone_command));
    const now = h('div', { class: 'card' }, h('h3', {}, 'Working now'),
      t ? h('div', {},
        h('div', {}, h('b', {}, '“' + t.text + '”')),
        h('div', { class: 'muted', style: 'margin:4px 0 8px' }, `${a0Ago(t.seconds)} so far · ${t.calls.length} Rosie tool${t.calls.length === 1 ? '' : 's'} used` + (t.waiting ? ` · ${t.waiting} waiting for your Yes` : '')),
        t.calls.length ? h('div', { class: 'list' }, t.calls.slice(-8).reverse().map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' }, x.tool)))) : null,
        h('div', { class: 'form', style: 'margin-top:10px' }, btn('Stop this task', 'sm bad', () => act('restart', 'Stopping the task', 'Stop what Agent Zero is doing? This restarts it (about 20 seconds); the chat keeps what was said so far.'))))
        : h('div', { class: 'muted' }, up ? 'Nothing right now.' : 'Agent Zero is not running.'));
    const calls = h('div', { class: 'card' }, h('h3', {}, 'Rosie\'s tools it used'),
      d.calls.length ? h('div', { class: 'list' }, d.calls.map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' }, h('b', {}, x.tool + '  '), h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, x.at + (x.args ? ' · ' + x.args : ''))))))
        : h('div', { class: 'muted' }, 'None yet.'));
    const log = h('div', { class: 'card wide' }, h('h3', {}, 'Recent activity'),
      d.activity.length ? h('pre', { class: 'a0-log' }, d.activity.join('\n')) : h('div', { class: 'muted' }, up ? 'Nothing logged yet.' : '–'));
    const b = d.board || {}, tm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const noteTo = h('select', { 'aria-label': 'Note to', onchange: (e) => { a0Draft.to = e.target.value; } },
      ['all', 'claude', 'rosie'].map((v) => h('option', { value: v }, v === 'all' ? 'Everyone' : v === 'claude' ? 'Claude sessions' : 'Rosie / Agent Zero')));
    noteTo.value = a0Draft.to;  // board #820 safety-draft-survives-redraw
    const noteText = h('input', { type: 'text', placeholder: 'Leave a note for the agents', 'aria-label': 'Note text', maxlength: 600,
      oninput: (e) => { a0Draft.text = e.target.value; }, onkeydown: (e) => { if (e.key === 'Enter') sendNote(); } });
    noteText.value = a0Draft.text;
    const sendNote = async () => {
      if (!noteText.value.trim()) return;
      try { const r = await post('/api/board/note', { text: noteText.value, to: noteTo.value }); if (r.error) throw new Error(r.error); noteText.value = ''; a0Draft.text = ''; noteText.blur(); toast('Note posted'); } catch (e) { toast(e.message, 1); }
      draw();
    };
    const endClaim = async (thing) => {
      if (!confirm(`End the claim on ${thing}? The agent holding it may still be working on it.`)) return;
      try { await post('/api/board/release', { thing }); toast('Claim ended'); } catch (e) { toast(e.message, 1); }
      draw();
    };
    const board = h('div', { class: 'card wide' }, h('h3', {}, 'Agent board'),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-bottom:8px' }, 'What your Claude sessions, Rosie and Agent Zero are doing. Each one sees this board and claims a slot or repo before changing it.'),
      b.error ? h('div', { class: 'err' }, b.error) : h('div', {},
        b.slots ? h('div', { style: 'margin-bottom:6px' }, b.slots) : null,
        h('b', {}, 'Around now'),
        (b.around || []).length ? h('div', { class: 'list' }, b.around.map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' }, h('b', {}, x.agent + (x.peer ? ` (${x.peer.name}, ${x.peer.status || '?'})` : '') + '  '), h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, (x.cwd ? x.cwd + ' · ' : '') + (x.task || 'no task posted'))))))
          : h('div', { class: 'muted' }, 'Nobody right now.'),
        (b.claims || []).length ? h('div', { style: 'margin-top:8px' }, h('b', {}, 'Claims'), h('div', { class: 'list' }, b.claims.map((x) => h('div', { class: 'item' },
          h('span', { class: 'tx' }, h('b', {}, x.thing + '  '), h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, `${x.agent} since ${tm(x.t)}` + (x.why ? ' · ' + x.why : ''))),
          btn('End', 'sm bad', () => endClaim(x.thing)))))) : null,
        h('div', { style: 'margin-top:8px' }, h('b', {}, 'Recent posts')),
        (b.posts || []).length ? h('div', { class: 'list' }, b.posts.slice(-12).reverse().map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' },
          h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, `${tm(x.t)} ${x.agent}${x.to_agent ? ' → ' + x.to_agent : ''} · ${x.kind}  `), x.text))))
          : h('div', { class: 'muted' }, 'No posts yet.'),
        h('div', { class: 'form a0-board-form', style: 'margin-top:10px' }, noteTo, noteText, btn('Post', 'sm pur', sendNote))));
    if (typingNote()) return;
    const y = $('#view').scrollTop;
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Agent Zero'), h('span', { class: 'muted' }, 'Rosie\'s brain for chat, voice and the phone app. Updates every few seconds.')),
      h('div', { class: 'grid' }, status, chatCard, now, board, calls, log));
    $('#view').scrollTop = y;
  };
  clearInterval(a0Timer);
  a0Timer = setInterval(draw, 4000);
  await draw();
};
