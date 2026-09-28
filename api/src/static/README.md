# Offline docs assets (vendored)

This folder vendors the minimum UI assets needed for **offline-safe** API docs:
- `/docs` (Swagger UI)
- `/redoc` (ReDoc)

These assets are committed to the repo so reviewers and “human testing” environments can run without external CDNs.

SDS keeps the vendored Swagger/ReDoc UI bundles unmodified. User-facing docs
guidance is added through the generated OpenAPI schema:

- `src.api.openapi_docs` enriches `/openapi.json` with concise endpoint
  guidance, request-body examples, and editable parameter examples consumed by
  Swagger UI.
- `src.api.main` serves Swagger with the stock offline-safe layout and keeps
  Try it out enabled by default so parameter fields start as editable example
  values instead of disabled preview fields.
- `swagger-ui/sds-mobile.css` is the SDS-owned mobile viewport shim. It is not
  a vendored Swagger file; it wraps long schema names and narrow tables so the
  stock Swagger document does not create horizontal page overflow on phones.

## Versions (pinned)

- Swagger UI: `swagger-ui-dist@5.33.0`
  - Source package: `https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.33.0.tgz`
  - Files vendored:
    - `swagger-ui/swagger-ui-bundle.js`
    - `swagger-ui/swagger-ui-standalone-preset.js`
    - `swagger-ui/swagger-ui.css`
    - `swagger-ui/favicon-16x16.png`
    - `swagger-ui/favicon-32x32.png`
    - `swagger-ui/LICENSE`, `swagger-ui/NOTICE`
    - `swagger-ui/swagger-ui-bundle.js.LICENSE.txt`
    - `swagger-ui/swagger-ui-standalone-preset.js.LICENSE.txt`
- ReDoc: `redoc@2.1.3`
  - Source bundle: `https://cdn.redoc.ly/redoc/v2.1.3/bundles/redoc.standalone.js`
  - File vendored:
    - `redoc/redoc.standalone.js`

## Licensing

- The Swagger `LICENSE`, `NOTICE`, and both JavaScript-specific `.LICENSE.txt`
  files are byte-for-byte from the pinned `swagger-ui-dist@5.33.0` package.
  Both vendored JavaScript files and stylesheets match that package as well.
- `redoc/LICENSE`, `redoc/redoc.standalone.js.LICENSE.txt`, and
  `redoc/756674defce81e90acea.worker.js.LICENSE.txt` are from the pinned
  `redoc@2.1.3` npm package. The vendored standalone bundle matches that
  package byte-for-byte, including the embedded worker's notice reference.

These upstream notices do not establish rights to publish the SDS repository or
replace a review of the actual release artifact and all its dependencies.
