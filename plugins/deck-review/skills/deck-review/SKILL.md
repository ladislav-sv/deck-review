---
name: deck-review
user-invocable: true
description: Open an HTML deck or document on localhost with an annotation overlay so the user can pin, box and quote-select comments directly on it, then apply those comments to the source. Works on slide decks and on flowing documents (docs, one-pagers, proposals, guides). Use when the user wants to review, mark up, annotate, or give feedback on a deck, slide, doc or generated HTML page before it is printed to PDF - e.g. "let me annotate this", "open the deck so I can comment", "I want to mark up slide 4", "review round on the onboarding docs".
---

# Deck review

Serves an HTML deck on `127.0.0.1` with an annotation overlay injected on the fly.
**The file on disk is never modified**, so whatever gets printed to PDF stays clean.

The user clicks, drags or selects text on the slides, writes comments, and hits
**Send to Claude**. That writes `<deck>.review-NNN.json` next to the deck, prints it
to stdout, and exits `0` — which is what wakes you up. No polling.

## Running it

Start it as a **background** Bash task, always. The process blocks until the user
sends or the timeout expires, so a foreground call would hang the turn.

```bash
python3 scripts/review_server.py <deck.html>
```

`scripts/review_server.py` sits next to this SKILL.md. This skill installs three
different ways and lands in a different folder each time, so **never hardcode the
path**. If the relative form does not resolve, find it once and reuse it:

```bash
SRV=$(find ~/.claude/skills ~/Library/Application\ Support/Claude \
  -name review_server.py -path '*deck-review*' 2>/dev/null | head -1)
python3 "$SRV" <deck.html>
```

Options: `--port 7654` (auto-increments if taken) · `--timeout 3600` ·
`--no-open` (skip auto-opening the browser) · `--out path.json`.

Then tell the user the URL in one line and stop. Do not narrate, do not poll, do not
start other work that assumes the review is done. When the task completes you are
re-invoked with the JSON in the output.

Exit codes: `0` comments sent · `2` timed out with nothing sent · `1` interrupted.
On `2` or `1`, say so plainly and offer to reopen — do not invent comments.

## What comes back

```json
{ "deck": "Edmund_Deck_Guide.html", "sent_at": "...", "count": 2,
  "comments": [
    { "n": 1, "slide": 7, "type": "text", "category": "copy",
      "note": "too abstract, name the machine",
      "text": "The chart argues, the speaker does not have to.",
      "path": "div:nth-child(3) > div:nth-child(2) > span:nth-child(2)",
      "point": null, "rect": {"x": 12.4, "y": 71.0, "w": 33.1, "h": 3.2} },
    { "n": 2, "slide": 3, "type": "region", "category": "layout",
      "note": "swatch labels are cramped",
      "text": null, "path": null, "point": null,
      "rect": {"x": 4.1, "y": 33.0, "w": 27.5, "h": 22.0} }
  ] }
```

- `slide` is 1-based and **matches the page number printed on the slide**, so slide 7
  is page 7 of the PDF and `pg-07.png` of the render.
- `rect` / `point` are percentages of the anchor box, not pixels.
- `category` is one of `copy` · `layout` · `data` · `colour` · `cut it` · `note`.

## Deck mode vs doc mode

The overlay picks its mode from the page. Both write the same file; only the
anchor differs.

| | `mode: "deck"` | `mode: "doc"` |
|---|---|---|
| Detected by | `<section class="slide">` present | no slides |
| Anchor | the slide | the nearest block: `section[id]`, heading, `p`, `li`, `table` |
| `slide` | 1-based page number | `null` |
| `section` | `null` | `{id, heading, tag}` |
| `rect`/`point` | % of the slide | % of that block |

In doc mode use `section.id` (the anchor a reader would link to) and
`section.heading` (nearest preceding heading, resolved for you) to locate the
passage, then confirm with `text`. Percentages are relative to the block, so they
stay meaningful after the document reflows.

## Applying the comments

1. **Find the real source first.** If a sibling `<stem>_build.py` exists, that is the
   source of truth — edit the Python, not the HTML, or the next build reverts you.
   Otherwise edit the HTML directly.
2. **`type: "text"` is the precise one, but do not paste it straight into `old_string`.**
   The browser collapses whitespace, so a sentence that wraps across two source lines
   comes back as one line with a single space. An exact-match edit on the full string
   will fail. Grep a **distinctive fragment that cannot span a source line break**
   (5-8 words), read the surrounding lines, then edit what is actually there:
   ```bash
   grep -n "Pick one before you open" build.py   # not the whole sentence
   ```
   If it appears more than once, the `slide` number disambiguates. If the string is
   computed rather than literal (a list comprehension, an f-string), find the data
   that produced it.
3. **`type: "region"` means look at it.** Crop the rendered page to the rect and
   actually view it before deciding what to change. `pdftoppm` crops directly, and
   its flags are far more reliable than `sips --cropOffset`:
   ```bash
   pdftoppm -png -r 150 -f 7 -l 7 -x 1950 -y 690 -W 960 -H 330 deck.pdf crop
   ```
   In doc mode there is no PDF page to crop: open the doc in the browser and read
   the block named by `section.id` instead.
4. **`type: "pin"` is a pointer, not an instruction.** Read `note` and use `path` /
   `point` to work out what it is aimed at.
5. Work through the comments in order, rebuild, re-render every page, and **look at
   the ones you changed** before reporting.
6. Report per comment: what you changed, or why you did not. Never silently skip one.
   If a comment asks for something that breaks a rule in the deck's own guide, say so
   and do it anyway if the user reaffirms.

## Consuming the review file

Once applied, the review file is spent — the coordinates refer to the revision that
was annotated, not the rebuilt one. Leave the JSON on disk as a record; a second
round writes `review-002.json`. Never re-apply an old review file.

## Notes

- Binds to `127.0.0.1` only, no auth, dies on send. Do not expose it.
- Comments survive a page refresh via `localStorage`, keyed on the deck filename.
- **Copy JSON to clipboard** in the rail is the fallback if the POST cannot reach the
  server — the user pastes it into chat and you apply it the same way.
- **Leave the server running when you hand over the URL.** Killing it makes Send look
  like it does nothing. The rail turns orange within four seconds, but do not put the
  user through that.
- The server exits after each send by design, so a second round needs a fresh run.
