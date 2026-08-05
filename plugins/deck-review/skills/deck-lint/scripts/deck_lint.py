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

    def add(self, rule, sev, unit, msg, evidence=None, family="visual"):
        self.findings.append({
            "rule": rule, "severity": sev, "family": family, "unit": unit,
            "message": msg, "evidence": (evidence or "")[:160],
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
                         "no dark slide, no dark band, no dark card.", val)

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
                    self.add("title-emphasis", "note", label,
                             "Title carries no purple phrase. Every title should have exactly one.",
                             visible_text(t)[:80])
                elif n > 1:
                    self.add("title-emphasis", "warn", label,
                             "Title carries %d emphasised phrases. Exactly one." % n,
                             visible_text(t)[:80])

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
                             visible_text(inner)[:70])
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
                             text[max(0, m.start() - 45):m.start() + 55], family="copy")
                    break
            for p in FILLER_PHRASES:
                if p in low:
                    i = low.index(p)
                    self.add("filler-phrase", "warn", label,
                             "'%s' adds no fact. If a sentence carries none, it goes." % p,
                             text[max(0, i - 40):i + 60], family="copy")
            for m in re.finditer(r"\bnot (?:just|only|merely)\b[^.]{0,60}?\bbut\b", low):
                self.add("not-just-but", "error", label,
                         "'not just X but Y' is the single most recognisable AI construction.",
                         text[m.start():m.end() + 20], family="copy")
            # Word on both sides: skips numeric ranges and the "–" that marks an
            # empty table cell.
            for m in re.finditer(r"[A-Za-zÀ-ž]\s[—–]\s[A-Za-zÀ-ž]", text):
                self.add("dash-punctuation", "warn", label,
                         "Dash used as punctuation. Use a comma, a colon or a new sentence. "
                         "A numeric range is the only dash allowed.",
                         text[max(0, m.start() - 45):m.start() + 55], family="copy")
                break
            if re.search(r"\b(three|four|five) (?:key |core |main )?(?:pillars|reasons|"
                         r"principles|steps|ways)\b", low):
                self.add("rule-of-three", "note", label,
                         "A numbered list announced by its own count usually exists for "
                         "rhythm rather than because the count is real.", family="copy")

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


def main():
    ap = argparse.ArgumentParser(description="Deterministic AI-slop lint for HTML decks and docs.")
    ap.add_argument("file", nargs="+")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--only", choices=["visual", "copy"], help="one rule family")
    ap.add_argument("--min", choices=["note", "warn", "error"], default="note",
                    help="minimum severity to report")
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
