"""Deterministic source-span recovery for Result X citations, not grading."""
import io
import re
import tokenize

SPACE = r"[ \t\r\n\f\v]+"


def source_quote(source: str, quote: str, path: str) -> str | None:
    """Restore ASCII whitespace runs only; retain every non-whitespace character.

    Existing exact substrings are unchanged. Otherwise return the first matching
    original span, never normalized/reconstructed source content. Whitespace in
    Python string literals is data, so changes there are not formatting repairs.
    """
    if not isinstance(quote, str) or not quote.strip():
        return None
    if quote in source:
        return quote
    quote = quote.strip(" \t\r\n\f\v")
    parts = re.split(f"({SPACE})", quote)
    pattern = "".join(f"({SPACE})" if i % 2 else re.escape(part)
                      for i, part in enumerate(parts))
    literal_spans = []
    if path.endswith(".py"):
        offsets = [0]
        for line in source.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        try:
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)):
                    literal_spans.append((offsets[token.start[0] - 1] + token.start[1],
                                          offsets[token.end[0] - 1] + token.end[1]))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return None  # Exact quotes still work for incomplete code sources.
    for match in re.finditer(pattern, source):
        changed = [match.span(i) for i, value in enumerate(parts[1::2], 1)
                   if match.group(i) != value]
        if any(a < end and start < b for a, b in changed for start, end in literal_spans):
            continue
        return match.group(0)
    return None
