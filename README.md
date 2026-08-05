# deck-review

**Annotate a deck or document in your browser. Claude picks up the comments and applies them.**

Claude builds you an HTML deck. You open it, click a slide, drag a box over a chart,
or select the exact words that are wrong, and type what should change. You hit send.
Claude wakes up with your comments and edits the source.

No screenshots pasted into chat. No "on slide 7, the third bullet, second sentence".

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
INSTALL.html                         illustrated guide
```

The skill never hardcodes its own path, so it works from whichever folder it lands
in — plugin cache, uploaded-skill cache, or `~/.claude/skills/`.

---

MIT
