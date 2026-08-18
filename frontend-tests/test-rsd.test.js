const { JSDOM } = require('jsdom');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = fs.readFileSync(path.join(ROOT, 'dashboard_template.html'), 'utf8');
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const PORT = 8127;
const BASE = `http://127.0.0.1:${PORT}`;
const LS_KEY = 'coalOptimizerOverrides.v1';

function makeWindow(seedOverrides) {
  let html = TEMPLATE;
  if (seedOverrides) {
    const seedScript = `<script>localStorage.setItem('${LS_KEY}', ${JSON.stringify(JSON.stringify(seedOverrides))});</script>`;
    html = TEMPLATE.replace('<body>', '<body>' + seedScript);
  }
  const dom = new JSDOM(html, {
    url: 'https://example.com/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
  });
  return dom.window;
}

function thresholdInput(win) {
  return win.document.getElementById('rsdThresholdInput');
}

function wholeRakesOverrides(win) {
  const ov = {};
  for (const p of win.buildState()) {
    for (const s of p.sources) {
      if (!Number.isInteger(s.currentRakes)) {
        ov[`${p.name}||${s.name}`] = { currentRakes: Math.round(s.currentRakes) };
      }
    }
  }
  return ov;
}

function startBackend() {
  return spawn(PY, ['-m', 'uvicorn', 'main:app', '--port', String(PORT), '--log-level', 'warning'], {
    cwd: path.join(ROOT, 'backend'),
    stdio: 'ignore',
  });
}

async function waitHealth(retries = 60) {
  for (let i = 0; i < retries; i++) {
    try {
      const r = await fetch(`${BASE}/health`);
      if (r.ok) return true;
    } catch (e) {}
    await new Promise(r => setTimeout(r, 250));
  }
  return false;
}

test('RSD threshold input handler mutates state and persists the override', () => {
  const win = makeWindow();
  const plantName = win.buildState()[0].name;

  let inp = thresholdInput(win);
  assert.ok(inp, 'threshold input exists in the active plant panel');
  assert.strictEqual(inp.value, '', 'starts unconstrained');

  inp.value = '4.25';
  inp.dispatchEvent(new win.Event('input', { bubbles: true }));

  const saved = JSON.parse(win.localStorage.getItem(LS_KEY));
  assert.strictEqual(saved[`${plantName}||__plant__`].rsd_threshold, 4.25, 'override persisted');
  assert.strictEqual(win.buildState()[0].rsd_threshold, 4.25, 'working state carries the threshold');
  assert.strictEqual(thresholdInput(win).value, '4.2500', 're-rendered input shows the value');

  inp = thresholdInput(win);
  inp.value = '';
  inp.dispatchEvent(new win.Event('input', { bubbles: true }));
  assert.strictEqual(JSON.parse(win.localStorage.getItem(LS_KEY))[`${plantName}||__plant__`].rsd_threshold, null, 'clearing removes the constraint');
  assert.strictEqual(win.buildState()[0].rsd_threshold, null, 'working state cleared');
  assert.strictEqual(thresholdInput(win).value, '', 're-rendered input empty again');
});

test('RSD threshold survives a page reload via localStorage', () => {
  const name0 = makeWindow().buildState()[0].name;
  const win = makeWindow({ [`${name0}||__plant__`]: { rsd_threshold: 4.12 } });

  assert.strictEqual(win.buildState()[0].rsd_threshold, 4.12, 'buildState re-applies the saved threshold on load');
  assert.strictEqual(thresholdInput(win).value, '4.1200', 'input shows the restored value');
});

test('optimize payload carries rsd_threshold_vc for every plant (null when unset)', () => {
  const names = makeWindow().buildState().map(p => p.name);
  const thresholded = names.slice(0, 2);
  const seed = Object.fromEntries(thresholded.map((n, i) => [`${n}||__plant__`, { rsd_threshold: 3.5 + i * 0.25 }]));
  const win = makeWindow(seed);
  const payload = win.buildOptimizePayload();

  assert.strictEqual(payload.plants.length, names.length);
  for (const p of payload.plants) {
    assert.ok(Object.prototype.hasOwnProperty.call(p, 'rsd_threshold_vc'), `${p.plant} always sends the key`);
  }
  for (const name of thresholded) {
    const p = payload.plants.find(x => x.plant === name);
    assert.strictEqual(p.rsd_threshold_vc, seed[`${name}||__plant__`].rsd_threshold, `${name} sends its threshold`);
  }
  const unset = payload.plants.find(p => !thresholded.includes(p.plant));
  assert.strictEqual(unset.rsd_threshold_vc, null, 'unset plants send null');
});

test('official result panel renders VC, threshold, and Safe/RSD/No Constraint status', () => {
  const probe = makeWindow();
  const names = probe.buildState().map(p => p.name);
  const thresholded = names.slice(0, 2);
  const seed = Object.fromEntries(thresholded.map((n, i) => [`${n}||__plant__`, { rsd_threshold: 4.00 + i * 0.1 }]));
  const win = makeWindow(seed);
  const state = win.buildState();

  const result = {
    status: 'Optimal',
    total_shutdowns: 1,
    weighted_vc_before: 4.2000,
    weighted_vc_after: 4.1000,
    vc_improvement: 0.1000,
    plants: state.map((p, i) => ({
      plant: p.name,
      current_rakes: p.totalRakes,
      optimized_rakes: p.totalRakes,
      current_vc: 4.2000,
      optimized_vc: thresholded.includes(p.name) && i === 0 ? 4.3199 : 4.1500,
      rsd_threshold_vc: seed[`${p.name}||__plant__`] ? seed[`${p.name}||__plant__`].rsd_threshold : null,
      rsd_status: thresholded.includes(p.name) ? (i === 0 ? 'rsd' : 'safe') : 'no_constraint',
      exceeded_threshold: thresholded.includes(p.name) && i === 0,
      delta_vc: 0.1199,
    })),
    allocations: [
      { plant: names[0], company: 'X', current_rakes: 1, optimized_rakes: 1, source_vc: 4.3199, delta_rakes: 0 },
    ],
    constraint_status: [
      { name: 'RSD threshold: ' + names[0], satisfied: false, detail: 'VC 4.3199 > 4.0000' },
      { name: 'RSD threshold: ' + names[1], satisfied: true, detail: 'VC 4.1500 <= 4.1000' },
    ],
  };
  win.renderServerOptimizeResult(result);
  const text = win.document.getElementById('serverOptimizeBody').textContent;

  assert.ok(text.includes('1 of 2'), 'shutdown summary counts only thresholded plants');
  assert.ok(text.includes('Safe'), 'safe badge present');
  assert.ok(text.includes('RSD'), 'rsd badge present');
  assert.ok(text.includes('No Constraint'), 'no-constraint badge present');
  assert.ok(text.includes('4.0000') && text.includes('4.1000'), 'thresholds rendered to 4 decimals');
  assert.ok(text.includes('4.3199'), 'optimized VC rendered');
  assert.ok(text.includes('RSD threshold: ' + names[0]), 'constraint rows label the plant');
});

test('end-to-end: frontend payload -> real backend -> rendered result preserves RSD data', async () => {
  const proc = startBackend();
  try {
    assert.ok(await waitHealth(), 'backend healthy');

    const probe = makeWindow();
    const rakes = wholeRakesOverrides(probe); // real data carries fractional rake receipts; make them whole like a user edit
    const thresholds = {
      [`${probe.buildState()[0].name}||__plant__`]: { rsd_threshold: 99.0 }, // trivially safe
      [`${probe.buildState()[2].name}||__plant__`]: { rsd_threshold: 0.01 }, // impossible -> must be shut down
    };
    const win = makeWindow({ ...rakes, ...thresholds });
    const payload = win.buildOptimizePayload();

    const thresholdedNames = [];
    for (const p of payload.plants) {
      const ov = thresholds[`${p.plant}||__plant__`];
      assert.strictEqual(p.rsd_threshold_vc, ov ? ov.rsd_threshold : null, `${p.plant} threshold in payload`);
      if (ov) thresholdedNames.push(p.plant);
    }

    const resp = await fetch(`${BASE}/optimize`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    assert.strictEqual(resp.status, 200, 'backend accepts the frontend-built payload');
    const body = await resp.json();
    assert.ok(['Optimal', 'Feasible'].includes(body.status), `solver succeeded, got ${body.status}`);
    assert.strictEqual(body.total_shutdowns, 1, 'only the impossible plant is shut down');

    const byName = Object.fromEntries(body.plants.map(p => [p.plant, p]));
    assert.strictEqual(byName[thresholdedNames[0]].rsd_status, 'safe', 'high threshold plant is safe');
    assert.strictEqual(byName[thresholdedNames[1]].rsd_status, 'rsd', 'impossible plant is in RSD');
    assert.strictEqual(byName[thresholdedNames[0]].rsd_threshold_vc, 99.0, 'threshold echoed back');
    assert.strictEqual(byName[thresholdedNames[1]].rsd_threshold_vc, 0.01, 'threshold echoed back');
    for (const name of payload.plants.map(p => p.plant).filter(n => !thresholdedNames.includes(n))) {
      assert.strictEqual(byName[name].rsd_status, 'no_constraint', `${name} has no constraint`);
      assert.strictEqual(byName[name].rsd_threshold_vc, null, `${name} threshold echoed as null`);
    }

    win.renderServerOptimizeResult(body);
    const text = win.document.getElementById('serverOptimizeBody').textContent;
    assert.ok(text.includes('1 of 2'), 'rendered summary shows the minimized shutdown count');
    assert.ok(text.includes('Safe') && text.includes('RSD') && text.includes('No Constraint'), 'all three statuses rendered');
    assert.ok(text.includes('99.0000') && text.includes('0.0100'), 'echoed thresholds rendered');
  } finally {
    proc.kill();
  }
});