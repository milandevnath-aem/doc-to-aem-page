"""JCR-safe slug helper: title / URL -> node name.

JCR node names cannot contain `/` (path separator) or `:` (namespace
separator), and while broader chars are technically legal, sticking to
`[a-z0-9-]` is universally safe and matches what AEM's own slug helpers
produce.
"""

import re

_ALLOWED = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, replace runs of non-alphanumeric chars with `-`, strip
    edges. Empty result falls back to the literal string 'page' so we never
    produce an empty JCR node name."""
    slug = _ALLOWED.sub("-", (text or "").lower()).strip("-")
    return slug or "page"
