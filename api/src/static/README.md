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

- Swagger UI: `swagger-ui-dist@5.11.0`
  - Source package: `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.11.0/`
  - Files vendored:
    - `swagger-ui/swagger-ui-bundle.js`
    - `swagger-ui/swagger-ui-standalone-preset.js`
    - `swagger-ui/swagger-ui.css`
    - `swagger-ui/favicon-16x16.png`
    - `swagger-ui/favicon-32x32.png`
- ReDoc: `redoc@2.1.3`
  - Source bundle: `https://cdn.redoc.ly/redoc/v2.1.3/bundles/redoc.standalone.js`
  - File vendored:
    - `redoc/redoc.standalone.js`

## Licensing

The upstream projects are open-source; keep their licenses in mind if re-distributing outside this repository context.
