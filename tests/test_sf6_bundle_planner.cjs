// Run with: node --test tests/test_sf6_bundle_planner.cjs
// Exercise the standalone page's planning functions without a browser dependency.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const page = readFileSync(join(__dirname, '../docs/sf6-bundle-planner.html'), 'utf8');
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
const core = script.split('/* ---------- events ---------- */')[0];

function planner() {
  const context = vm.createContext({localStorage: {getItem: () => null}});
  vm.runInContext(core + '\nglobalThis.planner = {state, buildPlan, modinfo, treeAfter, renderTree, warnings, gamePath, category};\n})();', context);
  return context.planner;
}

test('flat bundle uses separate folders and a shared bundle name', () => {
  const p = planner();
  const plan = p.buildPlan();
  assert.equal(plan.length, 2);
  assert.notEqual(plan[0].folder, plan[1].folder);
  for (const step of plan) {
    assert.equal(step.kind, 'mesh');
    assert.equal(step.parent, '');
    assert.equal(Object.fromEntries(p.modinfo(step)).nameasbundle, 'Ingrid C1 Hair Collection');
  }
  assert.equal(p.warnings(plan).length, 0);
});

test('nested recipe links three menus and four mesh options by display name', () => {
  const p = planner();
  p.state.mode = 'nested';
  const plan = p.buildPlan();
  assert.equal(plan.length, 7);
  assert.equal(plan.filter(s => s.kind === 'menu').length, 3);
  assert.equal(plan[1].parent, plan[0].display);
  assert.equal(plan[2].parent, plan[1].display);
  assert.equal(plan[5].parent, plan[4].display);
  assert.equal(Object.fromEntries(p.modinfo(plan[0])).dummymod, 'True');
  assert.equal(Object.fromEntries(p.modinfo(plan[2])).dummymod, undefined);
  assert.equal(p.treeAfter(plan, 6)[0].kids.length, 2);
});

test('all part paths retain the character and three-digit costume', () => {
  const p = planner();
  p.state.character = '004';
  p.state.costume = '002';
  assert.equal(p.category(), '!Characters > Chun-Li');
  for (const slot of ['00', '01', '02']) {
    assert.equal(p.gamePath(slot), `natives\\stm\\product\\model\\esf\\esf004\\002\\${slot}\\esf004_002_${slot}.mesh.230110883`);
  }
});

test('INI names and parent links trim surrounding whitespace like the exporter', () => {
  const p = planner();
  p.state.mode = 'nested';
  p.state.packName = ' Pack ';
  p.state.groups[0].name = ' Hair Options ';
  p.state.groups[0].vars[0] = ' Braid ';
  const plan = p.buildPlan();
  assert.equal(plan[1].parent, 'Pack');
  assert.equal(plan[2].parent, 'Hair Options');
  assert.equal(Object.fromEntries(p.modinfo(plan[2])).name, 'Braid');
  assert.equal(p.treeAfter(plan, 2)[0].kids[0].kids[0].key, 'Braid');
});

test('object prototype names remain ordinary variant and menu names', () => {
  const p = planner();
  p.state.bundleName = 'constructor';
  p.state.bundleVars = ['__proto__', 'toString'];
  const plan = p.buildPlan();
  const html = p.renderTree(p.treeAfter(plan, 1), false);
  assert.ok(html.includes('__proto__'));
  assert.ok(html.includes('toString'));
  assert.equal(p.warnings(plan).length, 0);
});

test('duplicate names, repeated folders and self-parent links produce warnings', () => {
  const p = planner();
  p.state.bundleVars = ['Braid', 'braid'];
  let messages = p.warnings(p.buildPlan()).join('\n');
  assert.ok(messages.includes('Two entries'));
  assert.ok(messages.includes('Two exports use the folder'));
  p.state.mode = 'nested';
  p.state.groups[0].name = p.state.packName;
  messages = p.warnings(p.buildPlan()).join('\n');
  assert.ok(messages.includes('same name as the menu'));
});

test('invalid folder characters and trailing dots are flagged', () => {
  const p = planner();
  p.state.bundleVars = ['Braid?', 'Bangs.'];
  const messages = p.warnings(p.buildPlan()).join('\n');
  assert.ok(messages.includes('Windows will not accept'));
  assert.ok(messages.includes('trailing dot'));
});

test('preview names are escaped as text', () => {
  const p = planner();
  p.state.bundleVars = ['<b>Hair</b> & "Bangs"'];
  const html = p.renderTree(p.treeAfter(p.buildPlan(), 0), false);
  assert.ok(html.includes('&lt;b&gt;Hair&lt;/b&gt; &amp; &quot;Bangs&quot;'));
  assert.ok(!html.includes('<b>Hair</b>'));
});

test('cyclic preview input cannot recurse indefinitely', () => {
  const p = planner();
  const menu = {key: 'Menu', kind: 'menu', kids: []};
  menu.kids.push(menu);
  assert.equal((p.renderTree([menu], false).match(/class="tnode menu"/g) || []).length, 1);
});
