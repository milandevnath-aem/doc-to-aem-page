"""Derive JCR paths from a document's own page URL.

The client's documents state the live URL of the page they describe. Its path
segments are the site's section hierarchy, so one URL determines three things:
where the page lands under the content root, which DAM folder its FAQ
fragments land in, and which tags those fragments carry.

    https://www.iffcotokio.co.in/health-insurance/family-health-insurance/who-should-buy
      hierarchy = [health-insurance, family-health-insurance]
      slug      = who-should-buy

Pure string handling -- no I/O, no config objects. Callers pass the roots.
"""

from mapping.slug import slugify


def split_site_url(url: str, origin: str) -> tuple[list[str], str]:
    """Split a site URL into (hierarchy_segments, page_slug).

    Returns ([], "") for an empty URL or one that is not on `origin` -- the
    caller treats that as "no path could be derived" rather than guessing a
    location for the page.
    """
    url = (url or "").strip()
    origin = (origin or "").rstrip("/")
    if not url or not origin:
        return [], ""
    if not url.lower().startswith(origin.lower() + "/"):
        return [], ""
    path = url[len(origin):].split("?", 1)[0].split("#", 1)[0].strip("/")
    if not path:
        return [], ""
    segments = [s for s in path.split("/") if s]
    # a trailing .html is a rendering suffix, not part of the node name
    if segments[-1].lower().endswith(".html"):
        segments[-1] = segments[-1][: -len(".html")]
    slugged = [slugify(s) for s in segments]
    return slugged[:-1], slugged[-1]


def page_path(content_root: str, hierarchy: list[str], slug: str) -> str:
    return "/".join([content_root.rstrip("/"), *hierarchy, slug])


def cf_folder_path(cf_root: str, hierarchy: list[str]) -> str:
    return "/".join([cf_root.rstrip("/"), *hierarchy])


def faq_categories(namespace: str, hierarchy: list[str]) -> list[str]:
    """One tag per hierarchy segment, matching the client's existing
    fragments (folder segments and tags are 1:1 there)."""
    return [f"{namespace}:{segment}" for segment in hierarchy]
