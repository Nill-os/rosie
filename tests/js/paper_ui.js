const fs = require('fs');
const vm = require('vm');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require(process.env.ROSIE_JSDOM || 'jsdom')); } catch (e) { console.log(JSON.stringify({ skip: 'jsdom not found' })); process.exit(0); }
const [dir, answersFile] = process.argv.slice(2);
const answers = JSON.parse(fs.readFileSync(answersFile, 'utf8'));
const shell = fs.readFileSync(path.join(dir, 'index.html'), 'utf8').replace(/<script[^>]*><\/script>/g, '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function page(width) {
  const dom = new JSDOM(shell, { url: 'http://127.0.0.1:8765/#/paper', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  Object.defineProperty(w, 'innerWidth', { value: width, configurable: true });
  const out = { errors: [], posts: [], gets: [] };
  w.addEventListener('error', (e) => out.errors.push(String(e.message || e.error)));
  w.addEventListener('unhandledrejection', (e) => out.errors.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
  w.confirm = () => true;
  w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {} }));
  const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
  w.fetch = async (u, o) => {
    const m = ((o && o.method) || 'GET').toUpperCase(), url = String(u);
    if (m !== 'GET') { out.posts.push({ method: m, url, body: o && typeof o.body === 'string' ? JSON.parse(o.body) : null }); return reply(200, answers[m + ' ' + url] || { ok: true, note: 'Sent 4 pages to the printer (one-sided).' }); }
    out.gets.push(url);
    return ('GET ' + url) in answers ? reply(200, answers['GET ' + url]) : reply(404, { error: 'not stubbed' });
  };
  const ctx = dom.getInternalVMContext();
  const T = (s, file) => new vm.Script(s, { filename: file || 'test' }).runInContext(ctx);
  const files = ['app.js', 'paper.js'];
  for (const f of files) {
    try { T(fs.readFileSync(path.join(dir, f), 'utf8'), f); } catch (e) { out.errors.push(f + ': ' + e.message); }
  }
  await sleep(80);
  await T('route()'); await sleep(30);
  const v = w.document.querySelector('#view');
  const fr = v.querySelector('iframe.pp-frame');
  out.title = w.document.querySelector('#ttl').textContent;
  out.headerButtons = [...w.document.querySelectorAll('#actions button')].map((b) => b.textContent);
  out.buttons = [...v.querySelectorAll('button')].map((b) => b.textContent.trim());
  out.frame = fr ? { src: fr.getAttribute('src'), sandbox: fr.getAttribute('sandbox') } : null;
  out.narrow = !!v.querySelector('.pp-page.pp-narrow');
  out.text = v.textContent;
  out.nav = [...w.document.querySelectorAll('#nav button')].map((b) => b.textContent);
  out.options = [...v.querySelectorAll('select option')].map((o) => o.textContent);
  const click = async (label) => { const b = [...v.querySelectorAll('button')].find((x) => x.textContent.trim() === label); if (b) { b.click(); await sleep(30); } return !!b; };
  out.clicked = { print: await click('Print'), add: await click('Add to my paper') };
  const lookBtn = [...v.querySelectorAll('.pp-tools .seg button')].find((b) => b.textContent === 'Newsprint');
  if (lookBtn) { lookBtn.click(); await sleep(40); }
  const fr2 = w.document.querySelector('#view iframe.pp-frame');
  out.frameAfterLook = fr2 ? fr2.getAttribute('src') : null;
  const pages2 = [...w.document.querySelectorAll('#view .pp-set .seg button')].find((b) => b.textContent === '2 pages');
  if (pages2) { pages2.click(); await sleep(30); }
  out.posts = out.posts.map((p) => ({ url: p.url, body: p.body }));
  return out;
}

(async () => {
  const desktop = await page(1280);
  const phone = await page(360);
  answers['GET /api/paper'] = answers._empty;
  const empty = await page(1280);
  done({ desktop, phone, empty: { text: empty.text, buttons: empty.buttons, frame: empty.frame, errors: empty.errors } });
})().catch((e) => done({ fatal: String(e && e.stack || e) }));
function done(o) { process.stdout.write(JSON.stringify(o) + '\n', () => process.exit(0)); }
