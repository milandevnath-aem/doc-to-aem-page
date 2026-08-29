"""HTTP client that fetches a PageTypeConfig Content Fragment from AEM.

Uses AEM's out-of-the-box Sling Content Fragment endpoint:

    GET <base_url>/<cf_path>/jcr:content/data/master.json

Zero custom AEM code required -- the endpoint exists on any AEM instance
where the CF is authored and readable by the caller.

`fetch_or_cached` is the convenience wrapper the UI drives: try the network,
fall back to the on-disk cache if AEM is unreachable, raise only when
neither is available.
"""

import httpx

from aem_client.config import (
    PageTypeConfig,
    load_page_type_config,
    save_page_type_config,
)


class FetchError(RuntimeError):
    """Raised for any AEM-fetch failure -- HTTP != 200, network down,
    JSON malformed, missing required CF fields. Wrap the original cause."""


def _build_url(base_url: str, cf_path: str) -> str:
    # normalize -- no trailing slash on base, one leading slash on path
    base = base_url.rstrip("/")
    path = cf_path if cf_path.startswith("/") else f"/{cf_path}"
    return f"{base}{path}/jcr:content/data/master.json"


def fetch_page_type_config(
    client: httpx.Client,
    base_url: str,
    cf_path: str,
    auth_token: str | None = None,
) -> PageTypeConfig:
    url = _build_url(base_url, cf_path)
    headers = {"authorization": auth_token} if auth_token else None
    try:
        response = client.get(url, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        raise FetchError(f"AEM fetch failed: {exc}") from exc
    if response.status_code != 200:
        raise FetchError(f"AEM returned {response.status_code} for {url}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError(f"AEM response was not JSON: {exc}") from exc
    try:
        return PageTypeConfig.from_dict(payload)
    except KeyError as exc:
        raise FetchError(f"AEM CF payload missing required field: {exc}") from exc


def fetch_or_cached(
    client: httpx.Client,
    base_url: str,
    cf_path: str,
    cache_path,
    auth_token: str | None = None,
) -> PageTypeConfig:
    """Try live AEM first, cache on success, fall back to cache on failure.
    Raise FetchError only if neither AEM nor a cache is available."""
    try:
        cfg = fetch_page_type_config(client, base_url, cf_path, auth_token)
    except FetchError:
        if cache_path.exists():
            return load_page_type_config(cache_path)
        raise
    save_page_type_config(cfg, cache_path)
    return cfg
