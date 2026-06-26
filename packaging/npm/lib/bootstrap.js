'use strict';
const path = require('node:path');

function markerPath(homedir) {
  return path.join(homedir, '.cache', 'yohoho', 'installed-version');
}

function needsBootstrap({ markerFile, version, readFileSync }) {
  let recorded = null;
  try { recorded = String(readFileSync(markerFile, 'utf8')).trim(); } catch (_) { recorded = null; }
  return recorded !== version;
}

function ensureUv({ run, log }) {
  if (run('uv', ['--version']).status === 0) return;
  log('Installing uv (one-time)…');
  const r = run('sh', ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh']);
  if (r.status !== 0) {
    throw new Error(
      "Could not install 'uv'. Install it, then re-run:\n" +
      '  curl -LsSf https://astral.sh/uv/install.sh | sh',
    );
  }
}

function installPinned({ version, run, log }) {
  log('Setting up yohoho (one-time)…');
  const r = run('uv', ['tool', 'install', '--force', `yohoho==${version}`]);
  if (r.status !== 0) {
    throw new Error(
      `Could not install yohoho==${version} from PyPI.\n` +
      `Try the GitHub install instead:\n` +
      `  uv tool install 'git+https://github.com/by-k4n/yohoho.git@v${version}'`,
    );
  }
}

function writeMarker({ markerFile, version, mkdirSync, writeFileSync }) {
  mkdirSync(path.dirname(markerFile), { recursive: true });
  writeFileSync(markerFile, version);
}

function uvToolBin({ run, homedir }) {
  const r = run('uv', ['tool', 'dir', '--bin']);
  const bin = r.status === 0 && r.stdout ? String(r.stdout).trim() : '';
  if (bin) return bin;
  return path.join(homedir, '.local', 'bin');  // uv's documented default
}

function withBinOnPath(env, uvBin) {
  return { ...env, PATH: env.PATH ? `${uvBin}:${env.PATH}` : uvBin };
}

function bootstrapAndRun(opts) {
  const {
    argv, version, platform, homedir,
    run, spawn, readFileSync, writeFileSync, mkdirSync, env, log,
  } = opts;

  if (platform !== 'darwin') log('Note: yohoho currently supports macOS only.');

  const markerFile = markerPath(homedir);
  if (needsBootstrap({ markerFile, version, readFileSync })) {
    ensureUv({ run, log });
    installPinned({ version, run, log });
    writeMarker({ markerFile, version, mkdirSync, writeFileSync });
  }

  const childEnv = withBinOnPath(env, uvToolBin({ run, homedir }));
  const result = spawn('yohoho', argv, { stdio: 'inherit', env: childEnv });
  return typeof result.status === 'number' ? result.status : 1;
}

module.exports = {
  markerPath, needsBootstrap, ensureUv, installPinned, writeMarker,
  uvToolBin, withBinOnPath, bootstrapAndRun,
};
