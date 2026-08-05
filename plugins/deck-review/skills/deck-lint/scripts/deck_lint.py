#!/usr/bin/env python3
"""
deck-lint - deterministic AI-slop and house-rule detection for HTML decks and docs.

No LLM, no network, no dependencies. Every rule either fires on something you can
point at in the file, or it does not fire at all. Rules that need judgement are
deliberately absent: a linter that cries wolf gets ignored.

    python3 deck_lint.py deck.html
    python3 deck_lint.py deck.html --json
    python3 deck_lint.py deck.html --only visual
    python3 deck_lint.py deck.html --min error

Exit codes: 0 clean or notes only · 1 warnings · 2 errors.
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- palette ----
BRAND = {
    "purple": "#7145fc", "pink": "#f8adf0", "ink": "#1a1f2e", "ink2": "#3d4557",
    "ink3": "#666e82", "paper": "#fbfaf8", "leak": "#d0562b", "navy": "#252e3d",
}
ALLOWED_FONTS = {
    "space grotesk", "dm sans", "jetbrains mono", "red rose",
    "system-ui", "sans-serif", "serif", "monospace", "ui-monospace",
    "-apple-system", "blinkmacsystemfont", "segoe ui", "georgia", "inherit",
}
# The classic generative-AI typeface tells. Not wrong everywhere, wrong here.
AI_FONTS = {"inter", "poppins", "montserrat", "roboto", "open sans", "lato", "nunito"}

BANNED_WORDS = [
    "seamless", "seamlessly", "unlock", "supercharge", "leverage", "revolutionise",
    "revolutionize", "empower", "elevate", "game-changing", "game changing",
    "holistic", "robust", "cutting-edge", "best-in-class", "world-class",
    "effortless", "delve", "harness", "unleash", "transformative", "synergy",
    "step-by-step journey", "journey towards", "journey toward",
]
FILLER_PHRASES = [
    "that is the whole", "at the end of the day", "it is worth noting",
    "in today's fast-paced", "the future of", "and beyond", "more than just",
]

# Em-dash overuse is a saturation pattern, not a single-occurrence sin. Two gates,
# borrowed from impeccable: an absolute floor, and a density per character of body
# text. One dash in a long deck is punctuation; one per clause is the tell.
DASH_FLOOR = 4
DASH_CHARS_PER_DASH = 500

SEV_ORDER = {"note": 0, "warn": 1, "error": 2}


# ------------------------------------------------------------- utilities ----
def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def strip_css_and_script(html):
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    return html


def drop_ignored(fragment):
    """Remove regions the author marked as deliberate counter-examples.

    A guide that shows what bad copy looks like must be able to show it. Mark the
    element with data-lint="ignore", or wrap a region in
    <!-- lint-ignore --> ... <!-- /lint-ignore -->.
    """
    frag = re.sub(r"<!--\s*lint-ignore\s*-->.*?<!--\s*/lint-ignore\s*-->", " ",
                  fragment, flags=re.S | re.I)
    out, i = [], 0
    for m in re.finditer(r"<(\w+)\b[^>]*\bdata-lint\s*=\s*[\"']ignore[\"'][^>]*>", frag, re.I):
        if m.start() < i:
            continue
        out.append(frag[i:m.start()])
        depth, j = 1, m.end()
        op = re.compile(r"<%s\b" % m.group(1), re.I)
        cl = re.compile(r"</%s\s*>" % m.group(1), re.I)
        while j < len(frag) and depth:
            o, c = op.search(frag, j), cl.search(frag, j)
            if not c:
                j = len(frag)
                break
            if o and o.start() < c.start():
                depth += 1
                j = o.end()
            else:
                depth -= 1
                j = c.end()
        i = j
    out.append(frag[i:])
    return "".join(out)


def visible_text(fragment):
    """Markup out, entities decoded, whitespace collapsed."""
    t = strip_css_and_script(fragment)
    t = re.sub(r"<svg\b.*?</svg>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    for ent, ch in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                    ("&middot;", "·"), ("&hellip;", "…"), ("&times;", "×"),
                    ("&rsquo;", "'"), ("&ldquo;", '"'), ("&bdquo;", '"'),
                    ("&ndash;", "–"), ("&mdash;", "—"), ("&minus;", "−"),
                    ("&asymp;", "≈"), ("&rarr;", "→"), ("&larr;", "←"), ("&deg;", "°")]:
        t = t.replace(ent, ch)
    return re.sub(r"\s+", " ", t).strip()


def split_units(html):
    """Return [(label, markup)] — one per slide, or one whole-document unit."""
    slides = re.findall(r"<section\b[^>]*class=\"[^\"]*\bslide\b[^\"]*\".*?</section>",
                        html, flags=re.S | re.I)
    if slides:
        return [("slide %02d" % (i + 1), s) for i, s in enumerate(slides)]
    body = re.search(r"<body\b[^>]*>(.*)</body>", html, flags=re.S | re.I)
    return [("document", body.group(1) if body else html)]


def css_blocks(html):
    return "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.S | re.I))


def screen_media_spans(css):
    """Character ranges covered by @media screen, so print-only rules can be told apart."""
    spans = []
    for m in re.finditer(r"@media[^{]*\bscreen\b[^{]*\{", css, flags=re.I):
        i, depth = m.end(), 1
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        spans.append((m.start(), i))
    return spans


def in_spans(pos, spans):
    return any(a <= pos < b for a, b in spans)


# ----------------------------------------------------------------- rules ----
class Lint:
    def __init__(self, html, path):
        self.html = html
        self.path = path
        self.css = css_blocks(html)
        self.screen = screen_media_spans(self.css)
        self.units = split_units(html)
        self.is_deck = self.units and self.units[0][0].startswith("slide")
        self.findings = []

    def add(self, rule, sev, unit, msg, evidence=None, family="visual",
            match=None, detail=None, anchor=None):
        self.findings.append({
            "rule": rule, "severity": sev, "family": family, "unit": unit,
            "message": msg, "evidence": (evidence or "")[:160],
            "match": match, "detail": detail, "anchor": anchor,
        })

    # -- visual / structural ------------------------------------------------
    def r_gradient_budget(self):
        for label, frag in self.units:
            grads = re.findall(r"linear-gradient\(([^)]*)\)", frag, flags=re.I)
            grads += ["var-grad"] * len(re.findall(r"var\(--grad(?:-h)?\)", frag))
            shapes = {re.sub(r"\s+", "", g).lower() for g in grads}
            if len(shapes) > 1:
                self.add("gradient-budget", "warn", label,
                         "%d distinct gradients on one %s. The budget is one; a second means "
                         "one of them is decoration." % (len(shapes),
                                                         "slide" if self.is_deck else "block"),
                         " | ".join(sorted(shapes)[:3]))

    def r_off_brand_gradient(self):
        for label, frag in self.units:
            for g in re.findall(r"linear-gradient\(([^)]*)\)", frag, flags=re.I):
                hexes = [h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,6}", g)]
                if not hexes:
                    continue
                known = {BRAND["purple"], BRAND["pink"]}
                if not set(hexes) & known:
                    self.add("off-brand-gradient", "warn", label,
                             "Gradient uses none of the brand colours. Purple to blue is the "
                             "house style of every AI-generated page on the internet.",
                             g.strip())

    def r_dark_ground(self):
        """A dark fill is only a *ground* if something sits on it. A swatch chip
        demonstrating #1A1F2E is the palette doing its job, not a violation."""
        tag_pat = re.compile(r"<(div|section|main|header|footer|aside|td|tr)\b([^>]*)>", re.I)
        for label, frag in self.units:
            for m in tag_pat.finditer(frag):
                attrs = m.group(2)
                bg = re.search(r"background(?:-color)?\s*:\s*([^;\"']+)", attrs, flags=re.I)
                if not bg:
                    continue
                val = bg.group(1).strip()
                if "gradient" in val.lower():
                    continue
                hexes = re.findall(r"#[0-9a-fA-F]{3,6}", val)
                dark = [h for h in hexes if (luminance(h) or 1) < 0.16]
                if "var(--navy)" in val or "var(--ink)" in val:
                    dark.append(val)
                if not dark:
                    continue
                inner = self._inner_html(frag, m.end(), m.group(1))
                if not visible_text(inner):
                    continue          # empty box: a swatch, a rule, a spacer
                self.add("dark-ground", "error", label,
                         "Dark background with content on it. Navy is ink, never a ground: "
                         "no dark slide, no dark band, no dark card.", val,
                         anchor=m.group(0), detail="dark ground")

    @staticmethod
    def _inner_html(frag, start, tag):
        """Balanced inner HTML for the element opened just before `start`."""
        depth, i = 1, start
        opener = re.compile(r"<%s\b" % tag, re.I)
        closer = re.compile(r"</%s\s*>" % tag, re.I)
        while i < len(frag) and depth:
            o, c = opener.search(frag, i), closer.search(frag, i)
            if not c:
                return frag[start:]
            if o and o.start() < c.start():
                depth += 1
                i = o.end()
            else:
                depth -= 1
                i = c.end()
                if not depth:
                    return frag[start:c.start()]
        return frag[start:i]

    def r_gradient_clipped_text(self):
        """Only a bug if it actually renders. Declared-but-unused CSS is dead weight,
        not a print defect."""
        body = re.sub(r"<style\b.*?</style>", " ", self.html, flags=re.S | re.I)
        # Only a style attribute counts. Prose that *mentions* the property — a guide
        # warning you about this very bug — is not the bug.
        inline = [a for a in re.findall(r"style\s*=\s*\"([^\"]*)\"", body, flags=re.I)
                  if re.search(r"background-clip\s*:\s*text", a, flags=re.I)]
        if inline:
            self.add("gradient-clipped-text", "error", "inline style",
                     "background-clip:text prints as a grey box in Chrome PDF. Big numbers "
                     "must use solid purple.", inline[0][:90])
            return
        classes = {c for m in re.finditer(r"background-clip\s*:\s*text", self.css, flags=re.I)
                   for c in self._selector_classes_before(m.start())}
        used = [c for c in classes if re.search(r'class="[^"]*\b%s\b' % re.escape(c), body)]
        if used:
            self.add("gradient-clipped-text", "error", "stylesheet",
                     "background-clip:text prints as a grey box in Chrome PDF. Big numbers "
                     "must use solid purple.", ", ".join("." + c for c in used[:3]))

    def _selector_classes_before(self, pos):
        head = self.css[:pos]
        brace = head.rfind("{")
        sel = head[head.rfind("}", 0, brace) + 1:brace] if brace > 0 else ""
        return re.findall(r"\.([A-Za-z0-9_-]+)", sel)

    def r_print_shadow(self):
        for m in re.finditer(r"box-shadow\s*:\s*([^;}]*)", self.css, flags=re.I):
            # "box-shadow:none" in a print block is the fix, not the defect.
            if re.match(r"\s*none\b", m.group(1), flags=re.I):
                continue
            if not in_spans(m.start(), self.screen):
                line = self.css[m.start():m.start() + 90].split("\n")[0]
                self.add("print-shadow", "warn", "stylesheet",
                         "box-shadow declared outside @media screen. Apple Preview and Quick "
                         "Look rasterise it into a hard grey box in the PDF.", line.strip())
                break

    def r_css_dot_grid(self):
        if not self.is_deck:
            return
        has_svg = 'class="dotgrid"' in self.html
        css_dots = re.search(r"\.slide[^{]*\{[^}]*radial-gradient\([^)]*\)[^}]*\}",
                             self.css, flags=re.I | re.S)
        if css_dots and not has_svg:
            self.add("css-dot-grid", "error", "stylesheet",
                     "The dot grid looks like a CSS background. Export tools and Figma import "
                     "drop those silently. It has to be a real SVG element per slide.",
                     "radial-gradient on .slide")

    def r_accent_budget(self):
        for label, frag in self.units:
            hits = re.findall(re.escape(BRAND["leak"]), frag, flags=re.I)
            hits += re.findall(r"var\(--neg\)", frag)
            if len(hits) > 3:
                self.add("accent-budget", "warn", label,
                         "The leak accent appears %d times. It marks the one broken thing on "
                         "the slide, never decoration." % len(hits))

    def r_title_emphasis(self):
        if not self.is_deck:
            return
        for label, frag in self.units:
            titles = re.findall(r"<h[12]\b[^>]*>(.*?)</h[12]>", frag, flags=re.S | re.I)
            for t in titles:
                if not visible_text(t):
                    continue
                n = len(re.findall(r"<em\b", t, flags=re.I))
                n += len(re.findall(r"<span[^>]*(?:--purple|#7145fc)", t, flags=re.I))
                if n == 0:
                    am = re.search(r"<h[12]\b[^>]*>", frag, re.I)
                    self.add("title-emphasis", "note", label,
                             "Title carries no purple phrase. Every title should have exactly one.",
                             visible_text(t)[:80],
                             anchor=am.group(0) if am else None, detail="none")
                elif n > 1:
                    self.add("title-emphasis", "warn", label,
                             "Title carries %d emphasised phrases. Exactly one." % n,
                             visible_text(t)[:80],
                             anchor=re.search(r"<h[12]\b[^>]*>", frag, re.I).group(0),
                             detail="%d emphases" % n)

    def r_fonts(self):
        seen = set()
        for m in re.finditer(r"font-family\s*:\s*([^;\"'}]+)", self.html, flags=re.I):
            for fam in m.group(1).split(","):
                f = fam.strip().strip("'\"").lower()
                if not f or f.startswith("var(") or f in seen:
                    continue
                seen.add(f)
                if f in AI_FONTS:
                    self.add("ai-font", "warn", "stylesheet",
                             "'%s' is one of the default typefaces every AI-generated page "
                             "reaches for. The house faces are Space Grotesk, DM Sans and "
                             "JetBrains Mono." % fam.strip(), m.group(0)[:70])
                elif f not in ALLOWED_FONTS:
                    self.add("font-allowlist", "note", "stylesheet",
                             "'%s' is outside the house set." % fam.strip(), m.group(0)[:70])

    def r_red_rose(self):
        for label, frag in self.units:
            if re.search(r"var\(--f-word\)|font-family\s*:\s*['\"]?Red Rose", frag, flags=re.I):
                self.add("red-rose-type", "error", label,
                         "Red Rose is the wordmark only, and the wordmark is an image. Place "
                         "the logo file, never typeset Edmund by hand.")

    def r_nested_cards(self):
        """Real containment, not a character window: two cards side by side in a grid
        are siblings, and siblings are fine."""
        card = re.compile(r"<div\b[^>]*class=\"[^\"]*\b(?:card|tpanel|dcard)\b[^\"]*\"[^>]*>", re.I)
        for label, frag in self.units:
            for m in card.finditer(frag):
                inner = self._inner_html(frag, m.end(), "div")
                if card.search(inner):
                    self.add("nested-cards", "warn", label,
                             "A card inside a card. Nesting panels is the most reliable "
                             "signature of generated layout.",
                             visible_text(inner)[:70],
                             anchor=card.search(inner).group(0), detail="cards in cards")
                    break

    def r_deck_length(self):
        if self.is_deck and len(self.units) > 12:
            self.add("deck-length", "warn", "deck",
                     "%d slides. Past twelve it is not a deck any more: write the document "
                     "and send that instead." % len(self.units))

    # -- copy ---------------------------------------------------------------
    def r_copy(self):
        for label, frag in self.units:
            text = visible_text(drop_ignored(frag))
            if not text:
                continue
            low = text.lower()
            for w in BANNED_WORDS:
                for m in re.finditer(r"\b%s\b" % re.escape(w), low):
                    self.add("banned-word", "warn", label,
                             "'%s' is marketing filler." % w,
                             text[max(0, m.start() - 45):m.start() + 55], family="copy",
                             match=text[m.start():m.end()], detail=w)
                    break
            for p in FILLER_PHRASES:
                if p in low:
                    i = low.index(p)
                    self.add("filler-phrase", "warn", label,
                             "'%s' adds no fact. If a sentence carries none, it goes." % p,
                             text[max(0, i - 40):i + 60], family="copy",
                             match=text[i:i + len(p)], detail=p)
            for m in re.finditer(r"\bnot (?:just|only|merely)\b[^.]{0,60}?\bbut\b", low):
                self.add("not-just-but", "error", label,
                         "'not just X but Y' is the single most recognisable AI construction.",
                         text[m.start():m.end() + 20], family="copy",
                         match=text[m.start():m.end()])
            if re.search(r"\b(three|four|five) (?:key |core |main )?(?:pillars|reasons|"
                         r"principles|steps|ways)\b", low):
                self.add("rule-of-three", "note", label,
                         "A numbered list announced by its own count usually exists for "
                         "rhythm rather than because the count is real.", family="copy")

    def r_dash_density(self):
        """Fires on saturation, not on the first dash."""
        whole, hits = [], []
        for label, frag in self.units:
            t = visible_text(drop_ignored(frag))
            whole.append(t)
            for m in re.finditer(r"[A-Za-zÀ-ž]\s([—–])\s[A-Za-zÀ-ž]", t):
                hits.append((label, t[max(0, m.start() - 45):m.start() + 55],
                             t[m.start():m.end()]))
        body = " ".join(whole)
        if not hits or not body:
            return
        density = len(body) / len(hits)
        if len(hits) < DASH_FLOOR or density > DASH_CHARS_PER_DASH:
            return
        label, ev, match = hits[0]
        self.add("dash-density", "warn", label,
                 "%d dashes used as punctuation, one every %d characters. At that rate it "
                 "is a tic, not punctuation: use a comma, a colon or a new sentence."
                 % (len(hits), int(density)), ev, family="copy", match=match.strip(),
                 detail="%d dashes" % len(hits))

    def run(self):
        for name in dir(self):
            if name.startswith("r_"):
                getattr(self, name)()
        order = {"error": 0, "warn": 1, "note": 2}
        self.findings.sort(key=lambda f: (order[f["severity"]], f["unit"], f["rule"]))
        return self.findings


# ---------------------------------------------------------------- report ----
GLYPH = {"error": "✗", "warn": "!", "note": "·"}


def report(findings, path, total_units, is_deck):
    name = os.path.basename(path)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEV_ORDER}
    out = ["", "  deck-lint  %s" % name,
           "  %s, %d %s" % ("deck" if is_deck else "document", total_units,
                            "slides" if is_deck else "block"),
           "  " + "─" * 66]
    if not findings:
        out += ["", "  ✓  nothing to flag", ""]
        return "\n".join(out)
    unit = None
    for f in findings:
        if f["unit"] != unit:
            unit = f["unit"]
            out.append("")
            out.append("  %s" % unit.upper())
        out.append("  %s %-22s %s" % (GLYPH[f["severity"]], f["rule"], f["message"]))
        if f["evidence"]:
            out.append("    %s%s" % (" " * 23, "“" + f["evidence"].strip() + "”"))
    out += ["", "  " + "─" * 66,
            "  %d error · %d warn · %d note" % (counts["error"], counts["warn"], counts["note"]),
            ""]
    return "\n".join(out)


# -------------------------------------------------------------- annotate ----
ANNOTATE_CSS = """
<style id="lint-css">
:root{--lint-flag:#f5c518;--lint-err:#d0562b;--lint-note:#8b95a8}
#lint-layer{position:absolute;inset:0;pointer-events:none;z-index:99998}
#lint-layer .lbox{position:absolute;border:1px solid var(--lint-flag);box-sizing:border-box}
#lint-layer .lbox[data-sev="error"]{border-color:var(--lint-err)}
#lint-layer .lbox[data-sev="note"]{border-color:var(--lint-note)}
#lint-layer .ltab{position:absolute;left:-1px;transform:translateY(-100%);
  background:var(--lint-flag);color:#1a1f2e;padding:2px 7px 3px;border-radius:3px 3px 0 0;
  font:600 11px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap;
  letter-spacing:.01em}
#lint-layer .lbox[data-sev="error"] .ltab{background:var(--lint-err);color:#fff}
#lint-layer .lbox[data-sev="note"] .ltab{background:var(--lint-note);color:#fff}
#lint-bar{position:fixed;left:0;right:0;bottom:0;z-index:99999;background:#12151f;color:#fff;
  padding:9px 18px;font:600 12px/1.4 ui-monospace,SFMono-Regular,monospace;letter-spacing:.05em;
  display:flex;gap:18px;align-items:center}
#lint-bar b{color:var(--lint-flag);font-weight:700}
#lint-bar .e{color:var(--lint-err)}
@media print{#lint-layer,#lint-bar{display:none}}
</style>
"""

ANNOTATE_JS = """
<script>
/* Draw a box around each flagged element with a tab on its top-left corner.
   Measured after layout so the boxes track the real rendered geometry. */
(function(){
  function draw(){
    var layer=document.getElementById('lint-layer'); if(!layer) return;
    layer.innerHTML='';
    var base=document.body.getBoundingClientRect();
    (window.__LINT__||[]).forEach(function(f,i){
      var t=document.querySelector('[data-lint-target="'+i+'"]'); if(!t) return;
      var r=t.getBoundingClientRect();
      if(!r.width && !r.height) return;
      var pad=f.pad===0?0:3;
      var box=document.createElement('div');
      box.className='lbox'; box.setAttribute('data-sev',f.severity);
      box.style.left=(r.left-base.left-pad)+'px';
      box.style.top=(r.top-base.top-pad)+'px';
      box.style.width=(r.width+pad*2)+'px';
      box.style.height=(r.height+pad*2)+'px';
      var tab=document.createElement('span');
      tab.className='ltab';
      tab.textContent=f.label;
      box.appendChild(tab);
      layer.appendChild(box);
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',draw);
  else draw();
  window.addEventListener('load',draw);
  window.addEventListener('resize',draw);
})();
</script>
"""


ENTITY_ALT = {
    "–": "(?:–|&ndash;)", "—": "(?:—|&mdash;)", "·": "(?:·|&middot;)",
    "…": "(?:…|&hellip;)", "×": "(?:×|&times;)", "&": "(?:&|&amp;)",
    "'": "(?:'|&rsquo;|&#39;)", '"': "(?:\"|&ldquo;|&rdquo;|&quot;)",
    "→": "(?:→|&rarr;)", "≈": "(?:≈|&asymp;)", "−": "(?:−|&minus;)",
}


def _needle_re(needle):
    """visible_text() decodes entities; the source still has them. Match either,
    and let any run of whitespace stand in for a single space."""
    out = []
    for ch in needle:
        if ch in ENTITY_ALT:
            out.append(ENTITY_ALT[ch])
        elif ch.isspace():
            out.append(r"\s+")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _mark_text_nodes(fragment, needle, rule, sev, target, limit=1):
    """Wrap `needle` where it appears as visible text, never inside a tag or a style."""
    if not needle:
        return fragment, 0
    parts, done = re.split(r"(<[^>]+>)", fragment), 0
    in_skip = False
    for i, part in enumerate(parts):
        if part.startswith("<"):
            low = part.lower()
            if low.startswith("<style") or low.startswith("<script") or low.startswith("<svg"):
                in_skip = True
            elif low.startswith("</style") or low.startswith("</script") or low.startswith("</svg"):
                in_skip = False
            continue
        if in_skip or done >= limit:
            continue
        m = re.search(_needle_re(needle), part, flags=re.I)
        if not m:
            continue
        parts[i] = (part[:m.start()]
                    + '<mark class="lint-hit" data-sev="%s" title="%s" data-lint-target="%d">'
                       % (sev, rule, target)
                    + part[m.start():m.end()] + "</mark>" + part[m.end():])
        done += 1
    return "".join(parts), done


# Human labels: the tab says what the tell IS, not which function caught it.
TAB_LABEL = {
    "dark-ground": "Dark ground", "not-just-but": "Not just X but Y",
    "banned-word": "Marketing filler", "filler-phrase": "Says nothing",
    "nested-cards": "Cards in cards", "off-brand-gradient": "Off-brand gradient",
    "gradient-budget": "Two gradients", "ai-font": "Overused font",
    "title-emphasis": "Two emphases", "red-rose-type": "Wordmark as type",
    "dash-density": "Dash tic", "print-shadow": "Shadow in print",
    "gradient-clipped-text": "Prints grey", "css-dot-grid": "CSS dot grid",
    "accent-budget": "Accent overused", "deck-length": "Too long",
    "rule-of-three": "Rule of three", "font-allowlist": "Off-set font",
}


def _tab(f):
    """Tab text: the tell, plus a detail only when it adds something."""
    base = TAB_LABEL.get(f["rule"], f["rule"])
    d = (f.get("detail") or "").strip()
    if not d or d.lower() in base.lower() or base.lower() in d.lower():
        return base
    return "%s · %s" % (base, d)


def annotate(html, lint, findings):
    """Write a copy of the page with a box and a tab drawn on each flagged element."""
    by_unit = {}
    for f in findings:
        by_unit.setdefault(f["unit"], []).append(f)

    out, idx, meta = html, 0, []
    for label, frag in lint.units:
        new = frag
        for f in by_unit.get(label, []):
            tagged = False
            if f.get("match"):
                new, n = _mark_text_nodes(new, f["match"], f["rule"], f["severity"], idx)
                tagged = bool(n)
            if not tagged and f.get("anchor") and f["anchor"] in new:
                tag = f["anchor"]
                new = new.replace(tag, tag[:-1] + ' data-lint-target="%d">' % idx, 1)
                tagged = True
            if not tagged:
                continue        # stylesheet-level finding: bar only, nothing to box
            meta.append({"rule": f["rule"], "severity": f["severity"],
                         "label": _tab(f)})
            idx += 1
        if new != frag:
            out = out.replace(frag, new, 1)

    c = {s: sum(1 for f in findings if f["severity"] == s) for s in SEV_ORDER}
    unboxed = len(findings) - len(meta)
    bar = ('<div id="lint-bar">deck-lint<span class="e">%d error</span>'
           '<b>%d warn</b><span>%d note</span>%s</div>'
           % (c["error"], c["warn"], c["note"],
              ("<span>%d not on the page</span>" % unboxed) if unboxed else ""))
    data = "<script>window.__LINT__=%s;</script>" % json.dumps(meta, ensure_ascii=False)
    head = re.search(r"</head\s*>", out, flags=re.I)
    inject = ANNOTATE_CSS + data
    out = (out[:head.start()] + inject + out[head.start():]) if head else inject + out
    m = re.search(r"<body\b[^>]*>", out, flags=re.I)
    if m:
        out = out[:m.end()] + '<div id="lint-layer"></div>' + bar + out[m.end():]
    return out + ANNOTATE_JS


def main():
    ap = argparse.ArgumentParser(description="Deterministic AI-slop lint for HTML decks and docs.")
    ap.add_argument("file", nargs="+")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--only", choices=["visual", "copy"], help="one rule family")
    ap.add_argument("--min", choices=["note", "warn", "error"], default="note",
                    help="minimum severity to report")
    ap.add_argument("--annotate", metavar="OUT.html",
                    help="write a copy of the page with the findings drawn on it")
    args = ap.parse_args()

    worst, payload = 0, []
    for path in args.file:
        try:
            html = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            print("deck-lint: %s" % e, file=sys.stderr)
            worst = max(worst, 2)
            continue
        lint = Lint(html, path)
        found = lint.run()
        if args.only:
            found = [f for f in found if f["family"] == args.only]
        found = [f for f in found if SEV_ORDER[f["severity"]] >= SEV_ORDER[args.min]]
        for f in found:
            worst = max(worst, 2 if f["severity"] == "error" else
                        1 if f["severity"] == "warn" else 0)
        if args.annotate:
            open(args.annotate, "w", encoding="utf-8").write(annotate(html, lint, found))
            print("  annotated copy → %s" % args.annotate)
        if args.json:
            payload.append({"file": path, "units": len(lint.units),
                            "mode": "deck" if lint.is_deck else "doc", "findings": found})
        else:
            print(report(found, path, len(lint.units), lint.is_deck))
    if args.json:
        print(json.dumps(payload if len(payload) > 1 else payload[0], indent=2,
                         ensure_ascii=False))
    return worst


if __name__ == "__main__":
    sys.exit(main())
