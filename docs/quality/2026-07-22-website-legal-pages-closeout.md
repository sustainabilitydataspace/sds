# Website Legal Pages Closeout - 2026-07-22

## Scope

This note records the executed public website maintenance for legal and policy
pages on `https://sustainabilitydataspace.com/`.

The change was made through the authenticated WordPress browser session using
REST and the WordPress Customizer. No SSH, WP-CLI, direct database access, or
WordPress application password was used.

## Issue

The legal/policy pages showed two related presentation risks:

- pages using the default WordPress page template still emitted a native
  `.site-main > .page-header` title in addition to the Elementor `h1`;
- Elementor global text typography set `--e-global-typography-text-font-weight`
  to `600`, so legal body text inherited a semi-bold weight.

`politica-de-privacidad` already used the Elementor header/footer template.
`aviso-legal`, `politica-de-cookies`, and `politica-de-seguridad` did not.

## Applied Changes

1. Aligned legal pages to the Elementor header/footer template.
   - `aviso-legal` (`page-id-4409`)
   - `politica-de-cookies` (`page-id-4433`)
   - `politica-de-seguridad` (`page-id-4280`)

2. Extended the active `hello-elementor` Customizer CSS with a scoped block for
   legal pages.
   - Targeted page ids: `4409`, `4433`, `4436`, `4280`.
   - Legal body text widgets now render at `font-weight: 400`.
   - `strong` and `b` emphasis remains at `font-weight: 600`.

3. Kept the existing `.site-main > .page-header` hide rule as a defensive
   fallback, although the corrected legal pages no longer emit the native page
   header wrapper.

## Final QA

Browser QA was executed on desktop (`1365x900`) and mobile (`390x844`) for:

- `https://sustainabilitydataspace.com/aviso-legal/`
- `https://sustainabilitydataspace.com/politica-de-privacidad/`
- `https://sustainabilitydataspace.com/politica-de-cookies/`
- `https://sustainabilitydataspace.com/politica-de-seguridad/`

Observed result:

- exactly one visible `h1` per legal page;
- no native `.site-main > .page-header` wrapper on the corrected legal pages;
- no computed heading underline or bottom border;
- legal body text widgets computed at `font-weight: 400`;
- legal headings remained at `font-weight: 600`;
- desktop screenshot review showed a single Elementor title and normal-weight
  legal text.

## Evidence Boundary

This closeout records public visual/legibility maintenance for E11. It does not
claim analytics evidence, E12 communications impact closure, full content
acceptance, or full subsidy/dossier closure.

Local screenshots and browser metrics were captured in the ignored local
working area, which is not canonical public evidence.
