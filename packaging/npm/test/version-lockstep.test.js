'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

test('npm package version matches the Python package version', () => {
  const pkg = require('../package.json');
  const pyproject = fs.readFileSync(
    path.join(__dirname, '..', '..', '..', 'pyproject.toml'), 'utf8',
  );
  const m = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
  assert.ok(m, 'could not find version in pyproject.toml');
  assert.strictEqual(pkg.version, m[1]);
});
