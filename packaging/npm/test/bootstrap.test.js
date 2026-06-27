'use strict';
const test = require('node:test');
const assert = require('node:assert');
const b = require('../lib/bootstrap');

test('needsBootstrap: false only when the marker matches the version', () => {
  // marker matches -> no bootstrap
  assert.strictEqual(
    b.needsBootstrap({ markerFile: '/m', version: '0.0.1', readFileSync: () => '0.0.1\n' }),
    false,
  );
  // marker missing -> bootstrap
  assert.strictEqual(
    b.needsBootstrap({ markerFile: '/m', version: '0.0.1', readFileSync: () => { throw new Error('ENOENT'); } }),
    true,
  );
  // marker is a different version -> bootstrap (version skew)
  assert.strictEqual(
    b.needsBootstrap({ markerFile: '/m', version: '0.0.2', readFileSync: () => '0.0.1' }),
    true,
  );
});

test('ensureUv: no install when uv is present', () => {
  const calls = [];
  const run = (cmd, args) => { calls.push([cmd, ...args]); return { status: 0 }; };
  b.ensureUv({ run, log: () => {} });
  assert.deepStrictEqual(calls, [['uv', '--version']]);  // probed, not installed
});

test('ensureUv: installs uv when absent, throws if install fails', () => {
  const run = () => ({ status: 1 });  // uv missing AND installer fails
  assert.throws(() => b.ensureUv({ run, log: () => {} }), /install.*uv/i);
});

test('ensureUv: installs uv when absent and succeeds silently', () => {
  const calls = [];
  const run = (cmd) => { calls.push(cmd); return { status: cmd === 'uv' ? 1 : 0 }; };
  assert.doesNotThrow(() => b.ensureUv({ run, log: () => {}, platform: 'darwin' }));
  assert.ok(calls.includes('sh'));
});

test('ensureUv: uses the PowerShell installer on Windows', () => {
  const calls = [];
  const run = (cmd) => { calls.push(cmd); return { status: cmd === 'uv' ? 1 : 0 }; };
  assert.doesNotThrow(() => b.ensureUv({ run, log: () => {}, platform: 'win32' }));
  assert.ok(calls.includes('powershell') && !calls.includes('sh'));
});

test('installPinned: installs yohoho==version from PyPI with --force (reconciles version skew)', () => {
  const calls = [];
  const run = (cmd, args) => { calls.push([cmd, ...args]); return { status: 0 }; };
  b.installPinned({ version: '0.0.1', run, log: () => {} });
  // --force so a marker-detected version skew actually REPLACES the installed version
  // instead of uv reporting "already installed" and exiting non-zero (-> false GitHub hint).
  // --refresh so uv's cached index never shadows a freshly-published version.
  assert.deepStrictEqual(calls, [['uv', 'tool', 'install', '--force', '--refresh', 'yohoho==0.0.1']]);
});

test('installPinned: failure surfaces uv\'s real stderr', () => {
  const run = () => ({ status: 1, stderr: 'No solution found: no version of yohoho==9.9.9' });
  assert.throws(() => b.installPinned({ version: '9.9.9', run, log: () => {} }),
    /No solution found/);
});

test('installPinned: failure throws with the GitHub fallback command', () => {
  const run = () => ({ status: 1 });
  assert.throws(() => b.installPinned({ version: '0.0.1', run, log: () => {} }),
    /git\+https:\/\/github\.com\/by-k4n\/yohoho\.git@v0\.0\.1/);
});

test('writeMarker: mkdirs the dir and writes the version', () => {
  const made = []; const wrote = [];
  b.writeMarker({
    markerFile: '/c/yohoho/installed-version', version: '0.0.1',
    mkdirSync: (d, o) => made.push([d, o]), writeFileSync: (f, v) => wrote.push([f, v]),
  });
  assert.deepStrictEqual(made, [['/c/yohoho', { recursive: true }]]);
  assert.deepStrictEqual(wrote, [['/c/yohoho/installed-version', '0.0.1']]);
});

test('uvToolBin: uses uv output, falls back to ~/.local/bin', () => {
  const ok = b.uvToolBin({ run: () => ({ status: 0, stdout: '/Users/x/.cargo/bin\n' }), homedir: '/Users/x' });
  assert.strictEqual(ok, '/Users/x/.cargo/bin');
  const fallback = b.uvToolBin({ run: () => ({ status: 1, stdout: '' }), homedir: '/Users/x' });
  assert.strictEqual(fallback, '/Users/x/.local/bin');
});

test('withBinOnPath: prepends the bin dir to PATH', () => {
  const env = b.withBinOnPath({ PATH: '/usr/bin' }, '/Users/x/.local/bin');
  assert.strictEqual(env.PATH, '/Users/x/.local/bin:/usr/bin');
  assert.strictEqual(b.withBinOnPath({}, '/bin').PATH, '/bin');  // no trailing colon when PATH is unset
});

function harness(overrides = {}) {
  const calls = { run: [], spawn: [], logs: [], wrote: [] };
  const base = {
    argv: ['start'], version: '0.0.1', platform: 'darwin', homedir: '/Users/x',
    env: { PATH: '/usr/bin' },
    run: (c, a) => { calls.run.push([c, ...a]); return { status: 0, stdout: '/Users/x/.local/bin' }; },
    spawn: (c, a, o) => { calls.spawn.push([c, a, o]); return { status: 0 }; },
    readFileSync: () => '0.0.1',  // marker matches -> fast path
    writeFileSync: (f, v) => calls.wrote.push([f, v]),
    mkdirSync: () => {},
    log: (m) => calls.logs.push(m),
  };
  return { opts: { ...base, ...overrides }, calls };
}

test('bootstrapAndRun: fast path execs without installing', () => {
  const { opts, calls } = harness();
  const status = b.bootstrapAndRun(opts);
  assert.strictEqual(status, 0);
  assert.ok(!calls.run.some((c) => c[2] === 'install'), 'must not install on the fast path');
  assert.deepStrictEqual(calls.spawn[0][0], 'yohoho');
  assert.deepStrictEqual(calls.spawn[0][1], ['start']);          // arg passthrough
  assert.match(calls.spawn[0][2].env.PATH, /\.local\/bin/);       // bin on PATH
});

test('bootstrapAndRun: cold path ensures uv, installs, writes marker', () => {
  const { opts, calls } = harness({ readFileSync: () => { throw new Error('ENOENT'); } });
  b.bootstrapAndRun(opts);
  assert.ok(calls.run.some((c) => c[0] === 'uv' && c[1] === '--version'));
  assert.ok(calls.run.some((c) => c.join(' ') === 'uv tool install --force --refresh yohoho==0.0.1'));
  assert.deepStrictEqual(calls.wrote, [[b.markerPath('/Users/x'), '0.0.1']]);
});

test('bootstrapAndRun: propagates the child exit code', () => {
  const { opts } = harness({ spawn: () => ({ status: 7 }) });
  assert.strictEqual(b.bootstrapAndRun(opts), 7);
});

test('bootstrapAndRun: runs on non-darwin without a macOS-only notice', () => {
  const { opts, calls } = harness({ platform: 'win32' });
  b.bootstrapAndRun(opts);
  assert.ok(!calls.logs.some((m) => /macOS only/i.test(m)), 'the stale macOS-only notice is gone');
  assert.strictEqual(calls.spawn.length, 1);
});
