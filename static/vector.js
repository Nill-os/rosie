'use strict';
/* The Vector page (owner 2026-10-21: "make my own local AI on it without using the clunky program, i want it to be rosie"): Rosie's own
   Vector server (vector_pod.py) on or off, pointing the robot at her, his personality, his voice commands, what his face shows while he
   sits, and what he heard last. Everything from the server is put on the page as text (h() children), never as HTML. */
let vecTimer = null;
const vecDraft = { personality: null, say: '', ip: '' };          // half-typed text survives the redraw every few seconds
const VEC_PANELS = [['clock', 'Clock'], ['system', 'PC load (CPU, RAM, GPU)'], ['next_event', 'Next calendar event'], ['battery', "Vector's battery"]];

VIEWS.vector = async () => {
  clearInterval(vecTimer);
  setHeader('Vector', []);
  const here = () => location.hash.replace(/^#\/?/, '').startsWith('vector');
  const act = async (url, body, label) => {
    toast(label + '…');
    try { const r = await post(url, body || {}); if (r.ok === false) throw new Error(r.error || 'It did not work.'); toast(r.message || label + ': done'); } catch (e) { toast(e.message, 1); }
    draw();
  };
  const typing = () => { const el = document.activeElement; return !!(el && el.closest && el.closest('.vec-keep')); };
  const draw = async () => {
    if (!here()) { clearInterval(vecTimer); return; }
    if (typing()) return;                                           // never redraw under a half-written box
    let d;
    try { d = await J('/api/vector'); } catch (e) { show(h('div', { class: 'err' }, e.message)); return; }
    const bot = d.robots[0];
    const status = h('div', { class: 'card' }, h('h3', {}, 'Rosie is his brain'),
      h('div', { class: 'seg', style: 'margin-bottom:10px' },
        h('button', { class: d.on ? 'on' : '', onclick: () => act('/api/vector/settings', { on: true }, 'Starting the Vector server') }, 'On'),
        h('button', { class: d.on ? '' : 'on', onclick: () => act('/api/vector/settings', { on: false }, 'Stopping the Vector server') }, 'Off')),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Server'), h('dd', {}, d.running ? `Listening at ${d.host}:${d.port}` : 'Off'),
        h('dt', {}, 'Robot'), h('dd', {}, bot ? `${bot.name || bot.esn} at ${bot.ip}${bot.firmware ? ' · ' + bot.firmware : ''}  ` : 'None set up yet', bot ? h('span', { class: 'chip ' + (d.online ? 'ok' : 'bad') }, d.online ? 'online' : 'not reachable') : null,
          d.held ? h('span', { class: 'chip ok', style: 'margin-left:6px' }, 'sitting') : null),
        h('dt', {}, 'Last heard'), h('dd', {}, d.last ? `${d.last.at} “${d.last.heard || '(nothing)'}” → ${d.last.did}` : 'Nothing yet.')),
      d.error ? h('div', { class: 'err', style: 'margin-top:8px' }, d.error) : null);

    const ip = h('input', { type: 'text', placeholder: "Vector's address, like 192.168.1.15", 'aria-label': "Vector's address", value: vecDraft.ip || (bot ? bot.ip : ''),
      oninput: (e) => { vecDraft.ip = e.target.value; } });
    const setup = h('div', { class: 'card' }, h('h3', {}, 'Set him up'),
      h('ol', { class: 'muted', style: 'margin:0 0 10px;padding-left:18px;font-size:calc(13px*var(--ts,1))' },
        h('li', {}, 'Vector is on your Wi-Fi, and wire-pod is not running.'),
        d.wirepod ? h('li', {}, 'Take over from wire-pod: his settings and SDK token move to Rosie, so nothing on him is reset.') : null,
        h('li', {}, 'Turn the server on, type his address, then connect. Escape pod firmware (2.0.1.6076ep): nothing on him changes, Rosie answers as his escape pod server. WireOS: Rosie copies her address and certificate to him over SSH, and Undo puts his old settings back.')),
      d.port_rule ? h('div', { class: 'err', style: 'margin-bottom:8px' }, 'Escape pod robots call port 443. Allow it once in a terminal: ', h('code', {}, d.port_rule)) : null,
      d.wirepod ? h('div', { class: 'form', style: 'margin-bottom:8px' }, btn('Take over from wire-pod', 'sm', () => act('/api/vector/import', {}, 'Taking over from wire-pod'))) : null,
      h('div', { class: 'form vec-keep' }, ip,
        btn('Connect Vector to Rosie', 'sm pur', () => confirm(`Connect the Vector at ${ip.value} to Rosie?`) && act('/api/vector/setup', { ip: ip.value.trim() }, 'Connecting')),
        btn('Undo', 'sm bad', () => confirm('Put his old server settings back?') && act('/api/vector/undo', { ip: ip.value.trim() }, 'Undoing'))));

    const say = h('input', { type: 'text', placeholder: 'Something for him to say', 'aria-label': 'Something for Vector to say', maxlength: 600, value: vecDraft.say,
      oninput: (e) => { vecDraft.say = e.target.value; }, onkeydown: (e) => { if (e.key === 'Enter') sayIt(); } });
    const sayIt = () => { if (!say.value.trim()) return; const t = say.value; vecDraft.say = ''; say.value = ''; say.blur(); act('/api/vector/say', { text: t }, 'Saying it'); };
    const control = h('div', { class: 'card' }, h('h3', {}, 'Right now'),
      h('div', { class: 'form', style: 'margin-bottom:8px' }, btn('Sit', 'sm', () => act('/api/vector/hold', {}, 'Sit')), btn('You can move', 'sm', () => act('/api/vector/release', {}, 'Releasing'))),
      h('div', { class: 'form vec-keep' }, say, btn('Say', 'sm pur', sayIt)),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Sit keeps him still with his own behaviors off; his face shows the screens you pick below.'));

    const pers = h('textarea', { rows: 4, 'aria-label': 'Personality', placeholder: 'How he talks. Example: dry wit, curious about everything, short answers.', maxlength: 2000,
      oninput: (e) => { vecDraft.personality = e.target.value; } });
    pers.value = vecDraft.personality !== null ? vecDraft.personality : d.personality;
    const personality = h('div', { class: 'card' }, h('h3', {}, 'Personality'),
      h('div', { class: 'form col vec-keep' }, pers, btn('Save', 'sm pur', async () => { await act('/api/vector/settings', { personality: pers.value }, 'Saving'); vecDraft.personality = null; })),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Added to Rosie\'s prompt when she answers through him. His chats are in the "Vector" chat.'));

    const own = d.commands.map((c) => ({ ...c }));
    const saveCmds = () => act('/api/vector/settings', { commands: own }, 'Saving commands');
    const phrasesIn = h('input', { type: 'text', placeholder: 'Phrases, comma separated (park it, stay put)', 'aria-label': 'Phrases' });
    const actionIn = h('select', { 'aria-label': 'Action' }, h('option', { value: 'hold' }, 'Sit (stay still)'), h('option', { value: 'release' }, 'You can move'), h('option', { value: 'say' }, 'Say something'));
    const textIn = h('input', { type: 'text', placeholder: 'What he says (for Say) or his reply', 'aria-label': 'Reply' });
    const commands = h('div', { class: 'card wide' }, h('h3', {}, 'Voice commands'),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-bottom:8px' }, 'Said as the whole sentence after his wake word. Your own commands come first. Everything else goes to Rosie, except his own skills (timer, photo, fist bump, volume, eye color). With his eyes: “what do you see”, “look around”, “go to the mug”, “find my phone”.'),
      h('div', { class: 'list' },
        own.map((c, i) => h('div', { class: 'item' }, h('span', { class: 'tx' }, h('b', {}, c.phrases.join(', ') + '  '), h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, `${c.action}${c.text ? ': ' + c.text : ''}${c.reply ? ' · replies “' + c.reply + '”' : ''}`)),
          btn('Remove', 'sm bad', () => { own.splice(i, 1); saveCmds(); }))),
        d.built_in.map((c) => h('div', { class: 'item' }, h('span', { class: 'tx' }, h('b', {}, c.phrases.slice(0, 6).join(', ') + '…  '), h('span', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1))' }, `${c.action} · built in`))))),
      h('div', { class: 'form vec-keep', style: 'margin-top:10px' }, phrasesIn, actionIn, textIn,
        btn('Add', 'sm pur', () => {
          const phrases = phrasesIn.value.split(',').map((s) => s.trim()).filter(Boolean);
          if (!phrases.length) { toast('Type at least one phrase.', 1); return; }
          own.unshift(actionIn.value === 'say' ? { phrases, action: 'say', text: textIn.value, reply: '' } : { phrases, action: actionIn.value, reply: textIn.value });
          phrasesIn.blur(); textIn.blur(); saveCmds();
        })));

    const vols = [['LOW', 'Low'], ['MEDIUM_LOW', 'Med-low'], ['MEDIUM', 'Medium'], ['MEDIUM_HIGH', 'Med-high'], ['HIGH', 'High']];
    const sounds = h('div', { class: 'card' }, h('h3', {}, 'Voice and looks'),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Voice'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.voice !== 'vector' ? 'on' : '', onclick: () => act('/api/vector/settings', { voice: 'rosie' }, 'Rosie\'s voice') }, 'Rosie'),
          h('button', { class: d.voice === 'vector' ? 'on' : '', onclick: () => act('/api/vector/settings', { voice: 'vector' }, 'His own voice') }, 'Vector'))),
        h('dt', {}, 'Eyes'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.eyes !== 'own' ? 'on' : '', onclick: () => act('/api/vector/settings', { eyes: 'rosie' }, 'Cyan to purple eyes') }, 'Rosie'),
          h('button', { class: d.eyes === 'own' ? 'on' : '', onclick: () => act('/api/vector/settings', { eyes: 'own' }, 'His own eye colour') }, 'His own'))),
        h('dt', {}, 'Back lights'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.backpack === 'rosie' ? 'on' : '', onclick: () => confirm('Cyan and purple back lights? His services restart (about 30 seconds). WireOS only.') && act('/api/vector/backpack', { rosie: true }, 'Cyan and purple back lights') }, 'Rosie'),
          h('button', { class: d.backpack === 'rosie' ? '' : 'on', onclick: () => confirm('His own back light colours again? His services restart (about 30 seconds).') && act('/api/vector/backpack', { rosie: false }, 'His own back lights') }, 'His own'))),
        h('dt', {}, 'Follow-ups'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.followup ? 'on' : '', onclick: () => act('/api/vector/settings', { followup: true }, 'Follow-ups on') }, 'On'),
          h('button', { class: d.followup ? '' : 'on', onclick: () => act('/api/vector/settings', { followup: false }, 'Follow-ups off') }, 'Off'))),
        h('dt', {}, 'Listening'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.stt_model !== 'small.en' ? 'on' : '', onclick: () => act('/api/vector/settings', { stt_model: 'base.en' }, 'Quick listening') }, 'Quick'),
          h('button', { class: d.stt_model === 'small.en' ? 'on' : '', onclick: () => act('/api/vector/settings', { stt_model: 'small.en' }, 'Careful listening') }, 'Careful'))),
        h('dt', {}, 'Wake word'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.wake_word === 'hey rosie' ? 'on' : '', onclick: () => confirm('Wake him with “Hey Rosie”? His services restart (about 30 seconds). WireOS only.') && act('/api/vector/wake', { rosie: true }, 'Hey Rosie') }, 'Hey Rosie'),
          h('button', { class: d.wake_word === 'hey rosie' ? '' : 'on', onclick: () => confirm('Back to “Hey Vector”? His services restart (about 30 seconds).') && act('/api/vector/wake', { rosie: false }, 'Hey Vector') }, 'Hey Vector'))),
        h('dt', {}, 'Quiet when idle'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: d.quiet_idle ? 'on' : '', onclick: () => act('/api/vector/settings', { quiet_idle: true }, 'Quiet when idle') }, 'On'),
          h('button', { class: d.quiet_idle ? '' : 'on', onclick: () => act('/api/vector/settings', { quiet_idle: false }, 'Normal sounds') }, 'Off'))),
        h('dt', {}, 'Talking volume'), h('dd', {}, h('div', { class: 'seg' }, vols.map(([v, label]) =>
          h('button', { class: d.talk_volume === v ? 'on' : '', onclick: () => act('/api/vector/settings', { talk_volume: v }, 'Volume ' + label) }, label))))),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Quiet when idle: no chirps or mumbles while he wanders. His wake word gives him his voice back until he has answered.'));

    const picked = d.face_panels || [];
    const face = h('div', { class: 'card' }, h('h3', {}, 'His face while he sits'),
      h('div', { class: 'list' }, VEC_PANELS.map(([k, label]) => h('label', { class: 'item', style: 'cursor:pointer' },
        h('input', { type: 'checkbox', checked: picked.includes(k), onchange: (e) => {
          const next = VEC_PANELS.map((p) => p[0]).filter((p) => (p === k ? e.target.checked : picked.includes(p)));
          act('/api/vector/settings', { face_panels: next }, 'Saving');
        } }), h('span', { class: 'tx' }, label)))),
      h('dl', { class: 'kv', style: 'margin-top:8px' }, h('dt', {}, 'Each screen'), h('dd', {}, h('div', { class: 'seg' }, [[10, '10 s'], [15, '15 s'], [30, '30 s'], [60, '1 min']].map(([v, label]) =>
        h('button', { class: (d.face_seconds || 15) === v ? 'on' : '', onclick: () => act('/api/vector/settings', { face_seconds: v }, 'Each screen ' + label) }, label))))),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'A running timer always shows. While he sits, his backpack button opens a settings menu on his face: move his lift by hand to pick a line, press the button to change it.'));

    const pad = d.pad || {};
    const padState = !pad.supported ? 'Needs Linux on this PC.' : !pad.on ? 'Off.'
      : pad.error ? pad.error : !pad.controller ? 'No controller found. Pair it with this PC (PS4: hold Share and PS until the light flashes), or plug it in.'
        : `${pad.controller}: ${pad.driving ? 'driving mode' : 'connected'}.`;
    const speeds = [['slow', 'Slow'], ['normal', 'Normal'], ['fast', 'Fast']];
    const padMap = [['Left stick', 'Drive (pushing it starts driving mode)'], ['Right stick', 'Tilt his head'], ['L2 / R2', 'Lower / raise his lift'],
      ['PS', 'Driving mode on or off'], ['Circle', 'Sit / you can move'], ['Triangle', 'What do you see?'], ['Cross', 'Rosie says a quick line'],
      ['Square', 'One of his animations'], ['Options', 'He listens (no wake word)'], ['D-pad left/right', 'Next screen while he sits']];
    const controller = h('div', { class: 'card' }, h('h3', {}, 'Controller'),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-bottom:8px' }, padState),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Controller'), h('dd', {}, h('div', { class: 'seg' },
          h('button', { class: pad.on ? 'on' : '', onclick: () => act('/api/vector/settings', { pad: true }, 'Controller on') }, 'On'),
          h('button', { class: pad.on ? '' : 'on', onclick: () => act('/api/vector/settings', { pad: false }, 'Controller off') }, 'Off'))),
        h('dt', {}, 'Speed'), h('dd', {}, h('div', { class: 'seg' }, speeds.map(([v, label]) =>
          h('button', { class: (pad.speed || 'normal') === v ? 'on' : '', onclick: () => act('/api/vector/settings', { pad_speed: v }, 'Speed ' + label) }, label)))),
        padMap.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v)])),
      h('div', { class: 'muted', style: 'font-size:calc(12px*var(--ts,1));margin-top:6px' }, 'Driving mode ends with PS, Circle, “you can move”, two minutes without input, or when the controller turns off. Cliffs and his wake word still work.'));

    const y = $('#view').scrollTop;
    show(h('div', { class: 'pagehead' }, h('h2', {}, 'Vector'), h('span', { class: 'muted' }, 'Your robot, with Rosie as his brain. Local only: no wire-pod, no cloud.')),
      h('div', { class: 'grid' }, status, control, controller, setup, sounds, personality, face, commands));
    $('#view').scrollTop = y;
  };
  clearInterval(vecTimer);
  vecTimer = setInterval(draw, 5000);
  await draw();
};
