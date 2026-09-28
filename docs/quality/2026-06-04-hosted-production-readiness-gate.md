# SDS Hosted Production Readiness Gate - 2026-06-04

## Scope

This gate applies to the hosted SustainabilityDataSpace API production surface:

- `https://sds.ueporreres.com/`

It is an API production-readiness gate. It is not the E11 website evidence at
`https://sustainabilitydataspace.com/`, and it is not a full subsidy/dossier
closure certificate. The subsidy residuals for E07, E11 content/impact
acceptance, E12, and E13 stay tracked separately in the public dossier
traceability status.

## Result

Status: `PASS`

This is a dated baseline for the hosted release verified on 2026-06-04. It is
not an evergreen proof for later runtime, OpenAPI, calculation, mapping, or data
package changes; those changes require a fresh hosted smoke before being called
current on the hosted surface.

The hosted SDS API is deployed as a native Linux service behind the Virtualmin
Apache HTTPS vhost. The API process is not exposed directly to the public
internet; it binds to loopback and is reached through Apache.

## Hosted Runtime Boundary

- Public HTTPS entrypoint: `https://sds.ueporreres.com/`
- Reverse proxy: Apache/Virtualmin HTTPS vhost.
- Public protection: Apache Basic Auth on `/`, `/docs`, `/openapi.json`,
  `/healthz`, `/ready`, and `/auth/login`.
- API protection: FastAPI bearer-token auth remains authoritative for
  `/api/v1/*` routes.
- Runtime service: `sds-api.service`.
- API bind address: `127.0.0.1:18090`.
- PostgreSQL bind address: `127.0.0.1:5432`.
- Deployment mode: native Python/systemd deployment, not Docker.
- Current migration head: retired guided-sample cleanup revision.

## Verification Evidence

Fresh checks from 2026-06-04:

- Public unauthenticated probe:
  `curl -I -L --max-time 20 https://sds.ueporreres.com/` returned
  `401 Unauthorized` with `WWW-Authenticate: Basic realm="SDS API"`,
  HSTS, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Permissions-Policy`.
- Host service check over SSH reported `sds-api.service` active and `httpd`
  active.
- Host listener check showed only loopback listeners for the API and local
  PostgreSQL: `127.0.0.1:18090` and `127.0.0.1:5432`.
- Loopback application checks on the host returned:
  - `/healthz`: `status=healthy`, `dependencies.database.status=healthy`
  - `/ready`: `status=ready`, `dependencies.database.status=healthy`
- Database evidence from local PostgreSQL on the production host:
  - `alembic=<retired guided-sample cleanup revision>`
  - `esg_values=25950`
  - `nordhaven_value_contexts=25950`
  - `sample_users=0`
  - `retired_sample_fx_observations=0`
  - `retired_sample_fx_periods=0`
- `api/scripts/verify_virtualmin_target.ps1` verified the Virtualmin document
  root, tailed reachable public logs, and confirmed the public URL returns the
  expected Basic Auth challenge.

Earlier operator-held E2E evidence from 2026-06-03 covered the credentialed
flow against the same public entrypoint: protected Swagger/OpenAPI, API login,
`/auth/me`, Nordhaven values and hierarchy reads, indicators, mappings, units,
FX coverage, and a real Nordhaven calculation. Credentials and secret material
remain outside the public repository.

## Release Verdict

The hosted API production surface verified on 2026-06-04 was ready for
controlled production use under the protected-access model recorded here.

This verdict does not close the communications/subsidy evidence residuals:

- `E07` remains external/not imported.
- `E11` website evidence remains tied to `https://sustainabilitydataspace.com/`;
  website content/impact acceptance is separate from this API host.
- `E12` remains draft-local.
- `E13` remains draft-local.

## Operator Follow-Up

- Keep Basic Auth and bearer-token responsibilities separated; do not route
  `/api/v1/*` through an Apache `Authorization` header that conflicts with API
  bearer auth.
- Keep `/healthz`, `/ready`, `/docs`, and `/openapi.json` protected unless a
  deliberate public documentation decision is approved.
- Capture a fresh backup with manifest before any package import, migration, or
  mapping materialization on the production database.
- Re-run public no-auth probes and host loopback health/readiness after every
  deployment or Apache configuration change.

## Operator Update - 2026-06-06

The hosted access credential was reset by the operator across both access
layers: Apache Basic Auth and the FastAPI admin user stored in PostgreSQL. The
new credential value, token output, `.htpasswd` hash, and production environment
file remain private and are not recorded in this repository.

Post-reset smoke evidence:

- Public unauthenticated probe still returns the expected `401 Unauthorized`
  Basic Auth challenge.
- Credentialed public probe passes Apache Basic Auth and reaches the proxied
  FastAPI app.
- `/auth/login` returns an access token for the API admin user.
- `sds-api.service` remains active.
- Host loopback `/healthz` and `/ready` return `200`.

If the reset credential was temporary or intentionally weak for emergency
access, rotate it to a strong private value before broader production use.
