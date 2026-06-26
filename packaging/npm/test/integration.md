# npm wrapper integration gate (manual)

Verifies a packed tarball installs and forwards. Hits the network (uv + PyPI),
so it is a manual / CI gate, not part of `node --test`. **Requires yohoho to be
published to PyPI first** (or use the GitHub fallback line).

```bash
cd packaging/npm
npm pack                                   # -> by-k4n-yohoho-<version>.tgz  (scoped name)
npm i -g ./by-k4n-yohoho-*.tgz              # or: npm i --prefix /tmp/yh ./by-k4n-yohoho-*.tgz
yohoho --help                              # first run bootstraps uv + the Python tool, then forwards
yohoho doctor                              # second run hits the version-marker fast path
```

Expected: first run prints "Setting up yohoho (one-time)…", then the real CLI
help; the second run skips setup. Clean up: `npm rm -g @by-k4n/yohoho`.
