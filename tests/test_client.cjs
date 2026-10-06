// Synthetic tests: no research files, external requests, or browser profiles.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates/notebook.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Syntax-check the complete portable template.
const seed = {
  format: 'recon-notebook', version: 1, target: 'synthetic.example', programUrl: '',
  sourceLabel: '', inventory: [], inventoryNote: '', routeNotes: '', updatedAt: null,
  features: [{id: 'feature-1', title: 'Synthetic profile', group: 'Profile & members', path: 'Profile', items: ['About'], note: '', custom: true,
    tests: [{id: 'test-1-1', type: 'XSS', title: 'Review rendering', detail: 'Trace an inert marker.', expected: 'Text stays inert.', ref: 'xss'}]}],
  featureNotes: {}, checks: {}, recaps: [], tasks: []
};
function node(tagName) {
  return {tagName, textContent: '', value: '', style: {}, dataset: {}, classList: {toggle() {}, remove() {}, add() {}},
    children: [], listeners: {}, append(...items) {this.children.push(...items);},
    attributes: {}, addEventListener(event, handler) {this.listeners[event] = handler;}, setAttribute(key, value) {this.attributes[key] = value;},
    replaceChildren(...items) {this.children = [...items];}, querySelector() {return null;},
    showModal() {this.open = true;}, close() {this.open = false;}, focus() {}, reportValidity() {return true;}, open: false};
}
const elements = new Map();
const get = id => {if (!elements.has(id)) elements.set(id, node()); return elements.get(id);};
let backend = structuredClone(seed), revision = 'revision-1', token = 'token-1', getHook = null, postHook = null;
const response = (status, data) => ({status, ok: status >= 200 && status < 300, json: async () => data});
const fetch = async (url, options = {}) => {
  assert.match(url, /^\/api\/notebook\?file=/);
  if (options.method === 'POST') {
    if (postHook) await postHook();
    if (options.headers['X-Notebook-Token'] !== token) return response(403, {error: 'Launcher restarted'});
    if (options.headers['If-Match'] !== revision) return response(409, {error: 'The JSON changed in another browser'});
    backend = JSON.parse(options.body);
    revision = 'revision-' + (Number(revision.split('-')[1]) + 1);
    return response(200, {revision});
  }
  const data = {state: structuredClone(backend), revision, token, database: 'synthetic/notebook.json', created: false};
  if (getHook) await getHook();
  return response(200, data);
};
const sandbox = {
  structuredClone, Date,
  location: {pathname: '/synthetic/notebook.html', protocol: 'http:', hostname: '127.0.0.1'},
  fetch, document: {getElementById: get, createElement: node, createTextNode: text => ({textContent: text}), querySelectorAll: () => [], title: ''},
  localStorage: {getItem: () => null}, setTimeout: () => 0, clearTimeout() {}
};
vm.createContext(sandbox);
vm.runInContext(script.split('    function toast(message)')[0] + `
  function renderActive() {} function refreshCounts() {} function toast() {}
  globalThis.api = {validateBackup, initialState, loadDisk, save, flushSave, syncDisk,
    state: () => state, dirty: () => dirtyVersion > savedVersion,
    problem: () => ({storageIssue, conflict}), setNote: text => state.featureNotes['feature-1'] = text};
`, sandbox);
const api = sandbox.api;

(async () => {
  await api.loadDisk();
  assert.equal(api.state().target, 'synthetic.example');
  assert.equal(api.initialState().features.length, 0);
  api.setNote('Stored in the adjacent JSON'); api.save(); await api.flushSave();
  assert.equal(backend.featureNotes['feature-1'], 'Stored in the adjacent JSON');
  assert.equal(api.dirty(), false);

  let releasePost;
  postHook = () => new Promise(resolve => releasePost = resolve);
  api.setNote('First edit'); api.save(); const write = api.flushSave();
  api.setNote('Second edit while saving'); api.save(); postHook = null; releasePost(); await write;
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(backend.featureNotes['feature-1'], 'Second edit while saving');
  assert.equal(api.dirty(), false);

  let releaseGet;
  getHook = () => new Promise(resolve => releaseGet = resolve);
  const poll = api.syncDisk(); api.setNote('Typed while polling'); api.save(); getHook = null; releaseGet(); await poll;
  assert.equal(api.state().featureNotes['feature-1'], 'Typed while polling'); await api.flushSave();

  token = 'new-launcher-token'; api.setNote('Edit after restart'); api.save(); await api.flushSave();
  assert.equal(backend.featureNotes['feature-1'], 'Edit after restart'); assert.equal(api.dirty(), false);

  backend.featureNotes['feature-1'] = 'Newer note from browser B'; revision = 'revision-99';
  api.setNote('Unsaved note from browser A'); api.save(); await api.flushSave();
  assert.equal(backend.featureNotes['feature-1'], 'Newer note from browser B');
  assert.equal(api.state().featureNotes['feature-1'], 'Unsaved note from browser A');
  assert.equal(api.problem().conflict, true); assert.equal(api.dirty(), true);
  await api.loadDisk(); assert.equal(api.state().featureNotes['feature-1'], 'Newer note from browser B');

  const bad = structuredClone(seed); bad.checks['test-1-1'] = {status: 'invalid', result: '', updatedAt: ''};
  assert.throws(() => api.validateBackup(bad));
  const orphan = structuredClone(seed); orphan.featureNotes['unknown-feature'] = 'orphan';
  assert.throws(() => api.validateBackup(orphan));
  const duplicate = structuredClone(seed); duplicate.features.push(duplicate.features[0]);
  assert.throws(() => api.validateBackup(duplicate));
  const roundtrip = structuredClone(seed); roundtrip.featureNotes['feature-1'] = '<img src=x onerror=alert(1)>\nsource → sink';
  assert.equal(api.validateBackup(JSON.parse(JSON.stringify(roundtrip))).featureNotes['feature-1'], roundtrip.featureNotes['feature-1']);
  assert.ok(!/\.innerHTML\s*=/.test(script));

  // Use the actual editor submit handlers and rendered buttons for check/tag CRUD.
  const editorElements = new Map();
  const editorGet = id => {if (!editorElements.has(id)) editorElements.set(id, node()); return editorElements.get(id);};
  const flatten = item => [item, ...(item.children || []).flatMap(flatten)];
  const editorSandbox = {
    structuredClone, Date, location: sandbox.location, setTimeout: () => 0, clearTimeout() {},
    document: {getElementById: editorGet, createElement: node, createTextNode: text => ({textContent: text}), querySelectorAll: () => [], title: ''},
    FormData: class {entries() {return flatten(editorGet('editor-fields')).filter(item => item.name).map(item => [item.name, item.value]);}}
  };
  vm.createContext(editorSandbox);
  vm.runInContext(script.split("    document.querySelectorAll('[data-close]')")[0] + `
    state = validateBackup(${JSON.stringify(seed)}); loaded = true; activeView = 'features';
    state.featureNotes['feature-1'] = 'Keep these JS leads';
    state.recaps = [{id:'recap-one', date:'2026-10-06', title:'Saved session', featureId:'feature-1', focus:'Review', summary:'Keep this recap', findings:'', evidence:'', next:'', notes:''}];
    state.tasks = [{id:'task-one', title:'Keep this next check', featureId:'feature-1', priority:'medium', status:'todo', notes:'', createdAt:'2026-10-06T00:00:00Z'}];
    $('feature-status').value = 'all'; $('test-focus').value = 'all';
    globalThis.editorApi = {openTest, openTags, openFeature, renderFeatures, validateBackup,
      state: () => state, recordResult: () => state.checks['test-1-1'] = {status:'lead', result:'Saved evidence', updatedAt:'2026-10-06T00:00:00Z'}};
  `, editorSandbox);
  const editorApi = editorSandbox.editorApi;
  const field = name => flatten(editorGet('editor-fields')).find(item => item.name === name);
  const submit = () => editorGet('editor-form').listeners.submit({preventDefault() {}});
  const clickRendered = text => {
    const target = flatten(editorGet('features')).find(item => item.tagName === 'button' && item.textContent === text);
    assert.ok(target, 'Rendered button: ' + text); target.listeners.click();
  };
  editorApi.recordResult(); editorApi.renderFeatures(); clickRendered('Edit check');
  assert.equal(field('title').value, 'Review rendering'); assert.equal(field('ref').value, 'xss');
  field('title').value = 'Updated rendering check'; field('type').value = 'OAuth'; field('ref').value = 'oauth'; submit();
  assert.equal(editorApi.state().features[0].tests[0].id, 'test-1-1');
  assert.equal(editorApi.state().features[0].tests[0].type, 'OAuth');
  assert.equal(editorApi.state().checks['test-1-1'].result, 'Saved evidence');
  assert.equal(editorApi.state().checks['test-1-1'].status, 'lead');
  clickRendered('Edit tags'); field('tags').value = 'OAuth, JS review, OAuth'; submit();
  assert.equal(editorApi.state().features[0].tags.join(', '), 'OAuth, JS review');
  editorApi.openFeature('feature-1'); field('title').value = 'Renamed profile'; submit();
  assert.equal(editorApi.state().features[0].tags.join(', '), 'OAuth, JS review');
  assert.equal(editorApi.state().features[0].tests[0].title, 'Updated rendering check');
  clickRendered('Delete check');
  assert.equal(editorApi.state().features[0].tests.length, 1, 'Delete requires confirmation');
  editorGet('confirm-accept').listeners.click();
  assert.equal(editorApi.state().features[0].tests.length, 0); assert.equal(editorApi.state().checks['test-1-1'], undefined);
  assert.equal(editorApi.state().featureNotes['feature-1'], 'Keep these JS leads');
  assert.equal(editorApi.state().recaps[0].featureId, 'feature-1');
  assert.equal(editorApi.state().tasks[0].title, 'Keep this next check');
  assert.equal(editorApi.state().features[0].tags.join(', '), 'OAuth, JS review', 'Independent tags survive check deletion');
  clickRendered('+ Add check'); field('title').value = 'New manual check'; field('detail').value = 'Read the implementation'; field('expected').value = 'Correct binding'; submit();
  assert.equal(editorApi.state().features[0].tests.length, 1);
  assert.equal(editorApi.state().features[0].tests[0].title, 'New manual check');
  editorApi.openTags('feature-1'); field('tags').value = 'a'.repeat(101); submit();
  assert.equal(editorGet('editor-error').hidden, false); assert.equal(editorApi.state().features[0].tags.join(', '), 'OAuth, JS review');
  const badTags = structuredClone(seed); badTags.features[0].tags = ['XSS', 'XSS'];
  assert.throws(() => editorApi.validateBackup(badTags));
  editorApi.validateBackup(editorApi.state());

  // Exercise the creator form and its success navigation with a fake local API.
  const creatorElements = new Map();
  const creatorGet = id => {if (!creatorElements.has(id)) creatorElements.set(id, node()); return creatorElements.get(id);};
  let destination = '', createdPayload = null;
  const creatorSandbox = {
    Date, encodeURIComponent,
    document: {getElementById: creatorGet, createElement: node, createTextNode: text => ({textContent: text})},
    window: {location: {assign: value => destination = value}},
    fetch: async (url, options = {}) => {
      assert.equal(url, '/api/notebooks');
      if (options.method === 'POST') {createdPayload = JSON.parse(options.body); return response(201, {path: createdPayload.name + '/notebook.html'});}
      return response(200, {notebooks: [], token: 'creator-token'});
    }
  };
  vm.createContext(creatorSandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/creator.js'), 'utf8'), creatorSandbox);
  for (let i = 0; i < 5; i++) await Promise.resolve();
  creatorGet('target').value = 'synthetic.example'; creatorGet('target').listeners.input();
  assert.equal(creatorGet('name').value, 'synthetic-example');
  creatorGet('name').value = 'custom-folder'; creatorGet('name').listeners.input();
  creatorGet('target').value = 'second.example'; creatorGet('target').listeners.input();
  assert.equal(creatorGet('name').value, 'custom-folder');
  creatorGet('program-url').value = 'https://example.org/program';
  await creatorGet('create-form').listeners.submit({preventDefault() {}});
  assert.equal(createdPayload.target, 'second.example');
  assert.equal(destination, '/custom-folder/notebook.html');

  console.log('PASS: JSON save queue, browser conflicts, backup validation, check edit/delete/add, preserved results, editable tags, inert notes, and creator form navigation.');
})().catch(error => {console.error(error); process.exitCode = 1;});
