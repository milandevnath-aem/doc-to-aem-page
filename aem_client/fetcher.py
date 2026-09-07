"""HTTP client that fetches a PageTypeConfig Content Fragment from AEM.

Uses AEM's out-of-the-box Sling Content Fragment endpoint:

    GET <base_url>/<cf_path>/jcr:content/data/master.json

Zero custom AEM code required -- the endpoint exists on any AEM instance
where the CF is authored and readable by the caller.

`fetch_or_cached` is the convenience wrapper the UI drives: try the network,
fall back to the on-disk cache if AEM is unreachable, raise only when
neither is available.
"""

import json

import httpx

from aem_client.config import (
    PageTypeConfig,
    load_page_type_config,
    save_page_type_config,
)

# CF Model uses one JSON Object field named `config` that wraps the whole
# PageTypeConfig JSON. AEM 6.5 serializes JSON Object fields as escaped
# JSON strings on the wire; other versions may return an embedded object.
# Accept both.
_WRAPPER_FIELD = "config"


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
    payload = _unwrap_config(payload)
    try:
        return PageTypeConfig.from_dict(payload)
    except KeyError as exc:
        raise FetchError(f"AEM CF payload missing required field: {exc}") from exc


def _unwrap_config(payload: dict) -> dict:
    """Real CFs authored with one JSON Object field named `config` return
    that object wrapped. AEM 6.5 serializes JSON Object as an escaped
    JSON string; other versions embed the dict directly. Handle both, or
    fall through to the flat-field payload used by our sample CFs."""
    if _WRAPPER_FIELD not in payload:
        return payload
    inner = payload[_WRAPPER_FIELD]
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError as exc:
            raise FetchError(
                f"AEM CF '{_WRAPPER_FIELD}' field is not valid JSON: {exc}"
            ) from exc
    if not isinstance(inner, dict):
        raise FetchError(
            f"AEM CF '{_WRAPPER_FIELD}' field must be an object or JSON string"
        )
    return inner


def fetch_or_cached(
    client: httpx.Client,
    base_url: str,
    cf_path: str,
    cache_path,
    auth_token: str | None = None,
) -> PageTypeConfig:
    """Try live AEM first, cache on success, fall back to cache on failure.
    Raise FetchError only if neither AEM nor a cache is available.

    When the fetch succeeds AND the CF declares a templatePath, also pulls
    the template's initial jcr:content tree so the mapper can use it as
    the layout base. Template-fetch failure is non-fatal -- pipeline just
    falls back to the flat-layout mapper on that run.
    """
    try:
        cfg = fetch_page_type_config(client, base_url, cf_path, auth_token)
    except FetchError:
        if cache_path.exists():
            return load_page_type_config(cache_path)
        raise
    if cfg.template_path:
        try:
            cfg.template_initial_content = fetch_template_initial(
                client, base_url, cfg.template_path, auth_token,
            )
        except FetchError:
            # keep going without template; mapper falls back to flat layout
            cfg.template_initial_content = {}
    save_page_type_config(cfg, cache_path)
    return cfg


def fetch_template_initial(
    client: httpx.Client,
    base_url: str,
    template_path: str,
    auth_token: str | None = None,
) -> dict:
    """Fetch <template>/initial/jcr:content.infinity.json and convert to
    the internal node-tree shape. Used as the layout base for pages
    generated from a template that ships default children."""
    from mapping.template_convert import aem_json_to_node
    base = base_url.rstrip("/")
    path = template_path if template_path.startswith("/") else f"/{template_path}"
    url = f"{base}{path}/initial/jcr:content.infinity.json"
    headers = {"authorization": auth_token} if auth_token else None
    try:
        response = client.get(url, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        raise FetchError(f"AEM template fetch failed: {exc}") from exc
    if response.status_code != 200:
        raise FetchError(f"AEM returned {response.status_code} for {url}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError(f"AEM template response was not JSON: {exc}") from exc
    return aem_json_to_node("jcr:content", payload)
