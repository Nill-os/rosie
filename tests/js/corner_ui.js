// board #832 safety-hostile-corner-data
const fs = require('fs');
const vm = require('vm');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require(process.env.ROSIE_JSDOM || 'jsdom')); } catch (e) { console.log(JSON.stringify({ skip: 'jsdom not found' })); process.exit(0); }
const [dir, answersFile] = process.argv.slice(2);
const answers = JSON.parse(fs.readFileSync(answersFile, 'utf8'));
const shell = fs.readFileSync(path.join(dir, 'index.html'), 'utf8').replace(/<script[^>]*><\/script>/g, '');
const dom = new JSDOM(shell, { url: 'http://127.0.0.1:8765/#/corner', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const out = { tabs: {}, errors: [], nav: '' };
w.addEventListener('error', (e) => out.errors.push(String(e.message || e.error)));
w.addEventListener('unhandledrejection', (e) => out.errors.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
w.HTMLMediaElement.prototype.play = () => Promise.resolve();
w.HTMLMediaElement.prototype.pause = () => {};
w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {} }));
w.confirm = () => true;
w.alert = (m) => out.errors.push('alert: ' + m);
const reply = (status, body) => ({ ok: status < 400, status, json: async () => body, blob: async () => new w.Blob([]) });
let liveGets = 0;
w.fetch = async (u, o) => {
  const m = ((o && o.method) || 'GET').toUpperCase(), url = String(u);
  if (url === '/api/corner/live') liveGets++;
  const k = m + ' ' + url;
  if (k in answers) return reply(200, answers[k]);
  if (m !== 'GET') return reply(200, {});
  return reply(404, { error: 'not stubbed' });
};
w.setInterval = () => 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const slowT = []; const realST = w.setTimeout.bind(w), realCT = w.clearTimeout.bind(w); let slowN = 0;
w.setTimeout = (fn, ms, ...args) => { if (!(ms >= 1500)) return realST(fn, ms, ...args); const id = 1e6 + ++slowN; slowT.push({ fn, ms, id }); return id; };
w.clearTimeout = (id) => { const i = slowT.findIndex((x) => x.id === id); if (i >= 0) slowT.splice(i, 1); else realCT(id); };
const fireSlow = async () => { const due = slowT.splice(0); for (const t of due) t.fn(); await sleep(20); };
let hidden = false;
Object.defineProperty(w.document, 'hidden', { configurable: true, get: () => hidden });
const ctx = dom.getInternalVMContext();
const T = (s, file) => new vm.Script(s, { filename: file || 'test' }).runInContext(ctx);
const DANGER = 'script, iframe, style, object, embed, link, meta, base, form, frame, frameset, svg, math';

(async () => {
  for (const f of ['app.js', 'corner.js']) {
    try { T(fs.readFileSync(path.join(dir, f), 'utf8'), f); } catch (e) { out.errors.push(f + ': ' + e.message); }
  }
  await sleep(50);
  out.nav = w.document.querySelector('#nav').textContent;
  const open = async (hash) => {
    w.history.replaceState(null, '', hash);
    await T('route()'); await sleep(40);
    const v = w.document.querySelector('#view');
    const danger = [...v.querySelectorAll(DANGER)].map((e) => e.tagName);
    for (const e of v.querySelectorAll('*')) {
      for (const a of e.attributes) {
        if (/^on/i.test(a.name) || a.name === 'style' && /url\(|expression|javascript/i.test(a.value) || a.name === 'srcdoc') danger.push(e.tagName + '[' + a.name + ']');
        if ((a.name === 'href' || a.name === 'src' || a.name === 'action' || a.name === 'formaction') && /^\s*(javascript|data|vbscript|file):/i.test(a.value)) danger.push(e.tagName + '[' + a.name + '=' + a.value + ']');
      }
    }
    const links = [...v.querySelectorAll('a[href]')].filter((a) => !a.getAttribute('href').startsWith('#/')).map((a) => ({ href: a.getAttribute('href'), dest: (a.nextSibling && a.nextSibling.textContent) || '', rel: a.getAttribute('rel') || '' }));
    return { text: v.textContent, danger, links, imgs: [...v.querySelectorAll('img')].map((i) => i.getAttribute('src')), buttons: [...v.querySelectorAll('button')].map((b) => b.textContent.trim()) };
  };
  for (const [name, hash] of [['live', '#/corner/live'], ['space', '#/corner'], ['finds', '#/corner/finds'], ['pictures', '#/corner/pictures'], ['notes', '#/corner/notes'],
    ['trail', '#/corner/trail'], ['changes', '#/corner/changes'], ['settings', '#/corner/settings']]) {
    out.tabs[name] = await open(hash);
  }
  if (answers._group) { out.tabs.finds_group = await open('#/corner/finds/' + answers._group); out.tabs.finds_new = await open('#/corner/finds/new'); }
  const p = {};
  await open('#/corner/live'); await sleep(20);
  p.first = liveGets; p.timers = slowT.length;
  await fireSlow(); p.after_tick = liveGets;
  hidden = true; await fireSlow(); p.hidden = liveGets; p.timers_hidden = slowT.length;
  hidden = false; w.document.dispatchEvent(new w.Event('visibilitychange')); await sleep(20); p.visible_again = liveGets;
  await open('#/corner/finds'); await fireSlow(); await fireSlow(); p.left = liveGets; p.timers_left = slowT.length;
  out.poll = p;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.log(JSON.stringify({ fatal: String(e), errors: [String(e)], tabs: {} })); process.exit(0); });
