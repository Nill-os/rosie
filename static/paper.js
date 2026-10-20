'use strict';
// ----- Morning Paper ("The Rosie Times"): logic lives in app/paper.py. The paper itself is drawn by the server (app/paper_render.py) in a
// frame with no scripts; this page is the controls around it: archive, look, print, email, make now, "Add to my paper" and the settings.
let ppTimer = null, ppFit = () => {};
addEventListener('resize', () => ppFit());
const PP_LOOKS = [['cyberdeck', 'Cyberdeck'], ['newsprint', 'Newsprint'], ['plain', 'Plain']];   // all black and white
const ppNarrow = () => innerWidth < 700;

function ppFrame(day, look) {
  const f = h('iframe', { class: 'pp-frame', title: 'The paper', src: '/api/paper/view?day=' + encodeURIComponent(day) + '&look=' + encodeURIComponent(look),
    sandbox: 'allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation', loading: 'lazy' });
  const fit = () => { try { f.style.height = (f.contentDocument.documentElement.scrollHeight + 8) + 'px'; } catch (e) { /* not loaded yet */ } };
  f.onload = () => { fit(); setTimeout(fit, 600); };
  ppFit = fit;
  return f;
}

function ppPoll(day) {
  clearTimeout(ppTimer);
  ppTimer = setTimeout(async () => {
    if (!location.hash.startsWith('#/paper')) return;                   // you left the page: stop asking
    let st; try { st = await J('/api/paper/status'); } catch (e) { return; }
    if (st.running) { const s = document.getElementById('pp-step'); if (s) s.textContent = 'Making the paper: ' + (st.step || 'starting') + '…'; ppPoll(day); return; }
    if (st.error) toast('The paper failed: ' + st.error, 1); else toast('Your paper is ready');
    keepScroll(() => VIEWS.paper(day));
  }, 3000);
}

function ppSettings(d, again) {
  const s = d.settings;
  const save = async (patch, msg) => { try { await post('/api/paper/settings', patch); toast(msg || 'Saved'); again(); } catch (e) { toast(e.message, 1); } };
  const title = h('input', { value: s.title, 'aria-label': 'Paper name', maxlength: 40 }), place = h('input', { value: s.place, 'aria-label': 'Weather place', maxlength: 60 });
  const extra = h('input', { value: (s.email_to || []).join(', '), placeholder: 'you@example.com', 'aria-label': 'Extra addresses' });
  const row = (label, ctl, hint) => h('div', { class: 'item pp-set' }, h('div', { class: 'tx' }, h('b', {}, label), hint ? h('div', { class: 'muted', style: 'font-size:12px' }, hint) : null), ctl);
  const seg = (opts, cur, fn) => h('div', { class: 'seg' }, opts.map(([v, l]) => h('button', { class: cur === v ? 'on' : '', onclick: () => fn(v) }, l)));
  const sections = h('div', { class: 'form' }, d.sections.map((x) => h('label', { class: 'chip', style: 'display:inline-flex;gap:6px;align-items:center' },
    h('input', { type: 'checkbox', checked: !!s.sections[x.key], onchange: (e) => save({ sections: { [x.key]: e.target.checked } }) }), x.label)));
  const feedRows = Object.entries(s.feeds).map(([sec, list]) => {
    const label = (d.sections.find((x) => x.key === sec) || {}).label || sec;
    const nm = h('input', { placeholder: 'Name', 'aria-label': 'Feed name', style: 'max-width:160px' }), url = h('input', { placeholder: 'https://… (RSS or Atom)', 'aria-label': 'Feed address' });
    return h('div', { class: 'pp-feeds' }, h('div', { class: 'muted', style: 'margin:8px 0 4px' }, label),
      list.map((f, i) => h('div', { class: 'item' }, h('span', { class: 'tx' }, h('b', {}, f.name + '  '), h('span', { class: 'muted', style: 'font-size:12px;word-break:break-all' }, f.url)),
        h('button', { class: 'x', 'aria-label': 'Remove ' + f.name, onclick: () => save({ feeds: { [sec]: list.filter((_, j) => j !== i) } }, 'Removed') }, '✕'))),
      h('form', { class: 'form', style: 'margin-top:4px', onsubmit: (e) => { e.preventDefault(); if (!url.value.trim()) return; save({ feeds: { [sec]: [...list, { name: nm.value.trim() || '', url: url.value.trim() }] } }, 'Added'); } },
        nm, url, btn('Add', 'sm', null, 'submit')));
  });
  const subRows = Object.entries(s.subreddits).map(([sec, list]) => {
    const inp = h('input', { value: list.join(', '), 'aria-label': 'Subreddits for ' + sec });
    return h('form', { class: 'form', style: 'margin-top:6px', onsubmit: (e) => { e.preventDefault(); save({ subreddits: { [sec]: inp.value.split(/[\s,]+/).filter(Boolean) } }); } },
      h('span', { class: 'muted', style: 'min-width:120px' }, (d.sections.find((x) => x.key === sec) || {}).label || sec), inp, btn('Save', 'sm', null, 'submit'));
  });
  return h('details', { class: 'card', style: 'margin-top:14px' }, h('summary', { style: 'cursor:pointer;font-weight:700' }, 'Paper settings'),
    h('div', { class: 'list', style: 'margin-top:10px' },
      row('Make it every morning', h('div', { class: 'form' }, onOff(s.on, (v) => save({ on: v })), timeIn(s.time, (v) => save({ time: v }))), 'Built at this time when the PC is awake.'),
      row('Masthead', seg([['mono', '> THE ROSIE TIMES'], ['blackletter', 'Blackletter']], s.masthead, (v) => save({ masthead: v })), 'How the name is set at the top of page one.'),
      row('Name', h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); save({ title: title.value }); } }, title, btn('Save', 'sm', null, 'submit')), 'Shown in the masthead.'),
      row('Weather for', h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); save({ place: place.value }); } }, place, btn('Save', 'sm', null, 'submit'))),
      row('Pages', seg([[4, '4 pages'], [2, '2 pages']], s.pages, (v) => save({ pages: v })), '4 = front, Briefing, sections, fun corner. 2 = front + one packed page. Always one-sided.'),
      row('Print it', h('div', { class: 'form' }, onOff(s.print_on, (v) => save({ print_on: v })), seg([[1, '1 copy'], [2, '2'], [3, '3']], s.copies, (v) => save({ copies: v }))),
        'On ' + d.printer.name + ' each morning, one-sided.' + (d.printer.ready ? '' : ' Right now: ' + d.printer.why)),
      row('Sudoku level', seg([['beginner', 'Beginner'], ['easy', 'Easy'], ['medium', 'Medium']], s.sudoku, (v) => save({ sudoku: v })), 'Beginner: lots of numbers filled in, slowly harder after two weeks, never past easy. Easy or medium: that level every day.'),
      row('Cipher level', seg([['beginner', 'Beginner'], ['easy', 'Easy'], ['medium', 'Medium'], ['medium-hard', 'Medium-hard'], ['hard', 'Hard']], s.cipher, (v) => save({ cipher: v })), 'How much help the daily cipher gives. It gets a little harder every 4 weeks after the first two, never past hard.'),
      row('Print in full dark theme', onOff(s.print_dark, (v) => save({ print_dark: v })), 'A black page with white text. Uses a lot of ink and makes the paper wavy; off is the usual.'),
      row('Printer', h('select', { 'aria-label': 'Printer', onchange: (e) => save({ printer: e.target.value }) },
        [...new Set([s.printer, ...(d.printer.choices || [])])].map((x) => h('option', { value: x, selected: x === s.printer }, x))),
        'The print queue the paper goes to. If a job hasn\'t printed within a few minutes, Rosie tells you in the Inbox (and never sends it twice).'),
      row('Email it', onOff(s.email_on, (v) => save({ email_on: v })), 'Only to your own address' + (d.recipients.length ? ': ' + d.recipients.join(', ') : ' (connect Gmail on the Mail page)') + '.'),
      row('Also email to', h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); save({ email_to: extra.value.split(/[\s,]+/).filter(Boolean) }); } }, extra, btn('Save', 'sm', null, 'submit')),
        'Your own other addresses only (up to 3). Rosie never emails anyone else.')),
    h('div', { class: 'muted', style: 'margin:12px 0 4px' }, 'Sections'), sections,
    h('div', { class: 'muted', style: 'margin:12px 0 0' }, 'Subreddits (top of the day)'), subRows,
    h('div', { class: 'muted', style: 'margin:12px 0 0' }, 'News feeds'), feedRows);
}

VIEWS.paper = async (arg) => {
  clearTimeout(ppTimer);
  const day = /^\d{4}-\d\d-\d\d$/.test(arg || '') ? arg : '';
  const d = await J('/api/paper' + (day ? '?day=' + day : ''));
  const again = () => keepScroll(() => VIEWS.paper(day));
  const look = { dark: 'cyberdeck' }[store.get('paper_look', '')] || store.get('paper_look', d.settings.look || 'cyberdeck');
  const make = async () => { try { const r = await post('/api/paper/make', {}); toast(r.running ? 'Already making it…' : 'Making today\'s paper (a few minutes)…'); again(); } catch (e) { toast(e.message, 1); } };
  setHeader('Morning Paper', [btn(ppNarrow() ? 'Make now' : 'Make today\'s paper now', 'sm pur', make)]);
  const p = d.paper, st = (p && p.status) || {};
  const doPrint = async () => { toast('Sending to the printer…'); try { const r = await post('/api/paper/print', { day: d.day }); toast(r.note); again(); } catch (e) { toast(e.message, 1); } };
  const doEmail = async () => { if (!confirm('Email this paper to ' + (d.recipients.join(', ') || 'your own address') + '?')) return; try { const r = await post('/api/paper/email', { day: d.day }); toast(r.note); again(); } catch (e) { toast(e.message, 1); } };
  const daySel = h('select', { 'aria-label': 'Day', onchange: (e) => go('#/paper/' + e.target.value) },
    (d.days.includes(d.day) ? d.days : [d.day, ...d.days]).map((x) => h('option', { value: x, selected: x === d.day }, x === d.today ? 'Today (' + x + ')' : x)));
  const lookSeg = h('div', { class: 'seg' }, PP_LOOKS.map(([v, l]) => h('button', { class: v === look ? 'on' : '', onclick: () => { store.set('paper_look', v); again(); } }, l)));
  const lines = [st.print_note && 'Print: ' + st.print_note, st.email_note && 'Email: ' + st.email_note, st.pdf_note && 'Printable copy: ' + st.pdf_note].filter(Boolean);
  const tools = h('div', { class: 'card pp-tools' },
    h('div', { class: 'form' }, daySel, lookSeg,
      p ? btn('Print', 'sm', doPrint) : null, p ? btn('Email it to me', 'sm dim', doEmail) : null,
      p && st.pdf ? h('a', { class: 'btn sm dim', href: '/api/paper/pdf?day=' + d.day, target: '_blank', rel: 'noopener', style: 'display:inline-flex;align-items:center;text-decoration:none' }, 'PDF') : null),
    d.state.running ? h('div', { id: 'pp-step', class: 'muted', style: 'margin-top:8px' }, 'Making the paper: ' + (d.state.step || 'starting') + '…') : null,
    d.state.error ? h('div', { class: 'err', style: 'margin-top:8px' }, 'The last try failed: ' + d.state.error) : null,
    lines.length ? h('div', { class: 'muted', style: 'margin-top:8px;font-size:13px' }, lines.join(' · ')) : null,
    p && p.model ? h('div', { class: 'muted', style: 'margin-top:6px;font-size:12px' }, p.model.down ? 'The local model was off, so summaries are the feeds\' own words.' : 'Summaries: ' + p.model.used + ' calls to ' + p.model.name + ', checked against each item\'s own text.') : null);
  const follow = p && p.follow && p.follow.length ? h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'You could also follow'),
    h('div', { class: 'list' }, p.follow.map((f) => h('div', { class: 'item' }, h('div', { class: 'tx' }, h('b', {}, f.label), h('div', { class: 'muted', style: 'font-size:13px' }, f.why)),
      btn('Add to my paper', 'sm ok', async () => { try { await post('/api/paper/follow', { kind: f.kind, value: f.value, section: f.section || '' }); toast('Added. It shows up in tomorrow\'s paper.'); } catch (e) { toast(e.message, 1); } }))))) : null;
  const notes = p && p.notes && p.notes.length ? h('details', { class: 'card', style: 'margin-top:14px' }, h('summary', { class: 'muted' }, 'What couldn\'t be read today (' + p.notes.length + ')'),
    h('div', { class: 'muted', style: 'font-size:13px;margin-top:6px' }, p.notes.join(' · '))) : null;
  show(h('div', { class: 'pp-page' + (ppNarrow() ? ' pp-narrow' : '') },
    h('div', { class: 'pagehead' }, h('h2', {}, 'Morning Paper'), h('span', { class: 'muted' }, d.settings.title + ', made every morning at ' + d.settings.time + ' from real news, your calendar, your deliveries and Rosie\'s data.')),
    tools,
    p ? h('div', { class: 'pp-wrap look-' + look }, ppFrame(d.day, look))
      : h('div', { class: 'card', style: 'margin-top:14px' }, h('div', { class: 'empty' }, d.day === d.today ? 'No paper yet today. It is made automatically each morning; you can make it now.' : 'No paper for this day.'),
        d.day === d.today && !d.state.running ? btn('Make today\'s paper now', 'pur', make) : null),
    follow, notes, ppSettings(d, again)));
  if (d.state.running) ppPoll(day);
};
