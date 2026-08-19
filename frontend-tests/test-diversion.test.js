const { JSDOM } = require('jsdom');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = fs.readFileSync(path.join(ROOT, 'dashboard_template.html'), 'utf8');
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const PORT = 8128; // deliberately different from the RSD round-trip test's port
const BASE = `http://127.0.0.1:${PORT}`;
const LS_KEY = 'coalOptimizerOverrides.v1';

// realFetch (optional): a Node-side fetch implementation bridged into the
// jsdom window, since jsdom does not implement fetch itself. Used for both
// stubbed responses and the real-backend round trip.
// configure (optional): runs in beforeParse, before the template's script -
// used to point OPTIMIZER_API_BASE at the test backend.
function makeWindow(seedOverrides, realFetch, configure){
  let html = TEMPLATE;
  if(seedOverrides){
    const seedScript = `<script>localStorage.setItem('${LS_KEY}', ${JSON.stringify(JSON.stringify(seedOverrides))});</script>`;
    html = TEMPLATE.replace('<body>', '<body>' + seedScript);
  }
  const dom = new JSDOM(html, {
    url: 'https://example.com/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window){
      if(realFetch) window.fetch = (url, opts) => realFetch(url, opts);
      if(configure) configure(window);
    },
  });
  return dom.window;
}

function actualInputs(win){
  return win.document.querySelectorAll('#diversionCalcBody input[data-field="actualRakes"]');
}

async function waitFor(fn, timeoutMs = 4000){
  const start = Date.now();
  while(Date.now() - start < timeoutMs){
    const v = await fn();
    if(v) return v;
    await new Promise(r => setTimeout(r, 40));
  }
  throw new Error('waitFor timed out');
}

test('panel opens with the input table defaulting to current rakes', () => {
  const win = makeWindow();
  const state = win.buildState();
  const totalRows = state.reduce((n, p) => n + p.sources.length, 0);

  assert.strictEqual(win.document.getElementById('diversionCalcWrap').style.display, 'none');
  win.document.getElementById('diversionCalcOpenBtn').click();

  assert.strictEqual(win.document.getElementById('diversionCalcWrap').style.display, 'block');
  const inputs = actualInputs(win);
  assert.strictEqual(inputs.length, totalRows, 'one input per plant-source pair');

  // every input starts blank (falls back to current rakes) with the current
  // rake count as its placeholder
  const first = state[0].sources[0];
  assert.strictEqual(inputs[0].value, '');
  assert.ok(inputs[0].placeholder.includes(String(first.currentRakes)), 'placeholder shows current rakes');

  // no optimizer suggestion before an official run
  const text = win.document.getElementById('diversionCalcBody').textContent;
  assert.ok(text.includes('Optimizer Suggestion'));
  assert.ok(text.includes('—') || !text.includes('Suggested'), 'no suggested values yet');
});

test('entering actual rakes flows into the calculate payload (empty = keep current)', () => {
  const win = makeWindow();
  win.document.getElementById('diversionCalcOpenBtn').click();
  const state = win.buildState();
  const firstKey = `${state[0].name}||${state[0].sources[0].name}`;
  const secondKey = `${state[1].name}||${state[1].sources[0].name}`;

  const inputs = actualInputs(win);
  inputs[0].value = '14';
  inputs[0].dispatchEvent(new win.Event('input', {bubbles: true}));
  inputs[1].value = '';
  inputs[1].dispatchEvent(new win.Event('input', {bubbles: true}));

  const payload = win.buildDiversionPayload();
  const flat = {};
  payload.plants.forEach(p => p.sources.forEach(s => {
    flat[`${p.plant}||${s.company}`] = s.rakes;
  }));

  assert.strictEqual(flat[firstKey], 14, 'entered value is sent');
  assert.strictEqual(flat[secondKey], state[1].sources[0].currentRakes, 'cleared row keeps its current rakes');

  // clearing the first input restores its current rakes too
  const inputsAfter = actualInputs(win);
  inputsAfter[0].value = '';
  inputsAfter[0].dispatchEvent(new win.Event('input', {bubbles: true}));
  const payload2 = win.buildDiversionPayload();
  const flat2 = {};
  payload2.plants.forEach(p => p.sources.forEach(s => { flat2[`${p.plant}||${s.company}`] = s.rakes; }));
  assert.strictEqual(flat2[firstKey], state[0].sources[0].currentRakes);
});

test('fractional actual rakes are rejected with a message, not rounded', () => {
  const win = makeWindow();
  win.document.getElementById('diversionCalcOpenBtn').click();
  const state = win.buildState();
  const firstKey = `${state[0].name}||${state[0].sources[0].name}`;
  const firstInput = actualInputs(win)[0];

  // a valid whole entry first
  firstInput.value = '15';
  firstInput.dispatchEvent(new win.Event('input', {bubbles: true}));
  assert.strictEqual(win.buildDiversionPayload().plants[0].sources[0].rakes, 15, 'whole entry stored');

  // now a fractional entry - must be rejected, reverted, and explained
  firstInput.value = '15.5';
  firstInput.dispatchEvent(new win.Event('input', {bubbles: true}));
  assert.strictEqual(firstInput.value, '15', 'input reverted to the last valid value');
  const notice = win.document.getElementById('rakeInputNotice');
  assert.strictEqual(notice.style.display, 'block', 'validation notice shown');
  assert.ok(notice.textContent.includes('whole number'), 'notice explains rakes must be whole');

  const payload = win.buildDiversionPayload();
  const flat = {};
  payload.plants.forEach(p => p.sources.forEach(s => { flat[`${p.plant}||${s.company}`] = s.rakes; }));
  assert.strictEqual(flat[firstKey], 15, 'no fractional rake ever entered the payload');
});

function fakeOptimizeResponse(win){
  const state = win.buildState();
  return {
    status: 'Optimal',
    weighted_vc_before: 4.2000,
    weighted_vc_after: 4.1000,
    vc_improvement: 0.1000,
    total_shutdowns: 0,
    plants: state.map(p => ({
      plant: p.name, rsd_threshold_vc: null, rsd_status: 'no_constraint',
      current_rakes: p.totalRakes, optimized_rakes: p.totalRakes,
      current_vc: 4.2000, optimized_vc: 4.1000, delta_vc: -0.1000,
      exceeded_threshold: false, threshold_margin: null,
    })),
    allocations: state.flatMap(p => p.sources.map(s => ({
      plant: p.name, company: s.name,
      current_rakes: s.currentRakes, optimized_rakes: s.currentRakes + 1,
      source_vc: s.currentVC, delta_rakes: 1, minRakes: 0, maxRakes: 999,
    }))),
    constraint_status: [],
    errors: [],
    message: null,
  };
}

test('seed button copies the official optimized allocation as the starting reference', async () => {
  const win = makeWindow(null, (url) => {
    const pathname = new URL(url).pathname;
    if(pathname === '/health') return Promise.resolve({ok: true, status: 200});
    if(pathname === '/optimize') return Promise.resolve({ok: true, status: 200, json: async () => fakeOptimizeResponse(win)});
    return Promise.resolve({ok: false, status: 404});
  });

  // run the official optimizer (stubbed) so lastOfficialResult exists
  win.document.getElementById('serverOptimizeBtn').click();
  await waitFor(() => win.document.getElementById('portfolioRakeTableWrap').style.display === 'block');

  win.document.getElementById('diversionCalcOpenBtn').click();
  const state = win.buildState();
  const suggested = state[0].sources[0].currentRakes + 1;

  // before seeding: the ACTUAL column is empty, but the reference column
  // already shows the official suggestion after the successful run
  let inputs = actualInputs(win);
  assert.strictEqual(inputs[0].value, '', 'actual column not seeded yet');
  let text = win.document.getElementById('diversionCalcBody').textContent;
  assert.ok(text.includes(String(suggested)), 'reference column already shows the official suggestion');

  win.document.getElementById('diversionCalcSeedBtn').click();

  // after seeding: the actual input carries the optimized value
  inputs = actualInputs(win);
  assert.strictEqual(inputs[0].value, String(suggested), 'Actual column seeded from optimizer');
  text = win.document.getElementById('diversionCalcBody').textContent;
  assert.ok(text.includes(String(suggested)), 'suggestion column shows the same value');

  const payload = win.buildDiversionPayload();
  const first = payload.plants[0].sources[0];
  assert.strictEqual(first.rakes, suggested, 'seeded value flows into the payload');
});

test('calculate renders plant-wise and overall VC from the backend response', async () => {
  const win = makeWindow();
  const state = win.buildState();
  const fakeResult = {
    status: 'ok',
    weighted_vc_current: 4.2000,
    weighted_vc_actual: 4.0500,
    vc_improvement: 0.1500,
    total_rakes_current: 200,
    total_rakes_actual: 205,
    plants: state.map(p => ({
      plant: p.name,
      current_rakes: p.totalRakes,
      actual_rakes: p.totalRakes,
      current_vc: 4.2000,
      actual_vc: 4.0500,
      delta_vc: -0.1500,
    })),
  };
  win.fetch = async () => ({ok: true, status: 200, json: async () => fakeResult});

  win.document.getElementById('diversionCalcOpenBtn').click();
  win.document.getElementById('diversionCalcBtn').click();

  await waitFor(() => {
    const t = win.document.getElementById('diversionCalcResults').textContent;
    return t.includes('Calculation result') && t.includes('Plant-wise VC');
  });
  const text = win.document.getElementById('diversionCalcResults').textContent;

  assert.ok(text.includes('Portfolio Avg VC'), 'overall VC cards shown');
  assert.ok(text.includes('4.2000') && text.includes('4.0500'), 'current and actual blended VC rendered');
  assert.ok(text.includes(state[0].name), 'plant-wise rows rendered');
  assert.ok(text.includes('205'), 'total actual rakes shown');

  // editing an input after a result drops the stale result
  const inputs = actualInputs(win);
  inputs[2].value = '1';
  inputs[2].dispatchEvent(new win.Event('input', {bubbles: true}));
  const after = win.document.getElementById('diversionCalcResults').textContent;
  assert.ok(after.includes('Re-run'), 'stale result replaced by a re-run hint');
});

test('end-to-end: entered actuals -> real backend -> rendered plant-wise and overall VC', async () => {
  const proc = spawn(PY, ['-m', 'uvicorn', 'main:app', '--port', String(PORT), '--log-level', 'warning'], {
    cwd: path.join(ROOT, 'backend'),
    stdio: 'ignore',
  });
  try{
    await waitFor(async () => {
      try{
        const r = await fetch(`${BASE}/health`);
        return r.ok;
      }catch(e){ return false; }
    });
  }catch(e){
    proc.kill();
    throw new Error('backend did not become healthy: ' + e.message);
  }

  try{
    const win = makeWindow(null, (url, opts) => fetch(url, opts), w => { w.OPTIMIZER_API_BASE = BASE; });
    win.document.getElementById('diversionCalcOpenBtn').click();
    const state = win.buildState();
    const firstPlant = state[0];
    const enteredValue = Math.round(firstPlant.sources[0].currentRakes) + 2;

    const inputs = actualInputs(win);
    const firstInput = inputs[0];
    firstInput.value = String(enteredValue);
    firstInput.dispatchEvent(new win.Event('input', {bubbles: true}));

    win.document.getElementById('diversionCalcBtn').click();
    await waitFor(() => {
      const t = win.document.getElementById('diversionCalcResults').textContent;
      return t.includes('Calculation result') && (t.includes('Portfolio Avg VC') || t.includes('could not be evaluated'));
    });
    const text = win.document.getElementById('diversionCalcResults').textContent;

    assert.ok(text.includes('Portfolio Avg VC'), 'backend result rendered');
    assert.ok(text.includes(firstPlant.name), 'plant-wise row for the edited plant');
    assert.ok(text.includes(String(enteredValue)), 'entered actual rakes echoed back');
    assert.ok(!text.includes('could not be evaluated'), 'no validation failure');
  }finally{
    proc.kill();
  }
});