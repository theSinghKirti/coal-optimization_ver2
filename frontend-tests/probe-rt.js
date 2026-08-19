const { JSDOM } = require('jsdom');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = fs.readFileSync(path.join(ROOT, 'dashboard_template.html'), 'utf8');
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const PORT = 8127;

const dom = new JSDOM(TEMPLATE, { url: 'https://example.com/', runScripts: 'dangerously', pretendToBeVisual: true });
const win = dom.window;

const proc = spawn(PY, ['-m', 'uvicorn', 'main:app', '--port', String(PORT), '--log-level', 'warning'], {
  cwd: path.join(ROOT, 'backend'),
  stdio: 'ignore',
});

async function waitHealth(retries = 60) {
  for (let i = 0; i < retries; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/health`);
      if (r.ok) return true;
    } catch (e) {}
    await new Promise(r => setTimeout(r, 250));
  }
  return false;
}

(async () => {
  try {
    const healthy = await waitHealth();
    console.log('backend healthy:', healthy);
    if (!healthy) return;
    win.saveOverride('Parichha', '__plant__', 'rsd_threshold', 4.00);
    win.saveOverride('Panki', '__plant__', 'rsd_threshold', 3.00);
    const payload = win.buildOptimizePayload();
    console.log('sent thresholds:', payload.plants.map(p => `${p.plant}=${p.rsd_threshold_vc}`).join(' | '));
    const resp = await fetch(`http://127.0.0.1:${PORT}/optimize`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload),
    });
    const body = await resp.json();
    console.log('status:', body.status, '| shutdowns:', body.total_shutdowns);
    console.log('errors:', JSON.stringify(body.errors, null, 1));
    console.log('plants:', body.plants.map(p => `${p.plant}: vc=${p.optimized_vc}, thr=${p.rsd_threshold_vc}, ${p.rsd_status}`).join(' | '));
    console.log('message:', body.message);
  } finally {
    proc.kill();
  }
})();
