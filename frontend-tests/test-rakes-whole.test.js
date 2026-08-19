const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = fs.readFileSync(path.join(ROOT, 'dashboard_template.html'), 'utf8');

function makeWindow() {
  const dom = new JSDOM(TEMPLATE, {
    url: 'https://example.com/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
  });
  return dom.window;
}

function firstPlant(win) {
  return win.buildState()[0];
}

test('default rake values in working state are whole numbers', () => {
  const win = makeWindow();
  for (const p of win.buildState()) {
    assert.ok(Number.isInteger(p.totalRakes), `${p.name} total ${p.totalRakes} is not whole`);
    for (const s of p.sources) {
      assert.ok(Number.isInteger(s.currentRakes), `${p.name}/${s.name} currentRakes ${s.currentRakes} is not whole`);
    }
  }
});

test('optimize payload sends whole current_rakes, minRakes and maxRakes', () => {
  const win = makeWindow();
  const payload = win.buildOptimizePayload();
  for (const p of payload.plants) {
    for (const s of p.sources) {
      assert.ok(Number.isInteger(s.current_rakes), `${p.plant}/${s.company} current_rakes=${s.current_rakes}`);
      assert.ok(Number.isInteger(s.minRakes), `${p.plant}/${s.company} minRakes=${s.minRakes}`);
      assert.ok(Number.isInteger(s.maxRakes), `${p.plant}/${s.company} maxRakes=${s.maxRakes}`);
      assert.ok(s.minRakes >= 0 && s.maxRakes >= s.minRakes, `${p.plant}/${s.company} bounds sane`);
    }
  }
});

test('fractional Current Rakes input is rejected, reverted, and explained', () => {
  const win = makeWindow();
  const plant = firstPlant(win);
  const src = plant.sources[0];
  const input = win.document.querySelector('#plantPanel input[data-field="currentRakes"]');
  assert.ok(input, 'current rakes input exists in the active plant panel');

  const validBefore = plant.sources[0].currentRakes;

  input.value = '10.5';
  input.dispatchEvent(new win.Event('input', { bubbles: true }));

  const notice = win.document.getElementById('rakeInputNotice');
  assert.strictEqual(notice.style.display, 'block', 'validation notice shown');
  assert.ok(notice.textContent.includes('whole number'), 'notice says rakes must be whole');

  const fresh = win.document.querySelector('#plantPanel input[data-field="currentRakes"]');
  assert.strictEqual(parseFloat(fresh.value), validBefore, 'state keeps its last valid value and the input re-renders to it');
  assert.strictEqual(firstPlant(win).sources[0].currentRakes, validBefore, 'no fractional rake ever entered state');

  const payload = win.buildOptimizePayload();
  assert.ok(payload.plants[0].sources.every(s => Number.isInteger(s.current_rakes)), 'payload rakes stay whole');
});

test('preview "applyOptimizedToPlant" writes whole rakes and conserves the plant total', () => {
  const win = makeWindow();
  const plant = firstPlant(win);
  const before = plant.sources.reduce((a, s) => a + s.currentRakes, 0);
  const ok = win.applyOptimizedToPlant(plant);
  assert.ok(ok, 'preview plant mix was feasible');

  const after = plant.sources.reduce((a, s) => a + s.currentRakes, 0);
  assert.strictEqual(after, before, 'plant total conserved by the whole-rake preview');
  for (const s of plant.sources) {
    assert.ok(Number.isInteger(s.currentRakes), `${s.name} preview rakes ${s.currentRakes} not whole`);
    assert.ok(s.currentRakes >= 0, `${s.name} preview rakes negative`);
  }
});

test('official result rake figures render as whole numbers (no trailing decimals)', () => {
  const win = makeWindow();
  const state = win.buildState();
  win.renderServerOptimizeResult({
    status: 'Optimal',
    weighted_vc_before: 4.2000,
    weighted_vc_after: 4.1000,
    vc_improvement: 0.1000,
    total_shutdowns: 0,
    plants: state.map(p => ({
      plant: p.name, rsd_threshold_vc: null, rsd_status: 'no_constraint',
      current_rakes: 52, optimized_rakes: 54,
      current_vc: 4.2000, optimized_vc: 4.1000, delta_vc: -0.1000,
      exceeded_threshold: false, threshold_margin: null,
    })),
    allocations: state[0].sources.map((s, i) => ({
      plant: state[0].name, company: s.name,
      current_rakes: 30 + i, optimized_rakes: 31 + i,
      source_vc: 4.2000, delta_rakes: 1, minRakes: 0, maxRakes: 50,
    })),
    constraint_status: [],
    errors: [],
    message: null,
  });
  const text = win.document.getElementById('serverOptimizeBody').textContent;
  assert.ok(!text.includes('52.0'), 'current rakes rendered as "52", not "52.0"');
  assert.ok(!text.includes('31.0'), 'optimized rakes rendered as whole numbers');
  assert.ok(text.includes('+1'), 'delta rakes rendered as a whole number');
});