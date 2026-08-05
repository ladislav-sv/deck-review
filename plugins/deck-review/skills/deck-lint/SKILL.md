---
name: deck-lint
user-invocable: true
description: Check an HTML deck or document for AI-slop tells and house-rule violations - off-brand gradients, dark grounds, nested cards, AI default fonts, banned marketing words, "not just X but Y", dashes as punctuation, print-breaking CSS. Deterministic, no LLM. Use when the user asks to lint, check, audit, or sanity-check a deck or doc, when they ask whether something "looks AI-generated" or "reads like slop", and before printing any deck to PDF.
---

# deck-lint

Deterministic AI-slop detection for HTML decks and documents. No LLM, no network,
no dependencies. Every rule points at something in the file or does not fire at all.

```bash
python3 scripts/deck_lint.py <file.html>
```

`scripts/deck_lint.py` sits next to this SKILL.md. This skill installs three
different ways and lands in a different folder each time, so **never hardcode the
path**. If the relative form does not resolve:

```bash
LINT=$(find ~/.claude/skills ~/Library/Application\ Support/Claude \
  -name deck_lint.py 2>/dev/null | head -1)
python3 "$LINT" <file.html>
```

Options: `--json` (machine-readable) · `--only visual|copy` · `--min note|warn|error`.
Accepts several files at once.

Exit codes: `0` clean or notes only · `1` warnings · `2` errors.

## When to run it

- Before printing any deck to PDF, alongside the checklist in the deck guide.
- When the user asks whether something reads as AI-generated.
- After a large copy rewrite.

Do not run it unasked on every edit. It is a gate, not a linter that nags.

## What it checks

**Visual and structural**

| Rule | Severity | Fires when |
|---|---|---|
| `dark-ground` | error | a dark fill carries content on it — navy is ink, never a ground |
| `red-rose-type` | error | Red Rose used as a typeface; it is the wordmark, and the wordmark is an image |
| `gradient-clipped-text` | error | `background-clip:text` is actually applied — Chrome prints it as a grey box |
| `css-dot-grid` | error | the dot grid is a CSS background rather than a real SVG element |
| `gradient-budget` | warn | more than one distinct gradient on a slide |
| `off-brand-gradient` | warn | a gradient using none of the brand colours |
| `ai-font` | warn | Inter, Poppins, Montserrat, Roboto and friends |
| `nested-cards` | warn | a card genuinely inside another card, not a sibling in a grid |
| `accent-budget` | warn | the leak orange used more than a few times in one unit |
| `print-shadow` | warn | `box-shadow` outside `@media screen` |
| `deck-length` | warn | more than 12 slides |
| `title-emphasis` | warn/note | a title with two or more purple phrases, or none |
| `font-allowlist` | note | any face outside the house set |

**Copy**

| Rule | Severity | Fires when |
|---|---|---|
| `not-just-but` | error | "not just X but Y", the most recognisable AI construction |
| `banned-word` | warn | seamless, unlock, leverage, empower, delve, synergy … |
| `filler-phrase` | warn | "the future of", "at the end of the day", "more than just" … |
| `dash-punctuation` | warn | an em or en dash between words; numeric ranges are allowed |
| `rule-of-three` | note | a list that announces its own count |

## Suppressing deliberate counter-examples

A guide that teaches "never write X" has to be able to print X. Mark it:

```html
<td data-lint="ignore">Unlock seamless operational excellence</td>
```

```html
<!-- lint-ignore -->  … bad examples here …  <!-- /lint-ignore -->
```

Suppression applies to copy rules. Use it for demonstration text only — if you are
reaching for it to silence a real finding, fix the finding instead.

## Reporting findings

Report by unit, most severe first, quoting the evidence the tool gives you. `unit`
is the slide number, which matches the page number printed on the slide and the
`pg-NN.png` of a render. In documents it is the block.

**Do not auto-fix.** A `banned-word` hit is sometimes the right word, and
`dash-punctuation` is occasionally a quotation. Show the findings, propose the
edits, let the user choose. The exception is a clear `error` in a file you just
generated — fix that and say you did.

If a rule produces a false positive, the rule is wrong. Fix the rule in
`scripts/deck_lint.py` rather than teaching the user to ignore it.

## Related

`deck-review` in the same plugin opens the deck in a browser for human annotation.
Lint first to catch the mechanical problems, then review for the judgement calls.
