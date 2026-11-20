// Render a built flow-scene HTML at chosen moments into one contact-sheet PNG, run the layout check and
// report page errors. Read the PNG afterwards and fix what looks wrong.
//
// usage: node frames.mjs --html out.html [--scene <id>|all] [--frames "1:0.5,3:0.9,last"] [--out sheet.png] [--width 1200]
//   frames: "<step>:<u>" (step is 1-based, u = 0..1 progress through the step), "last", or "auto" (default: ~8 evenly spaced)
// Needs playwright: `npm i playwright@1.56.1` in a scratch dir, then run with NODE_PATH=<dir>/node_modules.
// If the bundled browser is missing it falls back to an installed Chrome (or set CHROME=/path/to/chrome).
import { createRequire } from 'node:module';
import { existsSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const args = process.argv.slice(2), opt = {};
for (let i = 0; i < args.length; i += 2) opt[args[i].replace(/^--/, '')] = args[i + 1];
if (!opt.html) { console.error('need --html <built scene html>'); process.exit(1); }
let pw;
for (const base of [process.cwd(), ...(process.env.NODE_PATH || '').split(':').filter(Boolean).map((p) => resolve(p, '..'))]) {
  try { pw = createRequire(resolve(base, 'x.js'))('playwright'); break; } catch {}
}
if (!pw) { console.error('playwright not found. In a scratch dir: npm i playwright@1.56.1, then NODE_PATH=<dir>/node_modules node frames.mjs ...'); process.exit(1); }

async function launch() {
  try { return await pw.chromium.launch({ headless: true }); } catch (e) {
    const cands = [process.env.CHROME, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
      'C:/Program Files/Google/Chrome/Application/chrome.exe'].filter(Boolean);
    for (const c of cands) if (existsSync(c)) return pw.chromium.launch({ headless: true, executablePath: c });
    throw e;
  }
}
const W = +(opt.width || 1200);
const b = await launch();
const p = await b.newPage({ viewport: { width: W, height: 900 }, deviceScaleFactor: 1.5 });
const errs = [];
p.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
p.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errs.push(m.type() + ': ' + m.text()); });
await p.emulateMedia({ reducedMotion: 'no-preference' });
await p.goto(pathToFileURL(resolve(opt.html)).href);
await p.waitForFunction(() => window.FLOW && window.FLOW.ready, null, { timeout: 15000 }).catch(() => errs.push('runtime never became ready'));
const ids = await p.evaluate(() => (window.FLOW ? window.FLOW.scenes : []));
const which = !opt.scene || opt.scene === 'all' ? ids : [opt.scene];
const tiles = [], reports = [];
for (const id of which) {
  await p.evaluate((id) => window.FLOW.tab(id), id);
  await p.waitForTimeout(150);
  const rep = await p.evaluate(() => window.FLOW.check());
  reports.push(rep);
  const n = rep.steps;
  let frames = [];
  const spec = opt.frames || 'auto';
  if (!n) frames = [[0, 0]];
  else if (spec === 'auto') { const k = Math.min(8, n); for (let i = 0; i < k; i++) frames.push([Math.round((i * (n - 1)) / Math.max(1, k - 1)), i === k - 1 ? 1 : 0.5]); }
  else frames = spec.split(',').map((f) => (f === 'last' ? [n - 1, 1] : [Math.max(0, +f.split(':')[0] - 1), +(f.split(':')[1] ?? 0.5)]));
  for (const [s, u] of frames) {
    await p.evaluate(([s, u]) => window.FLOW.seek(s, u), [s, u]);
    await p.waitForTimeout(60);
    const buf = await (await p.$('.fs-stage')).screenshot();
    tiles.push({ cap: `${id} · ${n ? 'step ' + (s + 1) + ' @' + u : 'map'}`, src: 'data:image/png;base64,' + buf.toString('base64') });
  }
}
const sheet = await b.newPage({ viewport: { width: 1400, height: 800 } });
await sheet.setContent(`<body style="margin:0;background:#eee;font:13px system-ui"><div id=r style="display:grid;grid-template-columns:repeat(2,680px);gap:10px;padding:10px">${tiles.map((t) => `<figure style="margin:0;background:#fff;padding:6px"><img src="${t.src}" style="width:100%"><figcaption>${t.cap}</figcaption></figure>`).join('')}</div></body>`);
const out = resolve(opt.out || 'flow-frames.png');
await (await sheet.$('#r')).screenshot({ path: out });
await b.close();
console.log('sheet: ' + out);
for (const r of reports) {
  console.log(`\n[${r.scene}] ${r.nodes} nodes, ${r.steps} steps, direction ${r.dir}, scale ${r.scale}`);
  if (!r.issues.length) console.log('  layout clean');
  for (const i of r.issues) console.log(`  ${i.level}: ${i.msg}`);
}
if (errs.length) { console.log('\npage errors:\n' + errs.join('\n')); process.exitCode = 2; }
if (reports.some((r) => r.issues.some((i) => i.level === 'error'))) process.exitCode = 2;
