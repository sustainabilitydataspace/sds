# E11 - Website Publication Report

**Date:** 2026-07-22
**Version:** V2026-07-22
**Scope:** Official communication website for the Sustainability Data Space project.
**Status:** `official-url-recorded`

## Official URL

- `https://sustainabilitydataspace.com/`

## Publication Evidence

The official E11 evidence is the public communication website at the URL above.
The repository records the canonical URL in `evidence/official-url.md`.

## Availability Note

The repo-local status `official-url-recorded` records the canonical publication
target. On 2026-05-30, a manual availability check from this workspace returned
HTTP 404 for the URL above. On 2026-05-31, a repeat manual availability check
from this workspace also returned HTTP 404 for the URL above. On 2026-06-01,
another repeat manual availability check from this workspace returned HTTP 404
for the URL above. On 2026-06-08, HEAD and GET availability checks from this
workspace both returned HTTP 404 for the URL above.

On 2026-06-23, repeat HEAD and GET availability checks from this workspace
returned HTTP 200 for the URL above. This is a dated reachability observation;
it does not by itself promote E12, analytics, impact evidence, or full subsidy
closure.

## Legal Pages Maintenance Note

On 2026-07-22, an authenticated WordPress/Elementor maintenance pass corrected
the public legal pages after visual QA found duplicate-header risk and body text
rendering too heavy on policy/legal pages.

Applied corrections:

- `aviso-legal`, `politica-de-cookies`, and `politica-de-seguridad` were moved
  to the `elementor_header_footer` template, matching `politica-de-privacidad`
  and removing the WordPress native `.page-header` source of duplicate titles.
- The existing Customizer CSS was extended with a scoped legal-page typography
  block for page ids `4409`, `4433`, `4436`, and `4280`.
- Legal body text now renders at `font-weight: 400`; explicit `strong` and `b`
  emphasis remains `font-weight: 600`.

Desktop and mobile browser QA confirmed one visible `h1` per legal page, no
native `.page-header` wrapper, no computed heading underline or bottom border,
and legal body text at normal weight. This is visual/legibility maintenance
evidence only; it does not close analytics, E12 impact evidence, or full content
acceptance.

## Local Artifact Exclusion

`web/sds_website/` was a local test artifact. It is not the official E11
website and must not be promoted to `deliverables/`.

## Register Mapping

This report is the canonical local E11 file for the deliverables register. Live
availability evidence should be added under `evidence/availability-checks/` only
when captured as an intentional public evidence artifact.
