#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const { bootstrapAndRun } = require('../lib/bootstrap');
const pkg = require('../package.json');

const status = bootstrapAndRun({
  argv: process.argv.slice(2),
  version: pkg.version,
  platform: process.platform,
  homedir: os.homedir(),
  env: process.env,
  run: (cmd, args) => spawnSync(cmd, args, { encoding: 'utf8' }),
  spawn: (cmd, args, opts) => spawnSync(cmd, args, opts),
  readFileSync: fs.readFileSync,
  writeFileSync: fs.writeFileSync,
  mkdirSync: fs.mkdirSync,
  log: (m) => process.stderr.write(m + '\n'),
});
process.exit(status);
