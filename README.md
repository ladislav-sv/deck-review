# deck-review

**Annotate a deck or document in your browser. Claude picks up the comments and applies them.**

Claude builds you an HTML deck. You open it, click a slide, drag a box over a chart,
or select the exact words that are wrong, and type what should change. You hit send.
Claude wakes up with your comments and edits the source.

No screenshots pasted into chat. No "on slide 7, the third bullet, second sentence".

Two skills, one install:

| | |
|---|---|
| **deck-review** | the browser annotation round trip |
| **deck-lint** | deterministic AI-slop detection — no LLM, no network |

---

## Install

```
/plugin marketplace add ladislav-sv/deck-review
/plugin install deck-review@edmund
```

That is it. Works in Claude Code anywhere it runs: the desktop app, the terminal,
the web app, the IDE extensions.

### Updating

```
/plugin update deck-review@edmund
```

Pulls whatever is on `main`. Version lives in
[`plugins/deck-review/.claude-plugin/plugin.json`](plugins/deck-review/.claude-plugin/plugin.json).

### Without GitHub

Download `deck-review-skill.zip` from
[Releases](../../releases), then in Claude: `⌘,` → **Skills** → **Add → Upload a
skill**. Same tool, but you re-upload to update rather than running `/plugin update`.

---

## Use it

Ask in plain words:

```
let me annotate the deck
review round on the onboarding docs
```

Claude starts a local server and hands you a URL. Annotate, hit **Send**, and Claude
picks it up.

| Gesture | Makes |
|---|---|
| **Click** | a pin — "something is off here" |
| **Drag** over blank space | a region box — Claude crops the rendered page and looks at it |
| **Select text** | a quote — the exact words, which Claude can find in the source |
| **⇧ + drag** | forces a box over text |

`⌘↵` saves, `esc` cancels. Comments survive a refresh. A green **SERVER LIVE** dot
tells you the round trip is available; if it goes orange, **Copy JSON to clipboard**
still gets your comments across.

---

## How the round trip works

The server serves your file with an annotation overlay spliced in **at request time**.
The file on disk is never modified, so whatever you print to PDF stays clean.

Hitting send writes `<name>.review-NNN.json` next to the file, prints it to stdout,
and exits `0`. That exit is the signal: a completing background task re-invokes Claude
with the comments already in hand. No polling, no pasting.

```
Claude builds  →  you annotate  →  server exits 0  →  Claude wakes, applies, rebuilds
      ↑                                                              │
      └──────────────────────  next round  ───────────────────────────┘
```

---

## deck-lint

```bash
python3 deck_lint.py deck.html
```

Or just ask: *"lint the deck"*, *"does this read like AI slop?"*

Every rule points at something in the file or does not fire at all. Rules that need
judgement are deliberately absent — a linter that cries wolf gets ignored.

**Visual** — dark grounds (navy is ink, never a ground), more than one gradient per
slide, gradients using none of the brand colours, cards genuinely nested inside cards,
Inter/Poppins/Montserrat and the rest of the AI default typefaces, `background-clip:text`
that Chrome prints as a grey box, `box-shadow` outside `@media screen` that Apple
Preview turns boxy, a dot grid written as a CSS background that export tools drop.

**Copy** — `not just X but Y`, marketing filler (`seamless`, `unlock`, `leverage`,
`empower`, `delve`, `synergy`…), phrases that carry no fact, em and en dashes used as
punctuation.

Findings are reported per slide, numbered to match the printed page. Exit codes:
`0` clean, `1` warnings, `2` errors — so it drops into a pre-commit hook or CI.

A guide that teaches "never write X" has to be able to print X, so counter-examples
can be excluded:

```html
<td data-lint="ignore">Unlock seamless operational excellence</td>
<!-- lint-ignore -->  … bad examples …  <!-- /lint-ignore -->
```

[`test/slop-fixture.html`](test/slop-fixture.html) is a deliberately awful page that
should trip 13 rules. If it stops doing that, something regressed.

### The two together

`--annotate` writes a normal HTML file, so the review server can serve it. Lint
first, then review the flagged copy, and you get the flags and the annotation UI
on the same page:

```bash
python3 deck_lint.py deck.html --annotate deck.flagged.html
python3 review_server.py deck.flagged.html
```

Comments then come back against `deck.flagged.html` — apply them to the real
source, not the flagged copy.

---

## Decks and documents

The overlay picks its mode from the page.

|  | Deck mode | Doc mode |
|---|---|---|
| Triggered by | `<section class="slide">` present | anything else |
| Layout | slides scaled to fit | the document keeps its own |
| Anchor | the slide, numbered to match the printed page | nearest `section[id]`, heading, paragraph, table |
| Reads back as | `S07 · text` | `#keystatic · text` |

Document comments carry the nearest preceding heading, so Claude knows where it is in
words and not just in pixels. Coordinates are relative to the block, so they survive
reflow.

---

## Applying comments

- If the file has a sibling `*_build.py`, **that** is the source of truth. Edit the
  script, not the generated HTML, or the next build reverts the change.
- Quoted text comes back with whitespace collapsed. A sentence that wraps across two
  source lines arrives as one line — match on a short fragment, not the whole sentence.
- A region comment means: crop the rendered page to the rect and look before changing.
- A review file is spent once applied. Its coordinates describe the revision that was
  annotated, not the rebuilt one.

---

## Requirements

Python 3, standard library only. No `pip install`, no Node, no build step.

The server binds to `127.0.0.1`, has no auth, and exits after each send. Do not
expose it.

---

## Repo layout

```
.claude-plugin/marketplace.json      makes this repo installable
plugins/deck-review/
  .claude-plugin/plugin.json         name, version
  skills/deck-review/
    SKILL.md                         when Claude reaches for it, how to apply comments
    scripts/review_server.py         the server and the injected overlay
  skills/deck-lint/
    SKILL.md                         when to lint, how to report findings
    scripts/deck_lint.py             the rules
INSTALL.html                         illustrated guide
test/slop-fixture.html               a bad page that must keep failing
```

The skill never hardcodes its own path, so it works from whichever folder it lands
in — plugin cache, uploaded-skill cache, or `~/.claude/skills/`.

---

MIT
