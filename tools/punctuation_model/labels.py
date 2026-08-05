from __future__ import annotations

from enum import IntEnum


class PunctuationLabel(IntEnum):
    NONE = 0
    COMMA = 1
    PERIOD = 2
    QUESTION = 3
    EXCLAMATION = 4
    COLON = 5
    SEMICOLON = 6
    ENUM_COMMA = 7


class QuoteLabel(IntEnum):
    NONE = 0
    OPEN = 1
    CLOSE = 2


PUNCTUATION_NAMES = tuple(label.name for label in PunctuationLabel)
QUOTE_NAMES = tuple(label.name for label in QuoteLabel)

PUNCTUATION_BY_GLYPH = {
    ",": PunctuationLabel.COMMA,
    "，": PunctuationLabel.COMMA,
    ".": PunctuationLabel.PERIOD,
    "。": PunctuationLabel.PERIOD,
    "．": PunctuationLabel.PERIOD,
    "?": PunctuationLabel.QUESTION,
    "？": PunctuationLabel.QUESTION,
    "!": PunctuationLabel.EXCLAMATION,
    "！": PunctuationLabel.EXCLAMATION,
    ":": PunctuationLabel.COLON,
    "：": PunctuationLabel.COLON,
    ";": PunctuationLabel.SEMICOLON,
    "；": PunctuationLabel.SEMICOLON,
    "、": PunctuationLabel.ENUM_COMMA,
}

OPEN_QUOTES = frozenset({"「", "『", "“"})
CLOSE_QUOTES = frozenset({"」", "』", "”"})
AMBIGUOUS_QUOTES = frozenset({'"'})

ZH_TW_GLYPHS = {
    PunctuationLabel.NONE: "",
    PunctuationLabel.COMMA: "，",
    PunctuationLabel.PERIOD: "。",
    PunctuationLabel.QUESTION: "？",
    PunctuationLabel.EXCLAMATION: "！",
    PunctuationLabel.COLON: "：",
    PunctuationLabel.SEMICOLON: "；",
    PunctuationLabel.ENUM_COMMA: "、",
}

ENGLISH_GLYPHS = {
    PunctuationLabel.NONE: "",
    PunctuationLabel.COMMA: ",",
    PunctuationLabel.PERIOD: ".",
    PunctuationLabel.QUESTION: "?",
    PunctuationLabel.EXCLAMATION: "!",
    PunctuationLabel.COLON: ":",
    PunctuationLabel.SEMICOLON: ";",
    PunctuationLabel.ENUM_COMMA: ",",
}

QUOTE_GLYPHS = {
    "zh-tw": {QuoteLabel.OPEN: "「", QuoteLabel.CLOSE: "」"},
    "english": {QuoteLabel.OPEN: "“", QuoteLabel.CLOSE: "”"},
}


def render_boundary(punctuation: int, quote: int, style: str) -> str:
    if style not in {"zh-tw", "english"}:
        raise ValueError(f"unsupported punctuation style: {style}")
    punctuation_label = PunctuationLabel(punctuation)
    quote_label = QuoteLabel(quote)
    glyphs = ZH_TW_GLYPHS if style == "zh-tw" else ENGLISH_GLYPHS
    quote_glyph = "" if quote_label is QuoteLabel.NONE else QUOTE_GLYPHS[style][quote_label]
    return glyphs[punctuation_label] + quote_glyph
