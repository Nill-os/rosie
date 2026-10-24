// Renders the Vector page (static/app.js + static/vector.js) in jsdom with the server's real /api/vector answer (test_vector.py fills it
// with hostile text: <script>, onerror= and the like in his personality, commands and what he heard) and reports what ended up in the DOM.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require(process.env.ROSIE_JSDOM || 'jsdom')); } catch (e) { console.log(JSON.stringify({ skip: 'jsdom not found' })); process.exit(0); }
const [dir, answersFile] = process.argv.slice(2);
const answers = JSON.parse(fs.readFileSync(answersFile, 'utf8'));
const shell = fs.readFileSync(path.join(dir, 'index.html'), 'utf8').replace(/<script[^>]*><\/script>/g, '');
const dom = new JSDOM(shell, { url: 'http://127.0.0.1:8765/#/vector', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const out = { errors: [], posts: [] };
w.addEventListener('error', (e) => out.errors.push(String(e.message || e.error)));
w.addEventListener('unhandledrejection', (e) => out.errors.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {} }));
w.confirm = () => true;
w.alert = (m) => out.errors.push('alert: ' + m);
const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
w.fetch = async (u, o) => {
  const m = ((o && o.method) || 'GET').toUpperCase(), url = String(u);
  if (m !== 'GET') { out.posts.push([url, JSON.parse((o && o.body) || '{}')]); return reply(200, { ok: true }); }
  if (('GET ' + url) in answers) return reply(200, answers['GET ' + url]);
  return reply(404, { error: 'not stubbed' });
};
w.setInterval = () => 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const T = (s, file) => new vm.Script(s, { filename: file }).runInContext(dom.getInternalVMContext());
(async () => {
  for (const f of ['app.js', 'vector.js']) {
    try { T(fs.readFileSync(path.join(dir, f), 'utf8'), f); } catch (e) { out.errors.push(f + ': ' + e.message); }
  }
  await sleep(100);
  const doc = w.document;
  out.nav = doc.querySelector('#nav').textContent;
  out.text = doc.querySelector('#view').textContent;
  out.danger = [...doc.querySelectorAll('#view script, #view iframe, #view img, #view svg')].map((e) => e.tagName)
    .concat([...doc.querySelectorAll('#view *')].flatMap((e) => [...e.attributes].filter((a) => /^on/i.test(a.name)).map((a) => a.name)));
  out.buttons = [...doc.querySelectorAll('#view button')].map((b) => b.textContent);
  const box = doc.querySelector('#view input[type=checkbox]');       // the first screen (Clock) is on: turn it off
  box.checked = false;
  box.dispatchEvent(new w.Event('change'));
  const add = [...doc.querySelectorAll('#view button')].find((b) => b.textContent === 'Add');
  const phrases = doc.querySelector('#view input[aria-label=Phrases]');
  phrases.value = 'park it, stay put';
  add.click();
  await sleep(50);
  console.log(JSON.stringify(out));
})();
