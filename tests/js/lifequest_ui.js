// Renders the Life Quest page (static/app.js) in jsdom with the server's real /api/lifequest answer, then logs a win the way a user would:
// first with no note (nothing may be sent), then with a note and an area. Reports what ended up in the DOM and what was posted.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require(process.env.ROSIE_JSDOM || 'jsdom')); } catch (e) { console.log(JSON.stringify({ skip: 'jsdom not found' })); process.exit(0); }
const [dir, answersFile] = process.argv.slice(2);
const answers = JSON.parse(fs.readFileSync(answersFile, 'utf8'));
const shell = fs.readFileSync(path.join(dir, 'index.html'), 'utf8').replace(/<script[^>]*><\/script>/g, '');
const dom = new JSDOM(shell, { url: 'http://127.0.0.1:8765/#/lifequest', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const out = { errors: [], posts: [], toasts: [] };
w.addEventListener('error', (e) => out.errors.push(String(e.message || e.error)));
w.addEventListener('unhandledrejection', (e) => out.errors.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {} }));
w.confirm = () => true;
w.alert = (m) => out.errors.push('alert: ' + m);
const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
w.fetch = async (u, o) => {
  const m = ((o && o.method) || 'GET').toUpperCase(), url = String(u);
  if (m !== 'GET') { out.posts.push([url, JSON.parse((o && o.body) || '{}')]); return reply(200, answers['GET /api/lifequest']); }
  if (('GET ' + url) in answers) return reply(200, answers['GET ' + url]);
  return reply(404, { error: 'not stubbed' });
};
w.setInterval = () => 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const T = (s, file) => new vm.Script(s, { filename: file }).runInContext(dom.getInternalVMContext());
(async () => {
  try { T(fs.readFileSync(path.join(dir, 'app.js'), 'utf8'), 'app.js'); } catch (e) { out.errors.push('app.js: ' + e.message); }
  await sleep(100);
  const doc = w.document, view = () => doc.querySelector('#view');
  out.nav = doc.querySelector('#nav').textContent;
  out.title = doc.querySelector('#ttl').textContent;
  out.text = view().textContent;
  out.tags = [...view().querySelectorAll('.lq-tag')].map((b) => b.textContent);
  const form = view().querySelector('form'), note = view().querySelector('input[aria-label="What you did"]');
  form.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await sleep(50);
  out.postsAfterEmpty = out.posts.length;
  out.toastEmpty = doc.querySelector('#toast').textContent;
  note.value = 'ran two miles';
  form.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await sleep(50);
  out.postsNoArea = out.posts.length;
  view().querySelector('.lq-tag').click();
  form.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await sleep(80);
  out.posts = out.posts.filter((p) => p[0] === '/api/lifequest/log');
  out.danger = [...view().querySelectorAll('script, iframe, img')].map((e) => e.tagName);
  console.log(JSON.stringify(out));
})();
