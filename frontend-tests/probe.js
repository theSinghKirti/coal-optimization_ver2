const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');

const TEMPLATE = fs.readFileSync(path.join(__dirname, '..', 'dashboard_template.html'), 'utf8');

const errors = [];
const dom = new JSDOM(TEMPLATE, {
  url: 'https://example.com/',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole: new (require('jsdom').VirtualConsole)(),
});
const win = dom.window;
win.addEventListener('error', e => { errors.push(String(e.message)); });
dom.virtualConsole.on('jsdomError', e => { errors.push('jsdomError: ' + e.message); });

setTimeout(() => {
  console.log('window.renderServerOptimizeResult:', typeof win.renderServerOptimizeResult);
  console.log('window.buildOptimizePayload:', typeof win.buildOptimizePayload);
  console.log('window.buildState:', typeof win.buildState);
  const payload = win.buildOptimizePayload();
  console.log('payload plants:', payload.plants.length);
  console.log('first plant keys:', Object.keys(payload.plants[0]));
  console.log('errors:', JSON.stringify(errors.slice(0, 5)));
  console.log('serverOptimizeBody rendered:', !!win.document.getElementById('serverOptimizeBody'));
}, 200);
