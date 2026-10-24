'use strict';
/* Rosie's corner (owner 2026-10-14). The fixed renderer for the part Rosie arranges herself, plus the owner's view of everything she did.
   Every word from the server (page titles, her notes, her layout) is put on the page as text (h() text children = textContent).
   Nothing here ever uses innerHTML or the h() 'html' option, links are only http(s) and always show where they go, and pictures only
   come from her own checked gallery (/api/corner/img/<32 hex>.png|jpg). Colours are fixed class names from the app's palette. */

const CN_COLORS = { cyan: 'cn-c-cyan', purple: 'cn-c-purple', green: 'cn-c-green', lilac: 'cn-c-lilac', red: 'cn-c-red', grey: 'cn-c-grey', white: 'cn-c-white', plain: 'cn-c-plain' };
const CN_TABS = [['live', 'Live'], ['space', 'Her space'], ['finds', 'Finds'], ['pictures', 'Pictures'], ['notes', 'Her notes'], ['trail', 'Trail'], ['changes', 'What Rosie changed'], ['settings', 'Settings']];
const cnColor = (c) => CN_COLORS[c] || CN_COLORS.cyan;
const cnThumb = (s) => (/^\/api\/corner\/live\/thumb\/[0-9a-f]{32}\.jpg$/.test(String(s)) ? String(s) : null);
const cnImg = (s) => (/^\/api\/corner\/img\/[0-9a-f]{32}\.(png|jpg)$/.test(String(s)) ? String(s) : null);
function cnHref(u) {
  try { const x = new URL(String(u)); return (x.protocol === 'https:' || x.protocol === 'http:') && !x.username && !x.password && x.hostname ? x.href : null; } catch (e) { return null; }
}
const cnHost = (href) => new URL(href).hostname.replace(/^www\./, '');
function cnLink(text, url) {
  const href = cnHref(url);
  if (!href) return h('span', {}, String(text || ''));
  return h('span', { class: 'cn-link' }, h('a', { href, target: '_blank', rel: 'noopener noreferrer nofollow', referrerpolicy: 'no-referrer', title: href }, String(text || href)),
    h('span', { class: 'cn-dest' }, ' → ' + cnHost(href)));
}
const cnWhen = (iso) => { const s = String(iso || ''); return s.slice(0, 10) + ' ' + s.slice(11, 16); };
function cnInline(parts) {
  return (parts || []).map((x) => {
    const v = String(x.v == null ? '' : x.v);
    if (x.t === 'b') return h('b', {}, v);
    if (x.t === 'i') return h('i', {}, v);
    if (x.t === 'code') return h('code', {}, v);
    if (x.t === 'link') return cnLink(v, x.href);
    if (x.t === 'img') { const src = cnImg(x.src); return src ? h('img', { class: 'cn-inline-img', src, alt: String(x.alt || ''), loading: 'lazy' }) : null; }
    return v;
  });
}
function cnBlocks(blocks) {
  return (blocks || []).map((b) => {
    if (b.t === 'h') return h(b.level === 2 ? 'h4' : 'h5', { class: 'cn-h' }, cnInline(b.c));
    if (b.t === 'p') return h('p', {}, cnInline(b.c));
    if (b.t === 'ul' || b.t === 'ol') return h(b.t, {}, (b.items || []).map((it) => h('li', {}, cnInline(it))));
    if (b.t === 'quote') return h('blockquote', {}, cnInline(b.c));
    if (b.t === 'code') return h('pre', { class: 'cn-code' }, String(b.v || ''));
    if (b.t === 'hr') return h('hr', {});
    return null;
  });
}
const cnHers = (label, text) => h('div', { class: 'cn-hers' }, h('span', { class: 'cn-tag' }, label || "Rosie's thoughts"), ' ', String(text || ''));
function cnTabs(on) {
  return h('div', { class: 'seg rs-tabs cn-tabs', role: 'tablist' }, CN_TABS.map(([k, l]) =>
    h('button', { class: k === on ? 'on' : '', role: 'tab', 'aria-selected': k === on ? 'true' : 'false', onclick: () => go('#/corner/' + k) }, l)));
}

// ----- Live: what she is looking at right now (polled every 2 s while this tab is open and the browser tab is visible)
function cnLiveBody(d) {
  const L = d.live || {}, p = L.page;
  const sec = (title, ...kids) => h('div', { class: 'card wide cn-live-sec' }, h('h3', {}, title), ...kids);
  return h('div', { class: 'cn-live' },
    h('div', { class: 'card wide cn-now' }, h('div', { class: 'muted cn-meta' }, 'Now'), h('div', { class: 'cn-now-line' }, String(L.now || 'Stopped')),
      d.nonstop ? h('div', { class: 'muted cn-meta' }, `Running nonstop since ${d.nonstop_since || '—'}. ${d.pages_today} page${d.pages_today === 1 ? '' : 's'} today.`) : null),
    sec('The page she is reading', p ? [h('div', { class: 'cn-ft' }, cnLink(p.title, p.url)), h('div', { class: 'cn-live-text' }, String(p.text || '')),
      h('div', { class: 'muted cn-meta' }, 'The start of the page text she was given.')] : h('div', { class: 'empty' }, 'No page right now.')),
    sec('The links she is choosing from', (L.links || []).length ? h('ol', { class: 'cn-live-links' }, L.links.map((l) =>
      h('li', { class: l.n === L.chosen ? 'cn-chosen' : '' }, cnLink(l.text, l.url), l.n === L.chosen ? h('span', { class: 'chip ok' }, 'her pick') : null))) : h('div', { class: 'empty' }, 'No links to choose from right now.')),
    sec('Pictures she is considering', (L.pictures || []).length ? h('div', { class: 'cn-gallery' }, L.pictures.map((x) => {
      const src = cnThumb(x.src);
      return h('figure', { class: 'cn-pic' }, src ? h('img', { src, alt: String(x.alt || 'A picture she is considering') }) : h('div', { class: 'chip bad' }, x.status === 'off' ? 'not fetched' : 'blocked'),
        h('figcaption', {}, String(x.alt || ''), h('div', { class: 'muted cn-meta' }, String(x.host || '') + (x.why ? ' · ' + x.why : ''))));
    })) : h('div', { class: 'empty' }, 'None right now.'), h('div', { class: 'muted cn-meta' }, 'Previews are small copies Rosie drew herself. Pictures she keeps are virus-checked first.')),
    sec("Her thoughts", (L.thoughts || []).length ? L.thoughts.map((t) => h('div', {}, h('span', { class: 'muted cn-time' }, String(t.at || '')), cnHers("Rosie's thoughts", t.text))) : h('div', { class: 'empty' }, 'No thoughts yet.')),
    sec('What she did', (L.log || []).length ? h('div', { class: 'list' }, L.log.map((x) => h('div', { class: 'item' }, h('span', { class: 'muted cn-time' }, String(x.at || '')), h('span', { class: 'tx' }, String(x.text || ''))))) : h('div', { class: 'empty' }, 'Nothing yet.')));
}
let cnLiveT = null, cnLiveWaiting = null;
function cnLivePoll(box) {
  clearTimeout(cnLiveT);
  const tick = async () => {
    if (!box.isConnected || !/^#\/corner(\/live)?$/.test(location.hash)) return;     // left the Live tab: polling ends
    if (document.hidden) { cnLiveWaiting = box; return; }                          // hidden browser tab: wait for it to show again
    try { box.replaceChildren(cnLiveBody(await J('/api/corner/live'))); } catch (e) { /* offline: try again */ }
    cnLiveT = setTimeout(tick, 2000);
  };
  tick();
}
document.addEventListener('visibilitychange', () => { if (!document.hidden && cnLiveWaiting) { const b = cnLiveWaiting; cnLiveWaiting = null; cnLivePoll(b); } });

// ----- Finds in topic groups: a row of tabs (All, New since you looked, one per group, Other), easy-to-read cards
function cnCard(c, targets, again) {
  const act = (kind, msg) => async () => { try { await post('/api/corner/find/' + c.id + '/' + kind); toast(msg); again(); } catch (e) { toast(e.message, 1); } };
  const moveBtn = btn('Move to…', 'sm dim', (e) => popMenu(e.currentTarget, targets.filter((t) => t.id !== c.group).map((t) => ({ label: String(t.title),
    fn: async () => { try { await post('/api/corner/move/' + c.id, { group: t.id }); toast('Moved to ' + t.title); again(); } catch (er) { toast(er.message, 1); } } }))));
  return h('div', { class: 'cn-find cn-card' },
    h('div', { class: 'cn-ft cn-one' }, cnLink(c.title, c.url)),
    h('div', { class: 'muted cn-meta' }, (c.host || '') + ' · ' + cnWhen(c.at).slice(0, 10) + (c.more ? ' · ★ more like this' : '')),
    (c.summary || []).length ? h('div', { class: 'cn-sum', title: 'Copied from the page itself' }, h('span', { class: 'cn-tag dim' }, 'From the page'), ' ', c.summary.slice(0, 2).join(' ')) : null,
    (c.also || []).length ? h('div', { class: 'muted cn-meta cn-also' }, 'Also on ', c.also.map((x, i) => [i ? ', ' : '', cnLink(x.host || x.title, x.url)])) : null,
    c.why ? h('div', { class: 'cn-hers cn-one' }, h('span', { class: 'cn-tag' }, "Rosie's thoughts"), ' ', String(c.why)) : null,
    h('div', { class: 'form cn-btns' }, btn('More like this', 'sm' + (c.more ? ' ok' : ''), act('more', 'Rosie will look for more like this')),
      btn('Not interested', 'sm dim', act('not', 'Hidden. Rosie will look for less like this')),
      c.paper ? h('span', { class: 'chip ok' }, c.paper_used ? 'Was in your paper' : 'In your paper list') : btn('Add to my paper', 'sm pur', act('paper', 'Added to Rosie recommends in your paper')),
      moveBtn));
}
function cnGroups(g, sub, again) {
  const tabs = [['all', 'All', g.all.length], ['new', 'New since you looked', g.new.length]].concat(g.groups.map((x) => [x.id, x.title, x.count]));
  const on = tabs.some(([k]) => k === sub) ? sub : 'all';
  const row = h('div', { class: 'seg rs-tabs cn-subtabs', role: 'tablist' }, tabs.map(([k, l, n]) =>
    h('button', { class: k === on ? 'on' : '', role: 'tab', 'aria-selected': k === on ? 'true' : 'false', onclick: () => go('#/corner/finds/' + k) }, String(l) + ' (' + n + ')')));
  let head = null, list;
  if (on === 'all') list = g.all;
  else if (on === 'new') { list = g.new; head = h('div', { class: 'muted cn-meta' }, g.new_since ? 'Found since ' + cnWhen(g.new_since) : 'Everything is new the first time you look.'); }
  else {
    const grp = g.groups.find((x) => x.id === on); list = grp.cards;
    const hd = grp.header || {};
    head = h('div', { class: 'cn-ghead' },
      hd.quote ? h('blockquote', { class: 'cn-quote' }, h('span', { class: 'cn-tag dim' }, 'From the page'), ' ', String(hd.quote.sentence), ' ', cnLink(hd.quote.host, hd.quote.url)) : null,
      hd.thought ? cnHers("Rosie's thoughts", hd.thought) : null,
      h('div', { class: 'muted cn-meta' }, `${grp.count} find${grp.count === 1 ? '' : 's'}` + (grp.last ? ' · last added ' + cnWhen(grp.last).slice(0, 10) : '')));
  }
  return [row, head, list.length ? list.map((c) => cnCard(c, g.targets, again)) : h('div', { class: 'empty' }, 'Nothing here yet.')];
}

// ----- one find, the same everywhere
function cnFind(f, again, compact) {
  const buttons = compact ? null : h('div', { class: 'form cn-btns' },
    btn('Show me more like this', 'sm' + (f.more ? ' ok' : ''), async () => { try { await post('/api/corner/find/' + f.id + '/more'); toast('Rosie will look for more like this'); again(); } catch (e) { toast(e.message, 1); } }),
    btn('Not interested', 'sm dim', async () => { try { await post('/api/corner/find/' + f.id + '/not'); toast('Hidden. Rosie will look for less like this'); again(); } catch (e) { toast(e.message, 1); } }),
    f.paper ? h('span', { class: 'chip ok' }, f.paper_used ? 'Was in your paper' : 'In your paper list') :
      btn('Add to my paper', 'sm pur', async () => { try { await post('/api/corner/find/' + f.id + '/paper'); toast('Added to Rosie recommends in your paper'); again(); } catch (e) { toast(e.message, 1); } }));
  return h('div', { class: 'cn-find' },
    h('div', { class: 'cn-ft' }, cnLink(f.title, f.url)),
    h('div', { class: 'muted cn-meta' }, (f.host || f.source || '') + ' · found ' + cnWhen(f.at)),
    (f.summary || []).length ? h('blockquote', { class: 'cn-quote', title: 'Sentences copied from the page itself' }, h('span', { class: 'cn-tag dim' }, 'From the page'), ' ', f.summary.join(' ')) : null,
    f.why ? cnHers("Rosie's thoughts", f.why) : null, buttons);
}
function cnPic(p, again, compact) {
  const src = cnImg(p.src);
  if (!src) return null;
  return h('figure', { class: 'cn-pic' }, h('img', { src, alt: String(p.look || p.page_title || 'A picture Rosie kept'), loading: 'lazy' }),
    h('figcaption', {}, p.page_url ? cnLink(p.page_title || 'Source page', p.page_url) : null, h('div', { class: 'muted cn-meta' }, 'kept ' + cnWhen(p.at)),
      p.look ? cnHers("Rosie's look", p.look) : null,
      compact ? null : h('div', { class: 'form cn-btns' },
        btn(p.starred ? '★ Starred' : '☆ Star', 'sm' + (p.starred ? ' ok' : ''), async () => { try { await post('/api/corner/picture/' + p.id + '/star', { on: !p.starred }); again(); } catch (e) { toast(e.message, 1); } }),
        btn('Remove', 'sm bad', async () => { if (!confirm('Remove this picture?')) return; try { await del('/api/corner/picture/' + p.id); again(); } catch (e) { toast(e.message, 1); } }))));
}

// ----- Her space: the part Rosie arranges, drawn by this fixed renderer
function cnSpace(L, again) {
  const sec = (s) => {
    let body;
    if (s.kind === 'finds') body = s.finds.length ? s.finds.map((f) => cnFind(f, again, true)) : h('div', { class: 'empty' }, 'Nothing here yet.');
    else if (s.kind === 'pictures') body = s.pictures.length ? h('div', { class: 'cn-gallery' }, s.pictures.map((p) => cnPic(p, again, true))) : h('div', { class: 'empty' }, 'No pictures yet.');
    else if (s.kind === 'notes') body = s.notes.length ? s.notes.map((n) => cnHers("Rosie's thoughts", n.text)) : h('div', { class: 'empty' }, 'No notes yet.');
    else if (s.kind === 'links') body = s.links.length ? h('ul', {}, s.links.map((l) => h('li', {}, cnLink(l.label, l.url)))) : h('div', { class: 'empty' }, 'No links yet.');
    else body = cnBlocks(s.blocks);
    return h('div', { class: 'card cn-sec ' + cnColor(s.color) }, h('h3', {}, String(s.title)), s.auto ? h('div', { class: 'muted cn-meta' }, 'The newest ones') : null, body);
  };
  return h('div', { class: 'cn-space ' + cnColor(L.accent) },
    h('div', { class: 'muted cn-note' }, 'Rosie arranges this part herself. She can only write text and pick from her own finds and checked pictures; Rosie\'s app draws it, so it can\'t run code or change anything else.'),
    h('div', { class: 'card wide cn-about ' + cnColor(L.accent) }, h('h3', {}, String(L.about.title || 'About me')), h('div', { class: 'cn-by muted' }, 'Written by Rosie'), cnBlocks(L.about.blocks)),
    h('div', { class: 'grid' }, L.sections.map(sec)),
    L.pages.length ? h('div', { class: 'card wide' }, h('h3', {}, 'Her pages'), L.pages.map((p) => h('details', { class: 'cn-page' }, h('summary', {}, String(p.title)), h('div', { class: 'cn-by muted' }, 'Written by Rosie'), cnBlocks(p.blocks)))) : null);
}

function cnTrail(rows) {
  const chip = (r) => { const bad = ['blocked', 'robots.txt', 'failed', 'unreadable', 'refused', 'stopped', 'skipped', 'slow site'].includes(r.status); return h('span', { class: 'chip' + (bad ? ' bad' : r.status === 'read' || r.status === 'saved' ? ' ok' : '') }, r.status || r.kind); };
  const KIND = { page: 'Page', search: 'Search', robots: 'robots.txt', blocked: 'Not opened', picture: 'Picture', look: 'Looked', layout: 'Corner', session: 'Session', error: 'Problem' };
  return rows.length ? h('div', { class: 'list' }, rows.map((r) => h('div', { class: 'item cn-trail' },
    h('span', { class: 'muted cn-time' }, cnWhen(r.at)), h('span', { class: 'cn-kind' }, KIND[r.kind] || r.kind), chip(r),
    h('span', { class: 'tx' }, r.kind === 'search' ? '“' + r.title + '”' : (r.url ? cnLink(r.title || r.url, r.url) : String(r.title || '')), r.url && r.title ? h('div', { class: 'muted cn-url' }, r.url) : null,
      r.detail ? h('div', { class: 'muted cn-meta' }, r.detail) : null)))) : h('div', { class: 'empty' }, 'Rosie hasn\'t opened anything yet.');
}

function cnSettings(st, again) {
  const s = st.settings;
  const num = (k, label, hint, min, max) => { const i = h('input', { type: 'number', min, max, value: s[k], 'aria-label': label, style: 'flex:none;width:110px' }); i.dataset.k = k; return [h('dt', {}, label), h('dd', {}, h('div', { class: 'form' }, i), hint ? h('div', { class: 'muted cn-meta' }, hint) : null)]; };
  const qf = h('input', { type: 'time', value: s.corner_quiet_from, 'aria-label': 'Quiet hours start', style: 'flex:none;width:140px' });
  const qt = h('input', { type: 'time', value: s.corner_quiet_to, 'aria-label': 'Quiet hours end', style: 'flex:none;width:140px' });
  const interests = h('input', { value: (s.corner_interests || []).join(', '), 'aria-label': 'Interests' });
  const block = h('textarea', { rows: 4, 'aria-label': 'Sites to never open', placeholder: 'one site per line, like example.com' });
  block.value = (s.corner_block_domains || []).join('\n');
  let pics = !!s.corner_pictures, nsq = !!s.corner_nonstop_quiet, bat = !!s.corner_on_battery;
  const pc = st.pc || {}, pcNow = [pc.mem_free_gb != null ? pc.mem_free_gb + ' GB memory free' : '', pc.cpu_temp != null ? 'CPU ' + Math.round(pc.cpu_temp) + ' °C' : '',
    pc.on_battery == null ? '' : pc.on_battery ? 'on battery' : 'plugged in', pc.idle_s == null ? '' : pc.at_pc ? 'you are using the PC' : 'you are away from the PC'].filter(Boolean).join(' · ');
  const form = h('dl', { class: 'kv cn-set' },
    h('dt', {}, 'Also browse when idle'), h('dd', {}, onOff(st.on, async (v) => { try { await post('/api/corner/settings', { corner_on: v }); toast(v ? 'Rosie may browse when you are away' : 'Idle-time browsing is off'); again(); } catch (e) { toast(e.message, 1); } }),
      h('div', { class: 'muted cn-meta' }, 'When nonstop is stopped, Rosie may still read on her own after you have been away a while, within the limits here.')),
    h('dt', {}, 'Quiet hours'), h('dd', {}, h('div', { class: 'form' }, qf, 'to', qt), h('div', { class: 'muted cn-meta' }, 'No idle-time browsing at these times.' + (/\d/.test(st.paper_window || '') ? ' The morning paper always pauses her (' + st.paper_window + ').' : ''))),
    h('dt', {}, 'Quiet hours when nonstop'), h('dd', {}, onOff(nsq, (v) => { nsq = v; }), h('div', { class: 'muted cn-meta' }, 'Off: nonstop browsing only pauses for the morning paper and while you use Rosie.')),
    num('corner_idle_min', 'Wait after I stop', 'Minutes with no chat or request from you before Rosie starts.', 1, 240),
    num('corner_rest_min', 'Rest between sessions', 'Minutes.', 1, 720),
    num('corner_pages_session', 'Pages per session', '', 1, 100),
    num('corner_pages_hour', 'Pages per hour', '', 1, 1000),
    num('corner_pages_day', 'Pages per day', '', 1, 10000),
    num('corner_github_pct', 'GitHub share (%)', 'How many of her searches go to GitHub. She reads each repository from its README.', 0, 100),
    num('corner_graph_wait', 'Let her memory catch up after', 'Pieces (finds, pictures, notes from the last day) waiting for her memory graph before she pauses reading so it can catch up; she goes on when nearly all are in. 0 = never pause.', 0, 5000),
    h('dt', {}, 'Your PC now'), h('dd', {}, pcNow || 'Rosie can\'t read this PC\'s health.'),
    num('corner_pc_free_gb', 'Keep memory free for me (GB)', 'While you use the PC, she pauses and lets go of her AI models when less than this is free, and loads them again only when there is room. With you away she still keeps 1 GB free. 0 = off.', 0, 64),
    num('corner_pc_away_min', 'Away from the PC after', 'Minutes with no key press or mouse move. While you are at the PC she also skips picture looks (a second AI model).', 1, 120),
    num('corner_max_temp', 'Pause when the CPU reaches (°C)', 'She goes on when it is 10 °C cooler. 0 = never pause for heat.', 0, 105),
    h('dt', {}, 'Read on battery'), h('dd', {}, onOff(bat, (v) => { bat = v; }), h('div', { class: 'muted cn-meta' }, 'Off: on a laptop she waits until it is plugged in.')),
    num('corner_site_gap_s', 'Seconds between pages of one site', 'A site\'s robots.txt can ask for longer.', 5, 600),
    h('dt', {}, 'Pictures'), h('dd', {}, onOff(pics, (v) => { pics = v; }), h('div', { class: 'muted cn-meta' }, st.pictures_why)),
    num('corner_images_day', 'Pictures per day', '', 0, 100),
    num('corner_looks_day', 'Picture looks per day', 'Looking at a picture loads a second AI model, which pushes the chat model off the graphics card for a moment.', 0, 50),
    num('corner_storage_mb', 'Picture storage (MB)', 'Oldest pictures go first when it is full. Starred ones stay. She also stops saving pictures when the disk has less than 5 GB free.', 50, 10240),
    num('corner_changes_day', 'Corner changes per day', 'How many changes Rosie may make to her own space each day.', 0, 100),
    h('dt', {}, 'Interests'), h('dd', {}, h('div', { class: 'form' }, interests), h('div', { class: 'muted cn-meta' }, 'Comma separated. Rosie also follows her own curiosity.')),
    h('dt', {}, 'Never open'), h('dd', {}, block, h('div', { class: 'muted cn-meta' }, 'Added to the built-in block list (adult, gambling, piracy and malware sites, and sites that need a login).')));
  const save = async () => {
    const body = { corner_quiet_from: qf.value, corner_quiet_to: qt.value, corner_pictures: pics, corner_nonstop_quiet: nsq, corner_on_battery: bat,
      corner_interests: interests.value.split(',').map((x) => x.trim()).filter(Boolean), corner_block_domains: block.value.split(/\s+/).map((x) => x.trim()).filter(Boolean) };
    form.querySelectorAll('input[type=number]').forEach((i) => { body[i.dataset.k] = +i.value; });
    try { await post('/api/corner/settings', body); toast('Saved'); again(); } catch (e) { toast(e.message, 1); }
  };
  return h('div', { class: 'card wide' }, h('h3', {}, 'Settings'), form, h('div', { class: 'form', style: 'margin-top:12px' }, btn('Save settings', 'pur', save)));
}

VIEWS.corner = async (arg) => {
  setHeader("Rosie's corner", []);
  show(h('div', { class: 'muted' }, 'Loading…'));
  const d = await J('/api/corner');
  const st = d.status;
  const [a0, sub] = String(arg || '').split('/');
  const tab = CN_TABS.some(([k]) => k === a0) ? a0 : (st.running || st.nonstop ? 'live' : 'space');   // while she runs: Live first
  const again = () => keepScroll(() => VIEWS.corner(tab + (sub ? '/' + sub : '')));
  const line = st.nonstop ? `Running nonstop since ${st.nonstop_since || '—'}. ${st.pages_today} page${st.pages_today === 1 ? '' : 's'} today.`
    : st.running ? 'Rosie is browsing: ' + (st.doing || 'starting') : !st.on ? 'Browsing is off.' : 'Rosie is not browsing right now. ' + (st.waiting || 'She starts when you have been away for a while.');
  const nonstopBtn = st.nonstop
    ? btn('Stop', 'bad cn-big', async () => { try { await post('/api/corner/nonstop', { on: false }); toast('Stopped'); again(); } catch (e) { toast(e.message, 1); } })
    : btn('Start browsing', 'ok cn-big', async () => { try { await post('/api/corner/nonstop', { on: true }); toast('Rosie is browsing until you press Stop'); again(); } catch (e) { toast(e.message, 1); } });
  const head = h('div', { class: 'card wide cn-head' },
    h('h3', {}, 'Browse nonstop'),
    h('div', { class: 'form' }, nonstopBtn),
    h('div', { class: 'muted cn-meta' }, 'Start: Rosie reads until you press Stop or reads her pages per day, with no other page, picture or change limits. She pauses while you use Rosie' + (st.paper_window ? ' and while the morning paper is built (' + st.paper_window + ')' : '') + ', and still keeps every safety rule.'),
    st.nonstop && st.paused ? h('div', { class: 'cn-status' }, 'Paused: ' + st.paused) : null,
    st.disk_low ? h('div', { class: 'cn-pics-state off' }, `Pictures paused: less than 5 GB free on the disk (${st.disk_free_gb} GB).`) : null,
    st.running && !st.nonstop ? h('div', { class: 'form', style: 'margin-top:8px' }, btn('Stop now', 'sm bad', async () => { await post('/api/corner/stop'); toast('Stopped'); again(); })) : null,
    h('div', { class: 'cn-status' }, line), st.last_stop && !st.running ? h('div', { class: 'muted cn-meta' }, 'Last stop: ' + st.last_stop) : null,
    h('div', { class: 'muted cn-meta' }, st.nonstop ? `Pages today: ${st.pages_today} of ${st.settings.corner_pages_day} · this hour: ${st.pages_hour} · pictures today: ${st.images_today}`
      : `Pages today: ${st.pages_today} of ${st.settings.corner_pages_day} · this hour: ${st.pages_hour} of ${st.settings.corner_pages_hour} · pictures today: ${st.images_today}`),
    h('div', { class: 'cn-pics-state ' + (st.pictures_on ? 'ok' : 'off') }, st.pictures_why),
    st.graph ? h('div', { class: 'cn-pics-state ' + (st.graph.on ? 'ok' : 'off') }, st.graph.line) : null,
    h('div', { class: 'muted cn-meta' }, 'She only reads public web pages and searches, on the AI model on this PC. She has no access to your mail, calendar, tasks, files, phone or anything else, and she stops as soon as you use Rosie. You can see every page she opened in the Trail.'));
  let body;
  if (tab === 'live') { body = h('div', { id: 'cn-live' }, h('div', { class: 'muted' }, 'Loading…')); setTimeout(() => cnLivePoll(body), 0); }
  else if (tab === 'space') body = cnSpace(d.layout, again);
  else if (tab === 'finds') {
    const g = await J('/api/corner/groups?look=true');
    body = h('div', { class: 'card wide' }, h('h3', {}, 'Finds', h('a', { href: '#/corner/finds', onclick: async (e) => { e.preventDefault(); if (confirm('Clear all finds?')) { await post('/api/corner/clear', { what: 'finds' }); again(); } } }, 'CLEAR')),
      g.all.length ? cnGroups(g, sub, again) : h('div', { class: 'empty' }, 'No finds yet. Turn on browsing and Rosie will keep what she finds here.'));
  } else if (tab === 'pictures') {
    body = h('div', { class: 'card wide' }, h('h3', {}, 'Pictures', d.pictures.length ? h('a', { href: '#/corner/pictures', onclick: async (e) => { e.preventDefault(); if (confirm('Remove all pictures?')) { await post('/api/corner/clear', { what: 'pictures' }); again(); } } }, 'CLEAR') : null),
      h('div', { class: 'cn-pics-state ' + (st.pictures_on ? 'ok' : 'off') }, st.pictures_why),
      d.pictures.length ? h('div', { class: 'cn-gallery' }, d.pictures.map((p) => cnPic(p, again))) : h('div', { class: 'empty' }, 'No pictures yet.'));
  } else if (tab === 'notes') {
    body = h('div', { class: 'card wide' }, h('h3', {}, "Rosie's thoughts", d.notes.length ? h('a', { href: '#/corner/notes', onclick: async (e) => { e.preventDefault(); if (confirm('Clear all of her notes?')) { await post('/api/corner/clear', { what: 'notes' }); again(); } } }, 'CLEAR') : null),
      h('div', { class: 'muted cn-note' }, 'Her own notes about what she read. They are her thoughts, not checked facts.'),
      d.notes.length ? d.notes.map((n) => h('div', { class: 'cn-noteitem' }, h('div', { class: 'muted cn-meta' }, cnWhen(n.at)), cnHers("Rosie's thoughts", n.text))) : h('div', { class: 'empty' }, 'No notes yet.'));
  } else if (tab === 'trail') {
    body = h('div', { class: 'card wide' }, h('h3', {}, 'Every page she opened', d.trail.length ? h('a', { href: '#/corner/trail', onclick: async (e) => { e.preventDefault(); if (confirm('Clear the trail?')) { await post('/api/corner/clear', { what: 'trail' }); again(); } } }, 'CLEAR') : null), cnTrail(d.trail));
  } else if (tab === 'changes') {
    body = h('div', { class: 'card wide' }, h('h3', {}, 'What Rosie changed'),
      h('div', { class: 'form', style: 'margin-bottom:10px' }, h('span', { class: 'muted' }, `Changes by Rosie today: ${d.changes_today}` + (st.nonstop ? '' : ` of ${st.settings.corner_changes_day}`)), h('div', { class: 'sp' }),
        btn('Reset to default', 'sm bad', async () => { if (!confirm('Put her space back to the default layout? (You can undo this too.)')) return; await post('/api/corner/layout/reset'); toast('Reset'); again(); })),
      d.history.length ? h('div', { class: 'list' }, d.history.map((c, i) => h('div', { class: 'item' }, h('span', { class: 'muted cn-time' }, cnWhen(c.at)), h('span', { class: 'chip' + (c.by === 'rosie' ? '' : ' ok') }, c.by === 'rosie' ? 'Rosie' : 'You'),
        h('span', { class: 'tx' }, String(c.what)),
        btn(i ? 'Undo this and later' : 'Undo', 'sm', async () => { try { await post('/api/corner/layout/undo/' + c.id); toast('Undone'); again(); } catch (e) { toast(e.message, 1); } })))) : h('div', { class: 'empty' }, 'No changes yet.'));
  } else body = cnSettings(st, again);
  show(h('div', { class: 'pagehead' }, h('h2', {}, "Rosie's corner"), h('span', { class: 'muted' }, 'What Rosie reads and keeps when you are not using her.')), head, cnTabs(tab), body);
};
