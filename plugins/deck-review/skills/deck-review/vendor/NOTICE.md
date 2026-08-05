# Third-party notices

## Impeccable — anti-pattern detection engine

`impeccable-detect.js` is a verbatim copy of `cli/engine/detect-antipatterns-browser.js`
from Impeccable, used to power deck-review's AI-slop flagging overlay.

**Original work:** https://github.com/pbakaus/impeccable
**Version copied:** 3.5.0
**Copied on:** 2026-08-05
**Author:** Paul Bakaus
**License:** Apache License 2.0 — full text in `LICENSE-impeccable`

**Modifications:** none. The file is vendored unchanged. deck-review drives it
through its documented globals only:

- `window.__IMPECCABLE_CONFIG__` — set `{autoScan: false}` so deck-review controls
  when the scan runs
- `window.impeccableDetect()` — returns findings, each carrying a CSS `selector`,
  a `rect`, and a `findings[]` array of rule hits

Rule definitions, thresholds and copy in the overlay come from this engine. When
upgrading, re-copy the file and update the version and date above.
