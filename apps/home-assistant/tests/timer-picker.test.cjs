const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../config/www/airtouch-timer-picker.js'), 'utf8');
const flush = async () => { for (let n = 0; n < 15; n++) await Promise.resolve(); };

function fixture({ attached = true, hours = 1, minutes = 0, service } = {}) {
  let now = 0, nextId = 0;
  const timeouts = new Map();
  const clock = {
    setTimeout(fn, delay) { const id = ++nextId; timeouts.set(id, { fn, due: now + delay }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    tick(ms) {
      const target = now + ms;
      while (true) {
        const entry = [...timeouts].filter(([, t]) => t.due <= target).sort((a, b) => a[1].due - b[1].due)[0];
        if (!entry) break;
        timeouts.delete(entry[0]); now = entry[1].due; entry[1].fn();
      }
      now = target;
    },
  };
  class Element {
    constructor() { this.listeners = {}; this.style = {}; this.attributes = {}; this.textContent = ''; }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    fire(type, data = {}) { this.listeners[type]?.(data); }
    setAttribute(key, value) { this.attributes[key] = value; }
  }
  class Wheel extends Element {
    constructor(host, values) {
      super(); this.host = host; this.scrollTop = 0;
      this.options = values.map(value => {
        const option = new Element(); option.dataset = { value: String(value) };
        option.classList = { toggle: (_, value) => option.selected = value };
        return option;
      });
    }
    get clientHeight() { return this.host.isConnected && !this.host.hidden ? 176 : 0; }
    scrollTo({ top }) { if (this.clientHeight) this.scrollTop = top; }
    querySelectorAll() { return this.options; }
  }
  class HTMLElement {
    constructor() { this.isConnected = false; }
    attachShadow() {
      const map = {};
      for (const id of ['start', 'cancel', 'error', 'status']) map[`#${id}`] = new Element();
      for (const [kind, values] of [['hours', [0,1,2,3,4,5,6,7,8]], ['minutes', [0,15,30,45]]]) map[`[data-wheel="${kind}"]`] = new Wheel(this, values);
      this.shadowRoot = { querySelector: key => map[key], set innerHTML(_) {} };
      return this.shadowRoot;
    }
  }
  const registry = new Map();
  const context = vm.createContext({
    HTMLElement, window: {}, customElements: { define: (key, value) => registry.set(key, value), get: key => registry.get(key) },
    ResizeObserver: class { constructor(fn) { this.fn = fn; } observe() {} disconnect() {} },
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: () => ++nextId, clearInterval() {},
  });
  vm.runInContext(`{${source}}`, context);
  const Card = registry.get('airtouch-timer-picker');
  const card = new Card(); const config = Card.getStubConfig(); card.setConfig(config);
  const states = {
    [config.hours_entity]: { state: String(hours) }, [config.minutes_entity]: { state: String(minutes) },
    [config.timer_entity]: { state: 'idle', attributes: {} }, [config.climate_entity]: { state: 'off' },
  };
  const calls = [];
  const hass = { states, callService: async (...args) => { calls.push(args); return service?.(...args); } };
  card.hass = hass;
  if (attached) { card.isConnected = true; card.connectedCallback(); }
  const wheel = kind => card.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
  const element = id => card.shadowRoot.querySelector(`#${id}`);
  const update = (kind, value) => { states[config[`${kind}_entity`]].state = String(value); card.hass = { ...hass }; };
  return { card, config, states, calls, clock, hass, wheel, element, update, context };
}

function selected(wheel) { return Number(wheel.options.find(x => x.selected)?.dataset.value); }

function centered(f, kind, value) {
  const values = kind === 'hours' ? [0,1,2,3,4,5,6,7,8] : [0,15,30,45];
  assert.equal(f.wheel(kind).scrollTop, values.indexOf(value) * 44);
  assert.equal(selected(f.wheel(kind)), value);
}

test('initial HA state assigned before attachment is centred after mounting', () => {
  const f = fixture({ attached: false, hours: 3, minutes: 45 });
  assert.equal(f.wheel('hours').scrollTop, 0);
  f.card.isConnected = true; f.card.connectedCallback();
  centered(f, 'hours', 3); centered(f, 'minutes', 45);
  assert.equal(f.calls.length, 0);
});

test('hidden dashboard realigns on visibility even when selected value is unchanged', () => {
  const f = fixture();
  f.card.hidden = true; f.update('hours', 5); f.update('minutes', 30);
  f.card.hidden = false; f.card._resizeObserver.fn();
  centered(f, 'hours', 5); centered(f, 'minutes', 30);
});

test('remount cleans up interaction state and restores current backend values', () => {
  const f = fixture(); f.wheel('hours').fire('pointerdown');
  f.card.disconnectedCallback(); f.card.isConnected = false;
  f.update('hours', 6); f.card.isConnected = true; f.card.connectedCallback();
  centered(f, 'hours', 6);
});

test('Start freezes a moving wheel and sends its visible duration directly', async () => {
  const f = fixture(); const wheel = f.wheel('hours');
  wheel.fire('wheel'); wheel.scrollTop = 132; wheel.fire('scroll');
  await f.card._runAction(true); await flush();
  const start = f.calls.find(x => x[0] === 'script');
  assert.equal(start[1], 'airtouch_turn_on_with_timer');
  assert.equal(start[2].duration_minutes, 180);
  centered(f, 'hours', 3);
});

test('programmatic scroll events never persist an intermediate value', async () => {
  const f = fixture(); f.update('hours', 4);
  f.wheel('hours').scrollTop = 44; f.wheel('hours').fire('scroll');
  f.card.hass = f.hass; await flush();
  centered(f, 'hours', 4); assert.equal(f.calls.length, 0);
});

test('a long touch is protected until release, then commits its final value', async () => {
  const f = fixture(); const wheel = f.wheel('hours');
  wheel.fire('pointerdown'); f.clock.tick(1500);
  wheel.scrollTop = 176; wheel.fire('scroll'); f.card.hass = f.hass;
  assert.equal(wheel.scrollTop, 176);
  wheel.fire('pointerup'); wheel.fire('scrollend'); await flush();
  assert.equal(f.calls[0][2].value, 4);
});

test('wheel at list boundary does not remain locked against external changes', () => {
  const f = fixture({ hours: 0 }); f.wheel('hours').fire('wheel'); f.clock.tick(250);
  f.update('hours', 2); // The pending helper write is still optimistic until acknowledged.
  f.update('hours', 0); f.update('hours', 2);
  centered(f, 'hours', 2);
});

test('failed helper write restores backend value and shows error', async () => {
  const f = fixture({ service: () => Promise.reject(new Error('Save failed')) });
  f.wheel('hours').options[4].fire('click'); await flush();
  centered(f, 'hours', 1); assert.equal(f.element('error').textContent, 'Save failed');
});

test('lost acknowledgement cannot block backend synchronization indefinitely', async () => {
  const f = fixture(); f.wheel('hours').options[4].fire('click'); await flush();
  f.update('hours', 2); centered(f, 'hours', 4);
  f.clock.tick(2100); centered(f, 'hours', 2);
});

test('rapid helper writes are serialized and the latest selection wins', async () => {
  let finish; let pending = true;
  const f = fixture({ service: () => pending ? new Promise(r => finish = r) : Promise.resolve() });
  f.wheel('hours').options[2].fire('click'); f.wheel('hours').options[5].fire('click'); await flush();
  assert.equal(f.calls.length, 1);
  f.update('hours', 2); centered(f, 'hours', 5);
  pending = false; finish(); await flush();
  assert.equal(f.calls.length, 2); assert.equal(f.calls[1][2].value, 5);
  f.update('hours', 5); centered(f, 'hours', 5);
});

test('keyboard navigation changes one value and clamps to endpoints', async () => {
  const f = fixture(); const wheel = f.wheel('minutes');
  const key = name => wheel.fire('keydown', { key: name, preventDefault() {} });
  key('ArrowDown'); centered(f, 'minutes', 15);
  key('End'); centered(f, 'minutes', 45);
  key('ArrowDown'); centered(f, 'minutes', 45);
  key('Home'); centered(f, 'minutes', 0); await flush();
});

test('zero duration is disabled and cannot start the system', async () => {
  const f = fixture({ hours: 0, minutes: 0 });
  assert.equal(f.element('start').disabled, true);
  await f.card._runAction(true);
  assert.equal(f.calls.length, 0); assert.match(f.element('error').textContent, /15 minutes/);
});

test('pending start is protected against duplicate clicks and errors are shown', async () => {
  let reject;
  const f = fixture({ service: () => new Promise((_, r) => reject = r) });
  const action = f.card._runAction(true); await f.card._runAction(true);
  assert.equal(f.calls.length, 1); assert.equal(f.element('start').disabled, true);
  reject(new Error('No confirmation')); await action;
  assert.equal(f.element('error').textContent, 'No confirmation');
  assert.equal(f.element('start').disabled, false);
});

test('unavailable controller disables Start, and paused/unavailable timer is explicit', () => {
  const f = fixture(); f.states[f.config.climate_entity].state = 'unavailable'; f.card.hass = f.hass;
  assert.equal(f.element('start').disabled, true);
  f.states[f.config.timer_entity] = { state: 'paused', attributes: { remaining: '0:30:00' } }; f.card.hass = f.hass;
  assert.match(f.element('status').textContent, /Paused/);
  delete f.states[f.config.timer_entity]; f.card.hass = f.hass;
  assert.match(f.element('status').textContent, /Unavailable/);
});

test('reloading the module does not throw duplicate custom element errors', () => {
  const f = fixture(); vm.runInContext(`{${source}}`, f.context);
});

test('an expired timer awaiting shutdown is not displayed as idle', () => {
  const f = fixture();
  f.states[f.config.armed_entity] = { state: 'on' }; f.card.hass = f.hass;
  assert.match(f.element('status').textContent, /waiting for confirmed shutdown/);
});

test('releasing the mouse outside the wheel does not lock synchronization', async () => {
  const f = fixture();
  f.wheel('hours').fire('pointerdown');
  f.wheel('hours').fire('pointerleave', { pointerType: 'mouse' });
  f.clock.tick(250); await flush(); f.clock.tick(2100);
  f.update('hours', 4); centered(f, 'hours', 4);
});

test('Start enablement follows the visible value during scrolling from zero', () => {
  const f = fixture({ hours: 0 }); const wheel = f.wheel('hours');
  assert.equal(f.element('start').disabled, true);
  wheel.fire('wheel'); wheel.scrollTop = 44; wheel.fire('scroll');
  assert.equal(f.element('start').disabled, false);
  wheel.scrollTop = 0; wheel.fire('scroll');
  assert.equal(f.element('start').disabled, true);
});
