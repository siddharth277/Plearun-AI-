"""Rule 1: a cited quote must be an exact substring of the cited row's raw text."""


def quote_for(row):
    if row.hint and row.hint in row.raw:
        return row.hint
    return row.raw.strip()[:300]


def verify_quote(registry, file, row_no, quote):
    row = registry.get((file, row_no))
    return bool(row and isinstance(quote, str) and quote and quote in row.raw)
