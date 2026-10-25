// Loads the real page shell (static/index.html + app.js) in jsdom with canned API answers and checks the menu layout: the merge of the built-in
// NAV with a saved menu_layout, and the Settings > Menu editor (move up/down/to a group, drag, add and remove a group, reset, the saved payload).
// Usage: node menu_ui.js <static dir>. No network: fetch is a stub that records POSTs. Prints JSON (tests/test_menu_ui.py checks it).
const fs = require('fs');
const vm = require('vm');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require(process.env.ROSIE_JSDOM || 'jsdom')); } catch (e) { console.log(JSON.stringify({ skip: 'jsdom not found' })); process.exit(0); }
const dir = process.argv[2];
const shell = fs.readFileSync(path.join(dir, 'index.html'), 'utf8').replace(/<script[^>]*><\/script>/g, '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dom = new JSDOM(shell, { url: 'http://127.0.0.1:8765/#/settings', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  const out = { errors: [], posts: [] };
  w.addEventListener('error', (e) => out.errors.push(String(e.message || e.error)));
  w.addEventListener('unhandledrejection', (e) => out.errors.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
  w.confirm = () => true;
  w.matchMedia = () => ({ matches: false, addEventListener() {} });
  const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
  let echo = (b) => ({ ok: true, menu_layout: b.menu_layout });
  w.fetch = async (u, o) => {
    const m = ((o && o.method) || 'GET').toUpperCase();
    if (m !== 'GET') { const body = o && typeof o.body === 'string' ? JSON.parse(o.body) : null; out.posts.push({ url: String(u), body }); return reply(200, echo(body)); }
    return reply(404, { error: 'not stubbed' });
  };
  const ctx = dom.getInternalVMContext();
  const T = (s) => new vm.Script(s, { filename: 'test' }).runInContext(ctx);
  const files = ['app.js'];
  for (const f of files) { try { T(fs.readFileSync(path.join(dir, f), 'utf8')); } catch (e) { out.errors.push(f + ': ' + e.message); } }
  await sleep(80);
  const J = (s) => JSON.parse(T('JSON.stringify(' + s + ')'));

  // ---- the merge ----
  const def = J('menuGroups(NAV, [])');
  out.defLabels = def.map((g) => g.label);
  out.defPages = def.flatMap((g) => g.pages);
  out.defFlatSame = J("JSON.stringify(navEff()) === JSON.stringify(NAV)");   // no layout: the built-in menu, untouched
  const without = (k) => def.map((g) => ({ label: g.label, pages: g.pages.filter((x) => x !== k) }));
  const where = (groups, k) => { const g = groups.find((x) => x.pages.includes(k)); return g ? g.label : null; };
  let m = J('menuGroups(NAV, ' + JSON.stringify(without('notes')) + ')');
  out.newPageHome = where(m, 'notes');                                       // missing from the saved layout: back in its own group
  const gone = def.filter((g) => g.label !== 'Work');
  m = J('menuGroups(NAV, ' + JSON.stringify(gone) + ')');
  out.homeGone = [where(m, 'notes'), m[m.length - 1].pages.slice(-1)[0]];   // its group is gone: the last group, Settings stays last
  m = J('menuGroups(NAV, ' + JSON.stringify([{ label: 'A', pages: ['home', 'nope', 'home', 'old-page'] }, { label: '', pages: ['settings'] }]) + ')');
  out.dropped = [m[0].pages.includes('nope'), m[0].pages.includes('old-page'), m[0].pages.filter((x) => x === 'home').length];
  const moved = def.map((g) => ({ label: g.label, pages: g.pages.filter((x) => x !== 'brief') }));
  moved.find((g) => g.label === 'Work').pages.push('brief');                  // a tab group moves as ONE entry
  m = J('menuGroups(NAV, ' + JSON.stringify(moved) + ')');
  out.tabMoved = [where(m, 'brief'), m.some((g) => g.pages.includes('paper'))];
  out.tabActive = T("TAB_GROUP['paper']");                                    // the old route still lights the group entry
  T("menuLayout = " + JSON.stringify([{ label: 'First', pages: ['tasks', 'home'] }, { label: '', pages: ['settings'] }]));
  T("pagesHidden = ['home']; renderNav('tasks')");
  out.navShown = Array.from(w.document.querySelectorAll('#nav > *')).map((e) => e.textContent.trim());
  T("pagesHidden = []; menuLayout = []");

  // ---- the editor ----
  const host = w.document.createElement('div'); w.document.body.append(host);
  const mount = () => { host.replaceChildren(T('menuBox(() => {})')); };
  const rows = (gi) => Array.from(host.querySelectorAll('.mg-card')[gi].querySelectorAll('.mg-row')).map((r) => r.dataset.key);
  const click = (sel) => host.querySelector(sel).click();
  mount();
  out.cards = host.querySelectorAll('.mg-card').length;
  out.firstRows = rows(0);
  out.hasHandles = host.querySelectorAll('.mg-handle').length > 5;
  out.statusAria = host.querySelector('.mg-status').getAttribute('aria-live');
  click('[data-f="p:inbox:up"]');
  out.afterUp = rows(0);
  click('[data-f="p:inbox:down"]');
  click('[data-f="p:inbox:down"]');
  out.afterDown = rows(0);
  const sel = host.querySelector('[data-f="p:mail:to"]'); sel.value = '1'; sel.dispatchEvent(new w.Event('change'));
  out.afterMoveTo = [rows(0), rows(1)];
  click('[data-f="g:1:up"]');
  out.groupOrder = Array.from(host.querySelectorAll('.mg-title')).map((e) => e.value).slice(0, 2);
  click('[data-f="g:0:down"]');
  const rowEl = (k) => host.querySelector('.mg-row[data-key="' + k + '"]');
  rowEl('tasks').dispatchEvent(new w.Event('dragstart', { bubbles: true }));
  rowEl('home').dispatchEvent(new w.Event('dragover', { bubbles: true, cancelable: true }));
  rowEl('home').dispatchEvent(new w.Event('drop', { bubbles: true, cancelable: true }));
  out.afterDrag = Array.from(host.querySelectorAll('.mg-card'))[0].dataset.group && rows(0).concat(['|'], rows(1));
  click('.mg-foot .btn');                                                      // Add group
  out.cardsAfterAdd = host.querySelectorAll('.mg-card').length;
  out.lastRows = rows(host.querySelectorAll('.mg-card').length - 1);
  const emptyIdx = host.querySelectorAll('.mg-card').length - 2;
  rowEl('notes').dispatchEvent(new w.Event('dragstart', { bubbles: true }));
  host.querySelectorAll('.mg-card')[emptyIdx].dispatchEvent(new w.Event('drop', { bubbles: true, cancelable: true }));   // drop into the empty group
  out.emptyFilled = rows(emptyIdx);
  const ti = host.querySelectorAll('.mg-title')[emptyIdx]; ti.value = 'My new group'; ti.dispatchEvent(new w.Event('input', { bubbles: true }));
  await sleep(750);
  out.postsAfterEdit = out.posts.length;                                       // one save for all those clicks (debounced)
  out.saved = host.querySelector('.mg-status').textContent;
  out.payload = out.posts[out.posts.length - 1] && out.posts[out.posts.length - 1].body;
  out.menuLayoutNow = J('menuLayout').length;
  out.sidebar = Array.from(w.document.querySelectorAll('#nav .navhead')).map((e) => e.textContent.replace(/[▸▾]/g, '').trim());
  let asked = 0; w.confirm = () => { asked++; return false; };
  click('[data-f="g:' + emptyIdx + ':rm"]'); out.removeDeclined = host.querySelectorAll('.mg-card').length; out.asked = asked;
  w.confirm = () => true;
  click('[data-f="g:' + emptyIdx + ':rm"]'); out.removeAccepted = host.querySelectorAll('.mg-card').length;
  out.notesAfterRemove = where(J('menuGroups(NAV, ' + JSON.stringify(J('menuLayout')) + ')'), 'notes');
  out.posts.length = 0;
  click('.mg-foot .btn.dim');                                                  // Reset to default
  await sleep(50);
  out.reset = out.posts.map((p) => p.body);
  out.afterReset = host.querySelectorAll('.mg-card').length === def.length;
  return out;
}
main().then((o) => process.stdout.write(JSON.stringify(o) + '\n', () => process.exit(0))).catch((e) => process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) }) + '\n', () => process.exit(0)));
