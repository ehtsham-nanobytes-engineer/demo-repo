r"""Markdown -> LinkedIn Unicode.

LinkedIn's composer accepts plain text only: `**bold**` shows up as literal
asterisks, not bold. The trick every LinkedIn tool uses is to swap the ASCII
letters for the Unicode Mathematical Alphanumeric Symbols block (U+1D400...),
which renders as bold/italic glyphs in any feed without any markup.

`to_linkedin_unicode()` is the whole public surface: markdown in, styled plain
text out. Anything with no styled counterpart (punctuation, emoji, spaces, and
the digits in italic — the block has no italic digits) is passed through
untouched, so hashtags, arrows and emoji survive.
"""

import re
import unicodedata

# Sans-serif faces, not serif: they sit closer to LinkedIn's own UI font.
# Each entry is the codepoint the face's "A", "a" and "0" map to. A None means
# the block has no such range and those characters stay as they are.
_FACES = {
    "bold": (0x1D5D4, 0x1D5EE, 0x1D7EC),
    "italic": (0x1D608, 0x1D622, None),
    "bold_italic": (0x1D63C, 0x1D656, None),
}

# Markdown emphasis, longest marker first so ***x*** never parses as *x*.
# The underscore forms require a non-word character on each side, otherwise
# snake_case_names inside a post would come out half-italic.
_EMPHASIS = re.compile(
    r"""
      (?<!\\)\*\*\*(?P<bi>\S(?:[^*\n]*\S)?)(?<!\\)\*\*\*
    | (?<!\\)\*\*(?P<b>\S(?:[^*\n]*\S)?)(?<!\\)\*\*
    | (?<!\\)\*(?P<i>\S(?:[^*\n]*\S)?)(?<!\\)\*
    | (?<![\w\\])___(?P<bi2>\S(?:[^_\n]*\S)?)(?<!\\)___(?!\w)
    | (?<![\w\\])__(?P<b2>\S(?:[^_\n]*\S)?)(?<!\\)__(?!\w)
    | (?<![\w\\])_(?P<i2>\S(?:[^_\n]*\S)?)(?<!\\)_(?!\w)
    """,
    re.VERBOSE,
)

_INLINE_CODE = re.compile(r"(?<!\\)`+([^`\n]+?)`+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*#*$")
_BULLET = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.*)$")
_ORDERED = re.compile(r"^(?P<indent>\s*)\d+[.)]\s+(?P<text>.*)$")
_LINK = re.compile(r"""(?<!\\)\[(?P<text>[^\]\n]+)\]\((?P<url>[^)\s]+)(?:\s+"[^"]*")?\)""")
_QUOTE = re.compile(r"^\s{0,3}>\s?")
_RULE = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")
_ESCAPED = re.compile(r"\\([*_`\[\]#\\])")

# Markdown's two line-break conventions: a trailing backslash, or the classic
# "two spaces at the end of the line". Both are invisible instructions to a
# markdown renderer, and both come out of LinkedIn as literal trailing blanks —
# which is what puts that ragged whitespace after every line of a draft.
_HARD_BREAK = re.compile(r"(?:\\|\s{2,})$")

# Three or more newlines in a row, i.e. more than one blank line between
# paragraphs. LinkedIn keeps every one of them and the post looks gappy.
_BLANK_RUN = re.compile(r"\n{3,}")

# Zero-width characters some models emit, invisible here but real characters to
# LinkedIn's 3000-character counter. U+200D (ZWJ) is deliberately NOT in this
# set: it is what holds multi-part emoji like 👩‍💻 together.
_INVISIBLE = re.compile("[\u200b\u200c\u2060\ufeff]")

# An escape sequence the model typed out as text instead of pressing return:
# a literal backslash followed by n, r or t. LinkedIn has no escape syntax, so
# these reach the feed as the visible characters "\n" rather than a line break.
# The doubled forms ("\\n") show up when a draft has been through JSON encoding
# one time too many, so the backslash run is matched greedily.
_LITERAL_BREAK = re.compile(r"\\+r\\+n|\\+[nrNR]")
_LITERAL_TAB = re.compile(r"\\+t")

# A line whose leading whitespace carries no meaning: anything that is not an
# indented list item. LinkedIn honours those spaces, so " #AI" sits one space in
# from the rest of the post.
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")


def stylize(text: str, face: str) -> str:
    """Map the ASCII letters and digits in `text` onto one Unicode face.

    `face` is one of "bold", "italic", "bold_italic". Characters the face has
    no glyph for come back unchanged.
    """
    upper, lower, digit = _FACES[face]
    out = []
    for char in text:
        if "A" <= char <= "Z":
            out.append(chr(upper + ord(char) - ord("A")))
        elif "a" <= char <= "z":
            out.append(chr(lower + ord(char) - ord("a")))
        elif digit and "0" <= char <= "9":
            out.append(chr(digit + ord(char) - ord("0")))
        else:
            out.append(char)
    return "".join(out)


def to_linkedin_unicode(post: str, *, bullet: str = "\u2192", heading_face: str = "bold") -> str:
    r"""Convert a markdown-flavoured post into LinkedIn-ready plain text.

    - `**bold**` / `__bold__` -> bold sans-serif letters and digits
    - `*italic*` / `_italic_` -> italic letters
    - `***both***` / `___both___` -> bold italic letters
    - `# Heading` -> the heading text in `heading_face`, markers dropped
    - `- item` / `1. item` -> `bullet` prefix (LinkedIn eats list markup)
    - `[text](url)` -> `text (url)`, `` `code` `` -> code, `> quote` -> quote
    - `\*` escapes survive as literal asterisks
    - `---` rules are dropped; they have no plain-text equivalent

    Line endings are normalised on the way out. A literal `\n`, `\r\n` or `\t`
    written out as characters becomes the real break it stands for — LinkedIn
    has no escape syntax and would otherwise show the backslash — so none of
    those two-character sequences survive into the result. CRLF and the Unicode
    separators become plain newlines, markdown's trailing-backslash and
    two-trailing-space hard breaks are removed along with any other trailing whitespace, runs of blank
    lines collapse to a single one, zero-width characters are dropped (except
    the ZWJ that holds multi-part emoji together), and the post is trimmed top
    and bottom. LinkedIn preserves whitespace literally, so every one of those
    would otherwise show up in the feed.

    Everything else — emoji, hashtags, blank lines, non-ASCII text — is left
    exactly as written, and the result is NFC-normalised so the styled glyphs
    survive the round trip through the LinkedIn API.
    """
    lines = []
    # splitlines() is the line-ending normaliser: CRLF, a bare CR and the Unicode
    # separators U+2028/U+2029 all come back as ordinary lines here, so the join
    # below re-emits the whole post with plain "\n" regardless of what came in.
    for line in _unescape_breaks(post).splitlines():
        line = _HARD_BREAK.sub("", line).rstrip()
        if not _LIST_ITEM.match(line):
            line = line.lstrip()

        if not line:
            lines.append("")
            continue
        if _RULE.match(line):
            continue

        line = _QUOTE.sub("", line)

        heading = _HEADING.match(line)
        if heading:
            lines.append(stylize(_inline(heading.group("text")), heading_face))
            continue

        prefix = ""
        item = _BULLET.match(line) or _ORDERED.match(line)
        if item:
            prefix = f"{item.group('indent')}{bullet} "
            line = item.group("text")

        # rstrip again: _inline can leave a trailing space behind, and an empty
        # bullet would otherwise keep the arrow's own trailing space.
        lines.append((prefix + _inline(line)).rstrip())

    text = _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()

    # Second pass over the finished text. Unescaping markdown can uncover an
    # escape sequence that was hidden behind a backslash on the way in
    # (`\\n` -> `\n`), and this is the last point before the post leaves the
    # service, so the guarantee is made here rather than assumed: no literal
    # escape sequence reaches LinkedIn, whatever the model wrote.
    text = _unescape_breaks(text)
    text = _BLANK_RUN.sub("\n\n", "\n".join(l.rstrip() for l in text.splitlines()))
    return unicodedata.normalize("NFC", text.strip())


def _unescape_breaks(text: str) -> str:
    r"""Turn escape sequences written out as characters into real whitespace.

    `\n`, `\r\n`, `\N`, `\t` and their double-escaped forms (`\\n`, from a draft
    that went through JSON encoding one time too many) become an actual line
    break or space. LinkedIn has no escape syntax: left alone, these show up in
    the feed as a visible backslash followed by a letter.
    """
    text = _INVISIBLE.sub("", text)
    text = _LITERAL_BREAK.sub("\n", text)
    return _LITERAL_TAB.sub(" ", text)


def _inline(line: str) -> str:
    """Apply the inline conversions (links, code, emphasis, escapes) to one line."""
    line = _LINK.sub(lambda m: f"{m.group('text')} ({m.group('url')})", line)
    line = _INLINE_CODE.sub(r"\1", line)
    line = _EMPHASIS.sub(_style_match, line)
    return _ESCAPED.sub(r"\1", line)


def _style_match(match: re.Match) -> str:
    """Render one emphasis match; nested emphasis is resolved inside-out."""
    for group, face in (
        ("bi", "bold_italic"), ("bi2", "bold_italic"),
        ("b", "bold"), ("b2", "bold"),
        ("i", "italic"), ("i2", "italic"),
    ):
        inner = match.group(group)
        if inner is not None:
            return stylize(_EMPHASIS.sub(_style_match, inner), face)
    return match.group(0)
