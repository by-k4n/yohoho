# Releasing yohoho

Two artifacts ship in lockstep from one version: the Python package (PyPI) and the
npm wrapper. The Python `pyproject.toml` `version` is the source of truth.

1. **Decide the target version** (repo starts at `0.0.1`). Pick `X.Y.Z`.
2. **Bump both in lockstep:**
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `packaging/npm/package.json` → `"version": "X.Y.Z"`
   - Run `cd packaging/npm && node --test` (the version-lockstep guard must pass).
3. **Build + validate the Python package:**
   - `rm -rf dist && uv build`
   - `uvx twine check dist/*`  → both PASSED
4. **Dry-run to TestPyPI** (optional but recommended for a first release):
   - `uv publish --publish-url https://test.pypi.org/legacy/ --token <TEST_TOKEN>`
   - In a clean env: `uv tool install --index https://test.pypi.org/simple/ yohoho==X.Y.Z && yohoho --help`
5. **Publish to PyPI:** `uv publish --token <PYPI_TOKEN>`
6. **Publish the npm wrapper:** `cd packaging/npm && npm publish --access public`
   (the npm package is scoped `@by-k4n/yohoho` because the unscoped `yohoho` name is already taken on
   npm; scoped packages publish private by default, so `--access public` is required. PyPI stays `yohoho`.)
7. **Tag the release:** `git tag vX.Y.Z && git push --tags`
8. **Smoke** (clean machine): `npm i -g @by-k4n/yohoho && yohoho --help` and `uv tool install yohoho && yohoho --help`.

Tokens are supplied at publish time (env/CLI), never committed.
