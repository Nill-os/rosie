'use strict';
/* Assistant hub. Plain JS, no libraries. All user/AI text is inserted with textContent or escaped first. */

// ---------- sign-in: one prompt even if several requests fail at once ----------
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

// ---------- helpers ----------
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
  let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
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
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
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

// ---------- state ----------
let chatId = store.get('chat_id', null), chatSpace = null, chatTitle = 'New chat';
let meetTimer = null;
let dirty = false, prevHash = location.hash, sideData = { chats: [], spaces: [] }, overdueCount = 0, unreadCount = 0;
const log = $('#log'), t = $('#t');
// the message box grows as you write (up to ~45% of the screen, then scrolls), so everything you typed stays visible
const fit = () => { if (!t.value) { t.style.height = ''; return; } t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight + 2, innerHeight * 0.45) + 'px'; };
{ const d = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  Object.defineProperty(t, 'value', { get() { return d.get.call(t); }, set(v) { d.set.call(t, v); fit(); } }); }
t.addEventListener('input', fit); addEventListener('resize', fit);
t.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#bar').requestSubmit(); } });

// ---------- header + routing ----------
function setHeader(title, actions) { $('#ttl').textContent = title; $('#actions').replaceChildren(...(actions || [])); }
function show(...nodes) { $('#view').replaceChildren(h('div', { class: 'page' }, ...nodes)); $('#view').scrollTop = 0; }
const NAV = [['home', '⌂', 'Home'], ['inbox', '🔔', 'Inbox'], ['mail', '📧', 'Mail'], ['brief', '☀', 'Daily Brief'], ['paper', '📰', 'Morning Paper'], ['tasks', '✓', 'Tasks'], ['projects', '🧭', 'Projects'], ['calendar', '▦', 'Calendar'], ['meetings', '🎙', 'Meetings'],
  ['terminal', '⌨', 'Terminal'], ['notes', '✎', 'Notes'], ['memory', '◈', 'Memory'], ['skills', '✦', 'Skills'], ['corner', '❀', "Rosie's corner"], ['agentzero', '🧠', 'Agent Zero'], ['settings', '⚙', 'Settings']];
function renderNav(active) {
  $('#nav').replaceChildren(...NAV.map(([k, ic, label]) => h('button', { class: 'nav' + (k === active ? ' on' : ''), onclick: () => go('#/' + k) },
    h('span', { class: 'ic' }, ic), label, k === 'tasks' && overdueCount ? h('span', { class: 'cnt', title: 'Overdue tasks' }, overdueCount) : null,
    k === 'inbox' && unreadCount ? h('span', { class: 'cnt', style: 'background:var(--pur);color:#fff', title: 'Unread' }, unreadCount) : null)));
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
  renderNav(inChat ? '' : name);
  try {
    if (inChat) await showChat(arg); else await (VIEWS[name] || VIEWS.home)(arg);
  } catch (e) {
    if (!inChat) show(h('div', { class: 'err' }, 'Something went wrong: ' + e.message));
  }
  drawChats();
}

// ---------- sidebar: spaces + chats ----------
async function refreshSide() {
  try { sideData = await J('/api/chats'); } catch (e) { $('#dot').className = 'off'; return; }
  $('#dot').className = '';
  try { const b = await J('/api/badges'); overdueCount = b.overdue; unreadCount = b.inbox; } catch (e) { /* badges only */ }
  renderNav((location.hash.replace(/^#\/?/, '') || 'home').split('/')[0].replace('chat', ''));
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

// =====================================================================
// VIEWS
// =====================================================================
const selfRow = (r) => h('div', { class: 'item' },
  h('span', { class: 'chip ' + (r.ok ? 'ok' : 'bad') }, r.ok ? 'ok' : 'fix'),
  h('span', { class: 'tx' }, h('b', {}, r.name + '  '), h('span', { class: 'muted' }, r.detail)));
// Self-test, then a step-by-step walk through everything that is missing: each one pops up its Android prompt, waits for you to come back, checks again, then moves on.
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
    // wait until you have left for the Android prompt and come back, then check again (also gives up after 2 minutes)
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
  if (!out) { if (tries < 20) setTimeout(() => window.runSelfTest(tries + 1), 800); return; }                 // the Settings page may still be loading
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
  const say = (title, line, extra) => { card.replaceChildren(h('h3', {}, title), h('div', { style: 'font-size:20px;margin:14px 0;min-height:56px' }, line), extra || h('span')); };
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
    h('div', { class: 'tx', title: 'Double-click to rename', ondblclick: async () => { const v = prompt('Rename task:', tk.title); if (!v) return; try { await post(`/api/tasks/${tk.id}/rename`, { title: v }); onChange(); } catch (e) { toast(e.message, 1); } } }, tk.title),
    h('button', { class: 'chip' + (late ? ' bad' : ''), title: 'Change due date', onclick: async () => {
      const v = prompt('Move to (today, tomorrow, friday, or 2026-10-09):', tk.due); if (!v) return;
      try { await post(`/api/tasks/${tk.id}/move`, { due: v }); onChange(); } catch (e) { toast(e.message, 1); } } }, tk.due === todayISO() ? 'today' : tk.due),
    h('button', { class: 'x', 'aria-label': 'Delete task', onclick: async () => { if (!confirm('Delete this task?')) return; await del(`/api/tasks/${tk.id}`); onChange(); } }, '✕'));
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

// ----- Home -----
VIEWS.home = async () => {
  setHeader('Home', [btn('＋ New chat', 'sm pur', () => newChat())]);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/home');
  const again = () => keepScroll(() => VIEWS.home());
  const ask = h('input', { placeholder: 'Ask me anything…', 'aria-label': 'Ask anything', id: 'homeask' });
  const hero = h('div', { class: 'card wide' },
    h('div', { style: 'font-size:18px;color:#fff;margin-bottom:4px' }, d.date.pretty),
    h('div', { class: 'muted', style: 'margin-bottom:12px' }, `Day ${d.date.day_of_year} · Week ${d.date.week} · ${d.date.time}`),
    h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); const v = ask.value.trim(); if (!v) return; newChat(); send(v); } }, ask, btn('Ask', 'pur', null, 'submit')),
    h('div', { class: 'form', style: 'margin-top:10px' },
      btn('☀ Start my day', '', () => { newChat(); runCmd('start'); }), btn('🔄 Sync notes', 'dim', () => runRoutine('sync', 'Sync')),
      btn('🌙 Wrap up', 'dim', () => runRoutine('wrap-up', 'Wrap up')), btn('🗣 Talk', 'ok', () => { newChat(); toggleTalk(); })));
  const briefCard = h('div', { class: 'card wide' }, h('h3', {}, "Today's brief", h('a', { href: '#/brief' }, 'ALL BRIEFS →')),
    d.brief ? [mdNode(d.brief.text), h('div', { class: 'form', style: 'margin-top:10px' }, btn('Discuss in chat', 'sm', () => go('#/chat/' + d.brief.chat_id)),
      btn('Regenerate', 'sm dim', async () => { try { await post('/api/briefs/generate', { force: true }); refreshSide(); again(); } catch (e) { toast(e.message, 1); } }))]
      : [h('div', { class: 'empty' }, 'No brief yet today. It is made automatically each morning; you can make it now.'),
        btn('Generate today\'s brief', '', async () => { toast('Making your brief…'); try { await post('/api/briefs/generate', {}); refreshSide(); again(); } catch (e) { toast(e.message, 1); } })]);
  const nowNext = h('div', { class: 'card' }, h('h3', {}, 'Now / next'),
    d.calendar_error ? h('div', { class: 'err' }, d.calendar_error) : [
      h('div', {}, h('span', { class: 'muted' }, 'NOW  '), d.right_now.length ? d.right_now.join(', ') : 'nothing scheduled'),
      h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted' }, 'NEXT '), d.next.length ? d.next.map((n) => `${n.time} ${n.title}`).join('  ·  ') : 'nothing else today')]);
  const inboxCard = h('div', { class: 'card' }, h('h3', {}, 'Inbox & reminders', d.inbox_unread ? h('span', { class: 'chip', style: 'color:var(--pur);border-color:var(--pur)' }, d.inbox_unread + ' new') : null, h('a', { href: '#/inbox' }, 'OPEN →')),
    d.inbox_items.length ? h('div', { class: 'list' }, d.inbox_items.map((i) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg);border-left:3px solid var(--pur)', onclick: () => go(i.link || '#/inbox') }, h('span', { class: 'tx' }, i.title)))) : h('div', { class: 'empty' }, 'All caught up.'),
    d.reminders.length ? h('div', { style: 'margin-top:8px' }, h('div', { class: 'muted' }, 'UPCOMING REMINDERS'), d.reminders.map((r) => h('div', {}, h('span', { class: 'time' }, r.due.slice(11, 16)), ' ', r.text))) : null,
    h('div', { style: 'margin-top:10px' }, remindForm(again)));
  const projCard = h('div', { class: 'card' }, h('h3', {}, 'Projects: next actions', h('a', { href: '#/projects' }, 'OPEN →')),
    d.projects.length ? h('div', { class: 'list' }, d.projects.map((p) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg)', onclick: () => go('#/projects') },
      h('span', { class: 'tx' }, h('b', {}, p.project), h('span', { class: 'muted' }, '  ' + p.next_action)), p.stalled ? h('span', { class: 'chip bad' }, 'quiet ' + p.quiet_days + 'd') : null)))
      : h('div', { class: 'empty' }, 'No project next actions yet. Open Projects to import your folders and set one next step each.'));
  const tl = [...d.overdue, ...d.tasks_today];
  const tasksCard = h('div', { class: 'card' }, h('h3', {}, 'Tasks', h('a', { href: '#/tasks' }, 'OPEN →')),
    tl.length ? h('div', { class: 'list' }, tl.slice(0, 8).map((x) => taskRow(x, again))) : h('div', { class: 'empty' }, 'Nothing due. Nice.'), tl.length > 8 ? h('div', { class: 'muted' }, `+${tl.length - 8} more`) : null,
    h('div', { style: 'margin-top:10px' }, taskAdd(again)));
  const calCard = h('div', { class: 'card' }, h('h3', {}, 'Calendar today', h('a', { href: '#/calendar' }, 'WEEK →')),
    d.calendar_error ? h('div', { class: 'err' }, d.calendar_error) : [
      d.all_day.length ? h('div', { style: 'margin-bottom:6px' }, d.all_day.map((a) => h('span', { class: 'chip' }, a))) : null,
      d.calendar_today.length ? h('div', { class: 'list' }, d.calendar_today.map((e) => h('div', { class: 'item' }, h('span', { class: 'time' }, e.time), h('span', { class: 'tx' }, e.title)))) : h('div', { class: 'empty' }, 'Nothing scheduled today.')]);
  const wf = h('div', { class: 'card' }, h('h3', {}, 'Waiting on / focus', h('a', { href: '#/notes/memory.md' }, 'EDIT →')),
    d.waiting_on.length ? h('div', { class: 'list' }, d.waiting_on.slice(0, 6).map((w) => h('div', { class: 'item' }, h('span', { class: 'tx' }, w)))) : h('div', { class: 'empty' }, 'Nothing you are waiting on.'),
    d.focus.length ? h('div', { style: 'margin-top:8px' }, h('div', { class: 'muted' }, 'FOCUS'), d.focus.slice(0, 3).map((w) => h('div', {}, '• ' + w))) : null);
  const cap = h('input', { placeholder: 'Jot a thought…', 'aria-label': 'Quick capture' });
  const scratch = h('div', { class: 'card' }, h('h3', {}, 'Scratch pad', h('a', { href: '#/notes/' + encodeURIComponent('Scratch Pad.md') }, 'OPEN →')),
    h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); if (!cap.value.trim()) return; try { await post('/api/capture', { text: cap.value }); toast('Saved to scratch pad'); again(); } catch (er) { toast(er.message, 1); } } }, cap, btn('Add', 'pur', null, 'submit')),
    h('div', { class: 'muted', style: 'margin-top:8px' }, d.scratch.lines ? `${d.scratch.lines} unsorted note${d.scratch.lines === 1 ? '' : 's'}. Run Sync to turn them into tasks.` : 'Empty. Everything is sorted.'));
  const recent = h('div', { class: 'card' }, h('h3', {}, 'Recent chats'),
    d.chats.length ? h('div', { class: 'list' }, d.chats.map((c) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg)', onclick: () => go('#/chat/' + c.id) }, h('span', { class: 'tx' }, c.title), c.space ? h('span', { class: 'chip' }, c.space) : null))) : h('div', { class: 'empty' }, 'No chats yet.'));
  const sys = h('div', { class: 'card', id: 'syscard' }, h('h3', {}, 'System', h('a', { href: '#/settings' }, 'SETTINGS →')), h('div', { class: 'muted' }, 'checking…'));
  const healthCard = d.health && d.health.bad && d.health.bad.length ? h('div', { class: 'card', style: 'border-color:var(--bad)' }, h('h3', {}, '⚠ Needs attention'), h('div', {}, d.health.bad.join(' · ')), h('div', { class: 'form', style: 'margin-top:10px' }, btn('See details', 'sm bad', () => go('#/settings')))) : null;
  show(h('div', { class: 'grid' }, healthCard, hero, briefCard, nowNext, inboxCard, projCard, tasksCard, calCard, wf, scratch, recent, sys));
  J('/api/status').then((s) => { const c = $('#syscard'); if (!c) return;
    c.append(h('div', { class: 'form' }, h('span', { class: 'chip ' + (s.model.where === 'GPU' ? 'ok' : '') }, 'AI: ' + (s.model.model || 'n/a') + ' · ' + (s.model.where || s.model.error || '?')),
      h('span', { class: 'chip ' + (s.google.ok ? 'ok' : 'bad') }, s.google.ok ? 'Google: connected' : 'Google: login needed'),
      h('span', { class: 'chip' }, 'Calendar writes → ' + (s.write_target === 'test' ? 'TEST calendar' : 'REAL calendar')))); c.querySelector('.muted')?.remove(); }).catch(() => {});
};

// ----- Inbox + reminders -----
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
        h('div', { class: 'tx' }, h('div', { style: 'color:#fff' }, p.title), p.detail ? h('div', { class: 'muted md', style: 'font-size:13px' }, p.detail) : null, p.source.startsWith('email:') ? h('span', { class: 'chip' }, 'from email') : null),
        btn('Approve', 'sm ok', async () => { try { await post(`/api/proposals/${p.id}/approve`); toast('Done'); again(); } catch (e) { toast(e.message, 1); } }),
        btn('Dismiss', 'sm dim', async () => { await post(`/api/proposals/${p.id}/dismiss`); again(); }))))) : null,
    h('div', { class: 'card' }, h('h3', {}, 'Reminders'), remindForm(again),
      d.reminders.length ? h('div', { class: 'list', style: 'margin-top:10px' }, d.reminders.map((r) => h('div', { class: 'item' }, h('span', { class: 'time' }, r.due.slice(5, 16).replace('T', ' ')), h('span', { class: 'tx' }, r.text),
        h('button', { class: 'x', 'aria-label': 'Cancel reminder', onclick: async () => { await del('/api/reminders/' + r.id); again(); } }, '✕')))) : h('div', { class: 'empty', style: 'margin-top:8px' }, 'No upcoming reminders. You can also just say "remind me in 20 minutes to…".')),
    h('div', { style: 'margin-top:14px' }, d.items.length ? h('div', { class: 'list' }, d.items.map((i) => h('div', { class: 'item', style: 'align-items:flex-start;cursor:pointer;' + (i.read ? 'opacity:.6' : 'border-left:3px solid var(--pur)'),
      onclick: async () => { if (!i.read) { await post(`/api/inbox/${i.id}/read`); refreshSide(); } if (i.link) go(i.link); else again(); } },
      h('span', { style: 'width:24px' }, ICON[i.kind] || '•'), h('div', { class: 'tx' }, h('div', { style: 'color:#fff' }, i.title), i.body ? h('div', { class: 'muted md' }, i.body) : null),
      h('span', { class: 'muted', style: 'font-size:12px;white-space:nowrap' }, ago(i.created)),
      h('button', { class: 'x', 'aria-label': 'Delete', onclick: async (e) => { e.stopPropagation(); await del('/api/inbox/' + i.id); again(); } }, '✕')))) : h('div', { class: 'empty' }, 'Nothing here yet. I\'ll post your daily brief, meeting reminders and nudges here.')));
};

// ----- Mail (read-only monitoring) -----
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
      m.suspicious ? h('div', { style: 'color:var(--bad)' }, '⚠ Possible phishing: ' + m.reason) : [h('div', { class: 'muted' }, m.summary), m.reason ? h('div', { class: 'muted', style: 'font-size:12px' }, m.reason + (m.needs_reply ? ' · needs a reply' : '')) : null],
      h('div', { class: 'form', style: 'margin-top:8px' },
        h('a', { class: 'btn sm dim', href: m.link, target: '_blank', rel: 'noopener noreferrer', style: 'display:inline-flex;align-items:center;text-decoration:none' }, 'Open in Gmail'),
        m.suspicious ? null : btn('Draft reply', 'sm', () => draftFlow(m)),
        btn('Done', 'sm ok', async () => { await post('/api/mail/handled', { account: m.account, id: m.id }); again(); }))));
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
          h('li', {}, 'In a terminal run ', h('code', {}, '~/assistant/login-gmail.sh'), '. A browser opens: pick your Gmail, click Advanced, Go to assistant (unsafe), and allow "View your email messages".'),
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
    sec('Needs you', need, 'today'), sec('FYI', fyi), sec('Possible phishing (ignored)', sus), sec('Low priority', low));
};

// ----- Meetings (consent-first recorder) -----
VIEWS.meetings = async () => {
  clearInterval(meetTimer);
  setHeader('Meetings', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/meetings');
  const again = () => keepScroll(() => VIEWS.meetings());
  const recent = h('div', { style: 'margin-top:16px' }, h('div', { class: 'dayhead' }, 'Recent · ' + d.recent.length),
    d.recent.length ? h('div', { class: 'list' }, d.recent.map((m) => h('div', { class: 'item' }, h('div', { class: 'tx' }, h('div', {}, h('b', {}, m.title)), h('div', { class: 'muted', style: 'font-size:12px' }, (m.started || '').replace('T', ' ').slice(0, 16) + (m.participants ? ' · ' + m.participants : ''))),
      h('span', { class: 'chip' + (m.consent === 'own_voice_only' ? '' : ' ok') }, m.consent === 'own_voice_only' ? 'my voice only' : m.consent ? 'everyone agreed' : ''), h('span', { class: 'chip' + (m.status === 'done' ? ' ok' : m.status.startsWith('failed') ? ' bad' : '') }, m.status),
      m.transcript ? h('a', { class: 'btn sm dim', href: '#/notes/' + encodeURIComponent(m.transcript), style: 'text-decoration:none;display:inline-flex;align-items:center' }, 'Open') : null))) : h('div', { class: 'empty' }, 'No recordings yet.'));
  if (d.active) {
    const a = d.active, el = h('span', {}, a.elapsed_min + ' min');
    meetTimer = setInterval(async () => { try { const x = await J('/api/meetings'); if (!x.active) { clearInterval(meetTimer); again(); } else el.textContent = x.active.elapsed_min + ' min'; } catch (e) { /* offline */ } }, 10000);
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Meetings')),
      h('div', { class: 'card', style: 'border:2px solid var(--bad)' }, h('h3', { style: 'color:var(--bad)' }, '● Recording now'),
        h('div', { style: 'font-size:18px;color:#fff' }, a.title), h('div', { class: 'muted', style: 'margin:6px 0' }, 'Running for ', el, ' · ' + (a.tracks.includes('them') ? 'your microphone + the call audio' : 'your microphone only')),
        h('div', { class: 'muted', style: 'font-size:13px' }, 'Consent confirmed: ' + a.consent.text + (a.consent.note ? ' "' + a.consent.note + '"' : '')),
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
      h('div', { class: 'muted', style: 'font-size:12px;margin-top:10px' }, 'Your mic: ' + (d.devices.mic_label || d.devices.mic || 'not found') + ' · Call sound from: ' + (d.devices.call_label || d.devices.call || 'not found') + '. Wearing a headset? Select it in your system sound settings BEFORE you start.',
        h('br'), 'Two tracks are kept apart so lines are labelled Me and Them. Audio is deleted after the transcript is made. Other sounds playing on this PC (music, notifications) are recorded as part of "Them", so mute them first. The assistant can never start a recording; only you can, here.')), recent);
  refresh();
};

// ----- Projects -----
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
      h('div', { class: 'card wide' }, h('h3', {}, 'Next action'), nxt, h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'Just one. When you finish it, write the next one. This is what shows on Home and in your morning brief.')),
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
      h('div', { class: 'card' }, h('h3', {}, 'Notes'), p.notes_path ? h('div', {}, h('a', { href: '#/notes/' + encodeURIComponent(p.notes_path), style: 'color:var(--acc)' }, 'Open the project notes file →'), h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'Plain text in your vault. The assistant can read and add to it.')) : h('div', { class: 'empty' }, 'No notes file yet.'),
        h('div', { style: 'margin-top:14px' }, btn('Delete project', 'sm bad', async () => { if (!confirm('Delete "' + p.name + '"? Its tasks stay in your task list; the notes file and folder are not touched.')) return; await del('/api/projects/' + pid); go('#/projects'); })))));
}

// ----- Terminal + AI agents -----
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
function closeTerm() { try { termRO && termRO.disconnect(); } catch (e) { /* gone */ } try { termWs && termWs.close(); } catch (e) { /* gone */ } try { term && term.dispose(); } catch (e) { /* gone */ } term = termWs = termFit = termRO = null; }
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
    h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'This session keeps running when you leave this page. If an agent asks for permission, answer it here.'));
  try { await loadXterm(); } catch (e) { status.textContent = 'failed to load terminal'; return; }
  term = new window.Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, fontFamily: 'ui-monospace, "DejaVu Sans Mono", Menlo, Consolas, monospace',
    theme: { background: '#000000', foreground: '#E8E8F0', cursor: '#00F0FF', selectionBackground: '#9D00FF66', black: '#000000', brightBlack: '#4A607A' } });
  termFit = new window.FitAddon.FitAddon(); term.loadAddon(termFit); term.open(host); termFit.fit();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  termWs = new WebSocket(`${proto}://${location.host}/ws/term/${encodeURIComponent(name)}`); termWs.binaryType = 'arraybuffer';
  const sendSize = () => { if (termWs && termWs.readyState === 1 && term) termWs.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); };
  termWs.onopen = () => { status.textContent = 'connected'; status.className = 'chip ok'; termFit.fit(); sendSize(); term.focus(); };
  termWs.onmessage = (e) => term && term.write(new Uint8Array(e.data));
  termWs.onclose = (e) => { status.textContent = e.code === 4404 ? 'session ended' : 'disconnected'; status.className = 'chip bad'; if (term) term.write('\r\n\x1b[90m[' + status.textContent + ']\x1b[0m\r\n'); };
  term.onData((d) => send(d)); term.onResize(sendSize);
  let t = null; termRO = new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => { try { termFit.fit(); } catch (e) { /* hidden */ } }, 120); }); termRO.observe(host);
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
      btn('Start', 'ok', null, 'submit')), h('div', { class: 'muted', style: 'font-size:12px;margin-top:8px' }, 'Agents ask their own permission questions inside the session; you answer them here. I never start an agent in "skip permissions" mode.'));
  sync();
  const list = h('div', { class: 'list' }, d.sessions.length ? d.sessions.map((s) => h('div', { class: 'item', style: 'cursor:pointer' + (s.name === arg ? ';border-color:var(--acc)' : ''), onclick: () => go('#/terminal/' + s.name) },
    h('div', { class: 'tx' }, h('div', {}, h('b', {}, s.name), ' ', s.kind === 'agent' ? h('span', { class: 'chip', style: 'color:var(--pur);border-color:var(--pur)' }, s.agent) : null), h('div', { class: 'muted', style: 'font-size:12px' }, s.folder.replace(/^\/home\/[^/]+/, '~') + ' · ' + (s.idle ? 'idle' : 'running ' + s.running))),
    h('button', { class: 'x', 'aria-label': 'Stop ' + s.name, onclick: async (e) => { e.stopPropagation(); if (!confirm('Stop ' + s.name + '?')) return; await del('/api/terminals/' + s.name); if (s.name === arg) go('#/terminal'); else again(); } }, '✕'))) : h('div', { class: 'empty' }, 'No sessions yet.'));
  const pane = h('div', { class: 'tpane' }, arg ? null : h('div', { class: 'card' }, h('div', { class: 'empty' }, 'Start a session on the left, or open one. Sessions keep running when you leave this page, and I can start agents for you too ("launch claude in my android project to fix …").')));
  show(h('div', { class: 'termpage' + (arg ? ' open' : '') }, h('div', { class: 'tl' }, newForm, h('div', { style: 'margin-top:14px' }, h('div', { class: 'dayhead' }, 'Sessions · ' + d.sessions.length), list)), pane));
  if (arg) { if (!d.sessions.some((s) => s.name === arg)) { pane.replaceChildren(h('div', { class: 'err' }, 'That session has ended.')); return; } await openTerminal(arg, pane); }
};

// ----- Daily brief -----
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

// ----- Tasks -----
VIEWS.tasks = async () => {
  setHeader('Tasks', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/tasks');
  const again = async () => { await keepScroll(() => VIEWS.tasks()); refreshSide(); };
  const sec = (title, list, cls) => list.length ? h('div', { style: 'margin-top:16px' }, h('div', { class: 'dayhead ' + (cls || '') }, `${title} · ${list.length}`), h('div', { class: 'list' }, list.map((x) => taskRow(x, again)))) : null;
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Tasks'), h('span', { class: 'muted' }, 'Tip: double-click a title to rename; tap the date to move it')),
    h('div', { class: 'card' }, taskAdd(again, 'Add a task… (due can be: tomorrow, friday, 2026-10-09)')),
    sec('Overdue', d.overdue, 'today'), sec('Today', d.due_today), sec('Upcoming', d.upcoming), sec('Done recently', d.done_recent),
    !(d.overdue.length + d.due_today.length + d.upcoming.length) ? h('div', { class: 'empty', style: 'margin-top:16px' }, 'Nothing open. Add one above, or just tell the assistant.') : null);
};

// ----- Calendar -----
VIEWS.calendar = async () => {
  setHeader('Calendar', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/calendar?days=7');
  const title = h('input', { placeholder: 'New event title', 'aria-label': 'Event title' }), date = h('input', { type: 'date', value: todayISO(), 'aria-label': 'Date' });
  const st = h('input', { type: 'time', value: '09:00', 'aria-label': 'Start' }), en = h('input', { type: 'time', value: '10:00', 'aria-label': 'End' });
  const where = d.write_target === 'primary' ? 'your REAL calendar' : 'the TEST calendar (change in Settings)';
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Next 7 days'), h('div', { class: 'sp' }), btn('Ask assistant to change something', 'sm pur', () => { newChat(); t.value = 'On my calendar: '; t.focus(); })),
    d.error ? h('div', { class: 'err' }, d.error + ' (run ~/assistant/login.sh)') : null,
    h('div', { class: 'card' }, h('h3', {}, 'Add event'), h('div', { class: 'muted', style: 'margin-bottom:8px' }, 'Saves to ' + where + '. To move or delete events, ask the assistant (it asks you to confirm).'),
      h('form', { class: 'form', onsubmit: async (e) => { e.preventDefault(); try { const r = await post('/api/calendar/event', { title: title.value, date: date.value, start: st.value, end: en.value }); toast('Added to the ' + r.calendar.toUpperCase() + ' calendar'); VIEWS.calendar(); } catch (er) { toast(er.message, 1); } } },
        title, date, st, en, btn('Add', 'pur', null, 'submit'))),
    d.days.map((day) => h('div', {}, h('div', { class: 'dayhead' + (day.date === todayISO() ? ' today' : '') }, day.pretty + (day.date === todayISO() ? '  · today' : '')),
      day.events.length ? h('div', { class: 'list' }, day.events.map((e) => h('div', { class: 'item' }, h('span', { class: 'time' }, e.all_day ? 'all day' : e.start), h('span', { class: 'tx' }, e.title, e.location ? h('span', { class: 'muted' }, '  @ ' + e.location) : null), e.end ? h('span', { class: 'muted' }, '→ ' + e.end) : null))) : h('div', { class: 'empty' }, 'Nothing.'))));
};

// ----- Notes (vault editor) -----
let noteCur = null;
VIEWS.notes = async (arg) => {
  setHeader('Notes', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const tree = await J('/api/vault');
  const wrap = h('div', { class: 'notes' + (arg ? ' edit' : '') });
  const files = h('div', { class: 'files' });
  const found = h('div', { class: 'list' }), q = h('input', { placeholder: 'Search all notes…', 'aria-label': 'Search notes' });
  files.append(h('form', { class: 'form', style: 'margin-bottom:8px', onsubmit: async (e) => { e.preventDefault(); found.replaceChildren(); if (!q.value.trim()) return; try { const r = await J('/api/search?q=' + encodeURIComponent(q.value));
    found.replaceChildren(...(r.results.length ? r.results.map((x) => h('button', { class: 'item', style: 'text-align:left;cursor:pointer;background:var(--bg);flex-direction:column;align-items:flex-start', onclick: () => go('#/notes/' + encodeURIComponent(x.path)) }, h('b', {}, x.title), h('span', { class: 'muted md', style: 'font-size:12px', html: fmt(x.snippet).replace(/\[/g, '<b style="color:var(--acc)">').replace(/\]/g, '</b>') }))) : [h('div', { class: 'empty' }, 'Nothing found.')])); } catch (er) { toast(er.message, 1); } } }, q, btn('Find', 'sm', null, 'submit')), found);
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

// ----- Memory -----
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
      h('div', { class: 'card wide' }, h('h3', {}, 'Working memory', h('a', { href: '#/notes/memory.md' }, 'EDIT FILE →')),
        h('div', { class: 'grid' }, W.map(([k, label]) => h('div', {}, h('div', { class: 'muted' }, label.toUpperCase()), d.working[k].length ? d.working[k].map((x) => h('div', {}, '• ' + x)) : h('div', { class: 'empty' }, '—')))))));
};

// ----- Skills -----
VIEWS.skills = async () => {
  setHeader('Skills', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/skills');
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Skills'), h('span', { class: 'muted' }, 'Playbooks the assistant follows. Plain text you can edit.'), h('div', { class: 'sp' }),
    btn('＋ New skill', 'pur', async () => { const n = prompt('Name for the new skill (e.g. weekly review):'); if (!n) return; try { const r = await post('/api/vault/new', { group: 'Skills', name: n }); go('#/notes/' + encodeURIComponent(r.path)); } catch (e) { toast(e.message, 1); } })),
    h('div', { class: 'grid' }, d.skills.map((s) => h('div', { class: 'card' }, h('h3', {}, s.name), h('div', { class: 'muted', style: 'margin-bottom:10px' }, s.description),
      h('div', { class: 'form' }, btn('Edit', 'sm', () => go('#/notes/' + encodeURIComponent(s.path))))))));
};

// ----- Settings -----
async function autoSet(obj) { try { await post('/api/automations', obj); toast('Saved'); } catch (e) { toast(e.message, 1); } }
const onOff = (v, fn) => { const w = h('div', { class: 'seg' }); const draw = (cur) => w.replaceChildren(h('button', { class: cur ? 'on' : '', onclick: () => { draw(true); fn(true); } }, 'On'), h('button', { class: cur ? '' : 'on', onclick: () => { draw(false); fn(false); } }, 'Off')); draw(v); return w; };
const modeSel = (v, fn) => { const w = h('div', { class: 'seg' }); const draw = (cur) => w.replaceChildren(...['off', 'nudge', 'auto'].map((m) => h('button', { class: cur === m ? 'on' : '', onclick: () => { draw(m); fn(m); } }, m))); draw(v); return w; };
const timeIn = (v, fn) => h('input', { type: 'time', value: v, 'aria-label': 'Time', style: 'flex:none;width:150px', onchange: (e) => e.target.value && fn(e.target.value) });
const numSel = (v, opts, fn, suffix) => h('select', { 'aria-label': 'Minutes', onchange: (e) => fn(+e.target.value) }, opts.map((o) => h('option', { value: o, selected: o === v }, o + (suffix || ''))));
const autoRow = (label, a, b, hint) => [h('dt', {}, label), h('dd', {}, h('div', { class: 'form' }, a, b), h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, hint))];
const healthRow = (r) => h('div', { class: 'item' },
  h('span', { class: 'chip ' + (r.level === 'ok' ? 'ok' : r.level === 'off' ? '' : 'bad') }, r.level === 'ok' ? 'ok' : r.level === 'off' ? 'off' : r.level === 'warn' ? 'soon' : 'fix'),
  h('span', { class: 'tx' }, h('b', {}, r.name + '  '), h('span', { class: 'muted' }, r.detail + (r.fix ? '  →  ' + r.fix : ''))));
// Pick the local model Rosie thinks with. The server tests it with one answer before switching, so a bad pick never breaks her.
function modelPicker(md, live, again) {
  const where = live.model ? (live.loaded ? `loaded: ${live.model} · ${live.where}` : live.where) : (live.error || '');
  if (!md || md.error || !md.models) return h('div', {}, h('div', {}, live.model || 'unknown'), h('div', { class: 'muted', style: 'font-size:12px' }, (md && md.error) || 'Model list unavailable.'));
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
    note = h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, `Now: ${md.current}` + (where ? ' · ' + where : '') + '. Used for chat, her corner and her memory. Applies right away; no restart.'),
    md.problem ? h('div', { style: 'font-size:12px;color:var(--bad)' }, md.problem) : null);
}
// Settings > Context window: how much text the model can hold at once, measured per model so it stays fully on the graphics card.
function ctxBox(cx, again) {
  if (!cx || cx.error) return h('div', { class: 'muted' }, (cx && cx.error) || 'unknown');
  const c = cx.current || {}, st = cx.status || {};
  const how = !cx.tuned ? 'default, not tuned yet' : c.num_gpu ? 'every layer on the graphics card' : c.fits === false ? 'too big for the graphics card: part runs on the CPU' : 'Ollama decides where it runs';
  const line = h('div', {}, `${(c.num_ctx || 0).toLocaleString()} tokens`, h('span', { class: 'muted' }, ` · ${how}` + (c.tuned_at ? ` · tuned ${c.tuned_at.replace('T', ' ')}` : '')));
  const note = h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, st.running ? st.step : st.error ? 'Last tune failed: ' + st.error
    : `Finds the biggest window that keeps ${cx.model} fully on the graphics card. Takes 2 to 5 minutes; answers wait meanwhile. Agent Zero follows.`);
  const b = btn(st.running ? 'Tuning…' : 'Auto-tune', 'sm pur', async () => {
    try { await post('/api/ctx/tune', {}); toast('Tuning the context window. This takes a few minutes…'); b.disabled = true; poll(); } catch (e) { toast(e.message, 1); }
  });
  b.disabled = !!st.running;
  const poll = async () => {
    const r = await J('/api/ctx').catch(() => null);
    if (!r) return setTimeout(poll, 3000);
    if (r.status.running) { note.textContent = r.status.step; b.textContent = 'Tuning…'; return setTimeout(poll, 2000); }
    toast(r.status.error ? 'Tune failed: ' + r.status.error : `Context window: ${r.current.num_ctx.toLocaleString()} tokens`, !!r.status.error); again();
  };
  if (st.running) setTimeout(poll, 2000);
  return h('div', {}, line, h('div', { class: 'form', style: 'margin-top:6px' }, b), note);
}
// Settings tabs: General, plus Newspaper when the Morning Paper is installed (paper.js); #/settings/paper opens it directly.
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
VIEWS.settings = async (arg) => {
  if (arg === 'paper' && typeof ppSettings === 'function') return settingsPaper();
  setHeader('Settings', []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const KEYS_AI = [['Alt_R', 'Right Alt'], ['Control_R', 'Right Ctrl'], ['Super_R', 'Super_R'], ['Pause', 'Pause'], ['Scroll_Lock', 'Scroll Lock'], ['Menu', 'Menu key'], ['F8', 'F8'], ['F9', 'F9'], ['F10', 'F10'], ['Shift_L+Super_L+keycode:201', 'Copilot / Cortana key']];
  const [s, au, dc, mc, hl, md, cx] = await Promise.all([J('/api/status'), J('/api/automations'), J('/api/dictation'), J('/api/mcp'), J('/api/health'), J('/api/models').catch((e) => ({ error: e.message })), J('/api/ctx').catch((e) => ({ error: e.message }))]);
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
  const saveSpaces = async (list) => { try { await put('/api/spaces', { spaces: list }); await refreshSide(); again(); } catch (e) { toast(e.message, 1); } };
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v)];
  show(h('div', { class: 'pagehead' }, h('h2', {}, 'Settings')), settingsTabs('general'),
    h('div', { class: 'grid' },
      h('div', { class: 'card' }, h('h3', {}, 'System'), h('dl', { class: 'kv' },
        row('AI model', modelPicker(md, s.model, again)),
        row('Context window', ctxBox(cx, again)),
        row('Brain', h('div', {}, h('div', { class: 'seg' }, h('button', { class: s.brain !== 'agent_zero' ? 'on' : '', onclick: () => setBrain('rosie') }, 'Rosie'), h('button', { class: s.brain === 'agent_zero' ? 'on' : '', onclick: () => setBrain('agent_zero') }, 'Agent Zero')), h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, 'Who answers chat, voice and the phone app. Agent Zero is better at big tasks but slower; it uses Rosie\'s tools, and texts, calls, deletes and PC commands still wait for your Yes. If it is down, Rosie answers.'))),
        row('GPU', s.gpu.name ? [s.gpu.name, h('div', { class: 'bar' }, h('i', { style: 'width:' + gpuPct + '%' })), h('span', { class: 'muted' }, `${s.gpu.used_mb} / ${s.gpu.total_mb} MB`)] : (s.gpu.error || 'not found')),
        row('Google Calendar', s.google.ok ? 'connected' : h('span', { style: 'color:var(--bad)' }, (s.google.error || 'login needed') + ' · run ~/assistant/login.sh')),
        row('Phone address', s.tailscale.name ? s.tailscale.name : (s.tailscale.error || 'Tailscale not running')),
        row('Disk free', s.disk_free_gb + ' GB'))),
      h('div', { class: 'card' }, h('h3', {}, 'Health'),
        hl.rows.length ? h('div', { class: 'list' }, hl.rows.map(healthRow)) : h('div', { class: 'muted' }, 'Checking everything… open this page again in a few seconds.'),
        h('div', { class: 'form', style: 'margin-top:10px' }, btn('Check again', 'sm', async () => { toast('Checking…'); await J('/api/health?fresh=true'); again(); }))),
      h('div', { class: 'card' }, h('h3', {}, 'Calendar writes'), h('div', { class: 'muted', style: 'margin-bottom:10px' }, 'Where the assistant adds and changes events. Reading always uses your real calendar.'),
        h('div', { class: 'seg' + (s.write_target === 'primary' ? ' warn' : '') }, h('button', { class: s.write_target === 'test' ? 'on' : '', onclick: () => setTarget('test') }, 'Test calendar'), h('button', { class: s.write_target === 'primary' ? 'on' : '', onclick: () => setTarget('primary') }, 'Real calendar'))),
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
          ...autoRow('Find tasks and dates', onOff(A.mail_find_tasks, (v) => autoSet({ mail_find_tasks: v })), null, 'Suggests tasks/events from mail. You approve each one.'),
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
            h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'Notification access lets her read your notifications and answer messages with their reply box. Screen control lets her look at the screen and tap, type and scroll for you (find "Rosie screen control" under Android Settings > Accessibility; Android may ask you to allow restricted settings first). Banking, payment, password and security screens are blocked, she never types passwords, Send / Pay / Delete / Buy buttons always ask you, a "Rosie is controlling your screen" notice with a Stop button shows the whole time, and she gives up after 12 steps.')) : null,
          h('div', { class: 'muted', style: 'margin:14px 0 4px;font-weight:700' }, 'How she confirms (like Siri and Gemini)'),
          h('dl', { class: 'kv' },
            ...autoRow('Texts', h('select', { 'aria-label': 'Texts', onchange: (e) => autoSet({ sms_confirm: e.target.value }) }, [['ask', 'Read it back, wait for send / change / cancel'], ['headphones', 'Auto-send when headphones are on'], ['always', 'Always auto-send']].map(([k, t]) => h('option', { value: k, selected: k === A.sms_confirm }, t))), null, 'Auto-send says what it is sending, then waits 3 seconds for you to say "cancel" before sending.'),
            ...autoRow('Calls', h('select', { 'aria-label': 'Calls', onchange: (e) => autoSet({ call_confirm: e.target.value }) }, [['announce', 'Say who, call after 3 seconds (cancel any time)'], ['ask', 'Ask first'], ['now', 'Call right away']].map(([k, t]) => h('option', { value: k, selected: k === A.call_confirm }, t))), null, 'If a name matches more than one person she asks which one.'),
            ...autoRow('When I pause', h('select', { 'aria-label': 'Pause length', onchange: (e) => { AndroidBridge.setIntPref('endpoint_ms', parseInt(e.target.value, 10)); } }, [[800, 'Quick: answer after a short pause'], [1200, 'Normal'], [2000, 'Patient: wait while I think']].map(([k, t]) => h('option', { value: k, selected: k === AndroidBridge.getIntPref('endpoint_ms', 1200) }, t))), null, 'How long she waits after you stop talking before she starts answering.')),
          h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'Notes, alarms, timers, music, navigation and searches are just done and she tells you what she did. Deleting things, mail cleanup and anything that spends money always asks first.'));
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
            const flag = (name, label, hint) => { const on = AndroidBridge.getFlag(name); return h('div', { style: 'margin-top:12px' }, h('div', { class: 'form' }, btn(label + ': ' + (on ? 'ON' : 'OFF'), on ? 'ok' : 'sm', () => { AndroidBridge.setFlag(name, !on); setTimeout(again, 600); })), h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, hint)); };
            const asst = AndroidBridge.isAssistant();
            return h('div', {}, h('div', { class: 'muted', style: 'margin-top:12px' }, 'Microphone in use: ' + AndroidBridge.micInfo()),
              h('div', { class: 'muted', style: 'margin-top:14px;font-weight:700' }, 'Sounds'),
              (() => {
                const style = (AndroidBridge.getPref('sound_style', 'game') === 'glitch' ? 'game' : AndroidBridge.getPref('sound_style', 'game')), vol = AndroidBridge.getPref('sound_vol', 'medium');
                const sel = (key, cur, opts, label) => h('select', { 'aria-label': label, onchange: (e) => { AndroidBridge.setPref(key, e.target.value); AndroidBridge.previewSound('wake', key === 'sound_style' ? e.target.value : AndroidBridge.getPref('sound_style', 'game')); } }, opts.map(([k, t]) => h('option', { value: k, selected: k === cur }, t)));
                const prev = (kind, label) => btn(label, 'sm', () => AndroidBridge.previewSound(kind, AndroidBridge.getPref('sound_style', 'game')));
                return h('div', {}, h('div', { class: 'form', style: 'margin-top:8px' }, sel('sound_style', style, [['game', 'Video game'], ['soft', 'Soft chime'], ['off', 'No sounds']], 'Sound style'), sel('sound_vol', vol, [['low', 'Quiet'], ['medium', 'Medium'], ['high', 'Loud']], 'Sound volume')),
                  h('div', { class: 'form', style: 'margin-top:8px' }, prev('on', '▶ Turned on'), prev('wake', '▶ Listening'), prev('thinking', '▶ Thinking'), prev('done', '▶ Done')),
                  h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, 'Listening: she heard you and is ready. Thinking: she stopped listening and is working. Done: the conversation ended. Tap a button to hear it.'));
              })(),
              (() => { const off = AndroidBridge.getFlag('novibe'); return h('div', { class: 'form', style: 'margin-top:10px' }, btn('Vibration: ' + (off ? 'OFF' : 'ON'), off ? 'sm' : 'ok', () => { AndroidBridge.setFlag('novibe', !off); setTimeout(again, 400); })); })(),
              (() => { const on = AndroidBridge.getFlagD('pocketrule', true); return h('div', { style: 'margin-top:12px' }, h('div', { class: 'form' }, btn('In a pocket, only use headphones: ' + (on ? 'ON' : 'OFF'), on ? 'ok' : 'sm', () => { AndroidBridge.setFlag('pocketrule', !on); setTimeout(again, 500); })), h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, 'When the phone is covered (a pocket or bag) with the screen off, Rosie only listens through a Bluetooth or wired headset microphone. With no headset she stays off. Out of a pocket she works as normal.')); })(),
              flag('btmic', 'Listen through my Bluetooth headset', 'Hears you through the headset microphone even with the phone in a pocket. While on, music through the headset sounds like a phone call. A wired or USB headset is used automatically.'),
              h('div', { style: 'margin-top:12px' }, h('div', { class: 'form' }, asst ? h('span', { class: 'chip ok' }, 'headphone button opens Rosie') : btn('Use my headphone button for Rosie', 'sm pur', () => AndroidBridge.setAssistant())), h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, asst ? 'Press and hold the button on your headphones (or the power-button assistant gesture) and Rosie starts listening. This replaces Google Assistant for those buttons, so "Hey Google" stops working.' : 'Makes Rosie your phone\'s assistant, so holding the headphone button starts her instead of Google. Android asks you to confirm.')));
          })(),
          w.battery === false ? h('div', { style: 'margin-top:10px' }, h('div', { class: 'form' }, btn('Let Rosie run in the background', 'sm pur', () => AndroidBridge.askBatteryUnrestricted())), h('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' }, 'Without this Android may stop her to save battery, so "Hey Rosie" and phone control would quietly stop working.')) : null,
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
        h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Always blocked (' + mc.blocked.length + ')'), h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, mc.blocked.join('  ·  ') + '  ·  your .ssh keys, .env files, password/token/credential files, the assistant\'s secrets and databases')),
        h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Recent requests from agents (' + mc.audit.length + ')'), h('pre', { style: 'font-size:12px;white-space:pre-wrap;color:var(--mute)' }, mc.audit.join('\n') || 'none yet')),
        h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted' }, 'Add to another agent (Cursor, VS Code…)'), h('pre', { style: 'font-size:12px;white-space:pre-wrap' }, JSON.stringify({ mcpServers: { 'assistant-notes': { command: mc.command.split(' ')[0], args: [mc.command.split(' ')[1]] } } }, null, 2)))),
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
  if (window.AndroidBridge) {            // the phone app shows phone settings only: PC features (dictation, desktop voice key, meeting recorder, notes-sharing, the app download) are not on this device
    const pcOnly = ['Dictation (hold to talk)', 'Talk to the AI anywhere', 'Android app', 'Meetings and names', 'Connect AI agents to your notes (MCP)'];
    document.querySelectorAll('.card').forEach((c) => { const t = c.querySelector('h3'); if (t && pcOnly.includes(t.textContent.trim())) c.remove(); });
  }
};

// =====================================================================
// CHAT (conversation, voice, slash commands)
// =====================================================================
const BIG = [['⏱ What now?', () => whatnow()], ['🏃 Running late', () => send("I'm running late. Ask me how many minutes behind I am, then fix my afternoon.")], ['➕ Add task', () => addTask()]];
$('#btns').replaceChildren(...BIG.map(([l, f]) => h('button', { type: 'button', onclick: f }, l)));
const GREETING = "Hi, I'm Rosie. Tell me what you need: type, or tap TALK and speak. Type / for commands. I'll tell you straight if something won't work.";
function add(txt, cls) { const d = document.createElement('div'); d.className = 'm ' + cls; if (cls.includes('bot')) d.innerHTML = fmt(String(txt)); else d.textContent = txt; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }
function setChatHeader() { setHeader(chatTitle + (chatSpace ? '  [' + chatSpace + ']' : ''), chatId ? [btn('✎', 'sm dim', async () => { const v = prompt('Rename chat:', chatTitle); if (!v) return; await patch('/api/chats/' + chatId, { title: v }); chatTitle = v; setChatHeader(); refreshSide(); }),
  btn('🗑', 'sm bad', async () => { if (!confirm('Delete this chat for good?')) return; await del('/api/chats/' + chatId); newChat(); refreshSide(); })] : []); }
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
async function showChat(arg) {
  if (arg) {
    const id = +arg;
    if (id !== chatId || !log.children.length) { const ok = await loadChat(id); if (!ok) { toast('That chat no longer exists', 1); newChat(); return; } }
  } else if (!log.children.length) resetLog();
  setChatHeader(); setTimeout(() => { log.scrollTop = log.scrollHeight; }, 0);
}
function newChat(space) {
  if (talking) stopTalk();
  chatId = null; chatSpace = space || null; chatTitle = 'New chat'; store.del('chat_id'); pendingConf = null;
  resetLog(); setChatHeader(); go('#/chat'); setTimeout(() => t.focus(), 50);
}
function confirmBox(p) {
  const d = h('div', { class: 'conf' }, h('b', {}, 'Please confirm'), h('div', {}, p.summary), h('div', { class: 'b' },
    h('button', { class: 'yes', type: 'button', onclick: async () => { try { const r = await api('/api/confirm', { token: p.token }); d.remove(); add(r.status === 'done' ? '✅ Done.' : '⚠️ ' + (r.message || 'Failed'), 'bot'); } catch (e) { add('⚠️ Could not reach the server. The card is still here: try again.', 'bot'); } } }, 'Yes, do it'),
    h('button', { class: 'no', type: 'button', onclick: async () => { try { await api('/api/cancel', { token: p.token }); d.remove(); add('Cancelled. Nothing changed.', 'bot'); } catch (e) { add('⚠️ Could not reach the server. The card is still here: try again.', 'bot'); } } }, 'No')));
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
async function send(text) {
  if (text.trim().startsWith('/')) return handleSlash(text.trim());
  add(text, 'me'); const w = add('Thinking…', 'bot dim');
  try { const r = await api('/api/chat', { text, mode: 'auto', voice: false }); w.remove(); add(r.reply || '(no answer)', 'bot'); (r.pending || []).forEach(confirmBox); }
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

// ---- slash commands ----
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

// ---- one-shot voice: tap mic, speak, tap again ----
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


// ---- glitchy pixel-octopus overlay: shows while listening, thinking and speaking (same look as on the PC) ----
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

// ---- conversation mode: listen -> think -> speak -> listen ----
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
  try { wake = await navigator.wakeLock.request('screen'); } catch (e) { /* optional */ }
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
  if (!text || !talking) return;                                   // Stop was pressed while the answer was being worked out: stay quiet setTalk('🔊 Speaking… (tap to interrupt)', 'speak'); skipSpeech = false;
  const parts = clean(text).match(/[^.!?]+[.!?]*/g) || [clean(text)];
  const fetches = parts.map((p) => fetch('/api/speak', jopt('POST', { text: p })).then((r) => r.blob()));
  for (const f of fetches) { if (skipSpeech || !talking) break; const url = URL.createObjectURL(await f);
    await new Promise((res) => { player.onended = res; player.src = url; player.play().catch(res); }); URL.revokeObjectURL(url); }
}
talkBtn.addEventListener('click', toggleTalk);

// =====================================================================
// start
// =====================================================================
async function refreshBadges() {
  try {
    const b = await J('/api/badges'); const changed = b.overdue !== overdueCount || b.inbox !== unreadCount;
    overdueCount = b.overdue; unreadCount = b.inbox;
    if (changed) { renderNav((location.hash.replace(/^#\/?/, '') || 'home').split('/')[0].replace('chat', '')); if (location.hash.startsWith('#/inbox') && !dirty) keepScroll(() => VIEWS.inbox()); }
  } catch (e) { $('#dot').className = 'off'; }
}
setInterval(() => { if (!document.hidden) refreshBadges(); }, 45000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshBadges(); });
(async function init() {
  $('#menu').onclick = () => document.body.classList.toggle('side-open');
  $('#scrim').onclick = () => document.body.classList.remove('side-open');
  $('#newchat').onclick = () => newChat();
  renderNav('home');
  await refreshSide();           // first request: asks for the access code once if needed
  if (!location.hash || location.hash === '#') { history.replaceState(null, '', '#/home'); }
  prevHash = location.hash; route();
})();


// ---------- Shut down all (sidebar footer): stops Agent Zero, the memory graph, push-to-talk, the overlay, the models, then Rosie ----------
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

// ---------- the Agent Zero page: is it running, what is it doing now, which of Rosie's tools it used, its recent activity ----------
let a0Timer = null;
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
  const draw = async () => {
    if (!a0Here()) { clearInterval(a0Timer); return; }
    let d;
    try { d = await J('/api/a0/status'); } catch (e) { show(h('div', { class: 'err' }, 'Could not read Agent Zero\'s status: ' + e.message)); return; }
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
        h('a', { class: 'btn sm pur', href: d.url, target: '_blank', rel: 'noopener', style: 'text-decoration:none;display:inline-flex;align-items:center' }, 'Open Agent Zero'),
        up ? btn('Restart', 'sm', () => act('restart', 'Restarting Agent Zero', 'Restart Agent Zero? Anything it is working on stops.')) : null,
        up ? btn('Stop', 'sm bad', () => act('stop', 'Stopping Agent Zero', 'Stop Agent Zero? Rosie answers chat herself until it runs again.')) : btn('Start', 'sm ok', () => act('start', 'Starting Agent Zero'))),
      h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'Open Agent Zero works on this PC (it only listens here). A turn stops by itself after 50 steps.'));
    const t = d.turn;
    const now = h('div', { class: 'card' }, h('h3', {}, 'Working now'),
      t ? h('div', {},
        h('div', {}, h('b', {}, '“' + t.text + '”')),
        h('div', { class: 'muted', style: 'margin:4px 0 8px' }, `${a0Ago(t.seconds)} so far · ${t.calls.length} Rosie tool${t.calls.length === 1 ? '' : 's'} used` + (t.waiting ? ` · ${t.waiting} waiting for your Yes` : '')),
        t.calls.length ? h('div', { class: 'list' }, t.calls.slice(-8).reverse().map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' }, x.tool)))) : null,
        h('div', { class: 'form', style: 'margin-top:10px' }, btn('Stop this task', 'sm bad', () => act('restart', 'Stopping the task', 'Stop what Agent Zero is doing? This restarts it (about 20 seconds); the chat keeps what was said so far.'))))
        : h('div', { class: 'muted' }, up ? 'Nothing right now.' : 'Agent Zero is not running.'));
    const calls = h('div', { class: 'card' }, h('h3', {}, 'Rosie\'s tools it used'),
      d.calls.length ? h('div', { class: 'list' }, d.calls.map((x) => h('div', { class: 'item' }, h('span', { class: 'tx' }, h('b', {}, x.tool + '  '), h('span', { class: 'muted', style: 'font-size:12px' }, x.at + (x.args ? ' · ' + x.args : ''))))))
        : h('div', { class: 'muted' }, 'None yet.'));
    const log = h('div', { class: 'card wide' }, h('h3', {}, 'Recent activity'),
      d.activity.length ? h('pre', { class: 'a0-log' }, d.activity.join('\n')) : h('div', { class: 'muted' }, up ? 'Nothing logged yet.' : '–'));
    const y = $('#view').scrollTop;
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Agent Zero'), h('span', { class: 'muted' }, 'Rosie\'s brain for chat, voice and the phone app. Updates every few seconds.')),
      h('div', { class: 'grid' }, status, now, calls, log));
    $('#view').scrollTop = y;
  };
  await draw();
  a0Timer = setInterval(draw, 4000);
};
