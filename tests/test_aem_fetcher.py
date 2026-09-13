"""AEM CF fetcher.

Hits AEM's out-of-the-box `.model.json` endpoint on a Content Fragment,
maps the CF fields to a `PageTypeConfig`, and caches the result to disk.

Falls back to the bundled sample when AEM is unreachable OR when explicitly
asked to load samples -- lets the pipeline stay productive without a live
AEM.
"""


import httpx
import pytest

from aem_client.config import PageTypeConfig
from aem_client.fetcher import (
    FetchError,
    cf_exists,
    fetch_and_cache,
    fetch_page_type_config,
    fetch_or_cached,
)

# Shape of the JSON returned by AEM's Content Fragment .model.json endpoint
# (simplified but structurally accurate).
CF_PAYLOAD = {
    "type": "article",
    "templatePath": "/conf/iffcotokio/settings/wcm/templates/article",
    "contentRoot": "/content/iffcotokio/en/articles",
    "damRoot": "/content/dam/iffcotokio/articles",
    "pageNameStrategy": "slug_from_meta_title",
    "componentMappings": {
        "page-description": "iffcotokio/components/text",
        "faq": "iffcotokio/components/faq",
    },
    "jcr:primaryType": "nt:unstructured",
}


def _mock_transport(status: int = 200, payload: dict | None = None,
                    err: type[Exception] | None = None):
    """Build an httpx MockTransport that returns the given response or raises."""
    def handler(request: httpx.Request) -> httpx.Response:
        if err is not None:
            raise err("mock network error")
        return httpx.Response(status, json=payload or {})
    return httpx.MockTransport(handler)


def test_fetch_returns_page_type_config_from_cf_json():
    transport = _mock_transport(200, CF_PAYLOAD)
    with httpx.Client(transport=transport) as client:
        cfg = fetch_page_type_config(
            client,
            base_url="http://localhost:4502",
            cf_path="/content/dam/iffcotokio/site-config/article",
        )
    assert isinstance(cfg, PageTypeConfig)
    assert cfg.type == "article"
    assert cfg.template_path == "/conf/iffcotokio/settings/wcm/templates/article"
    assert cfg.component_mappings["faq"] == "iffcotokio/components/faq"


def test_fetch_hits_the_expected_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=CF_PAYLOAD)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetch_page_type_config(
            client,
            base_url="http://localhost:4502",
            cf_path="/content/dam/x/article",
            auth_token="Basic YWRtaW46YWRtaW4=",
        )

    assert seen["method"] == "GET"
    # Sling's Content Fragment JSON endpoint
    assert seen["url"] == (
        "http://localhost:4502/content/dam/x/article/jcr:content/data/master.json"
    )
    assert seen["auth"] == "Basic YWRtaW46YWRtaW4="


def test_fetch_raises_on_non_200():
    transport = _mock_transport(status=401, payload={"error": "unauthorized"})
    with httpx.Client(transport=transport) as client:
        with pytest.raises(FetchError) as excinfo:
            fetch_page_type_config(
                client, "http://localhost:4502",
                "/content/dam/x/article",
            )
    assert "401" in str(excinfo.value)


def test_fetch_raises_on_network_error():
    transport = _mock_transport(err=httpx.ConnectError)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(FetchError):
            fetch_page_type_config(
                client, "http://localhost:4502",
                "/content/dam/x/article",
            )


def test_fetch_or_cached_writes_cache_on_success(tmp_path):
    cache_path = tmp_path / "article.pagetypeconfig.json"
    transport = _mock_transport(200, CF_PAYLOAD)
    with httpx.Client(transport=transport) as client:
        cfg = fetch_or_cached(
            client, "http://localhost:4502",
            "/content/dam/x/article", cache_path,
        )
    assert cfg.type == "article"
    assert cache_path.exists()
    # cache is a plain snake_case JSON, re-loadable without another fetch
    text = cache_path.read_text(encoding="utf-8")
    assert '"template_path"' in text


def test_fetch_or_cached_falls_back_to_cache_on_network_failure(tmp_path):
    """A prior successful fetch left a cache file. Network is now down.
    fetch_or_cached must return the cached copy instead of raising."""
    cache_path = tmp_path / "article.pagetypeconfig.json"
    # seed the cache with a prior fetch
    transport_ok = _mock_transport(200, CF_PAYLOAD)
    with httpx.Client(transport=transport_ok) as client:
        fetch_or_cached(client, "http://localhost:4502",
                        "/content/dam/x/article", cache_path)
    # now simulate a network failure
    transport_down = _mock_transport(err=httpx.ConnectError)
    with httpx.Client(transport=transport_down) as client:
        cfg = fetch_or_cached(client, "http://localhost:4502",
                              "/content/dam/x/article", cache_path)
    assert cfg.type == "article"


def test_fetch_unwraps_config_field_when_dict():
    """Real AEM CFs authored with a single JSON Object field named
    `config` return that object wrapped under `config` in the payload."""
    wrapped = {
        "jcr:primaryType": "nt:unstructured",
        "config": CF_PAYLOAD,
    }
    transport = _mock_transport(200, wrapped)
    with httpx.Client(transport=transport) as client:
        cfg = fetch_page_type_config(
            client, "http://localhost:5402",
            "/content/dam/x/article-page-config",
        )
    assert cfg.type == "article"
    assert cfg.template_path == "/conf/iffcotokio/settings/wcm/templates/article"


def test_fetch_unwraps_config_field_when_json_string():
    """AEM 6.5 serializes CF JSON Object fields as escaped JSON strings on
    the wire, not as embedded objects. The fetcher must json.loads() them
    before handing to PageTypeConfig.from_dict()."""
    import json as _json
    wrapped = {
        "jcr:primaryType": "nt:unstructured",
        "config": _json.dumps(CF_PAYLOAD),   # string, not dict
        "config@LastModified": "Tue Sep 08 2026 00:20:23 GMT+0530",
    }
    transport = _mock_transport(200, wrapped)
    with httpx.Client(transport=transport) as client:
        cfg = fetch_page_type_config(
            client, "http://localhost:5402",
            "/content/dam/x/article-page-config",
        )
    assert cfg.type == "article"
    assert cfg.component_mappings["faq"] == "iffcotokio/components/faq"


def test_fetch_raises_when_config_field_is_malformed_json_string():
    wrapped = {
        "jcr:primaryType": "nt:unstructured",
        "config": "not valid json {",
    }
    transport = _mock_transport(200, wrapped)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(FetchError) as excinfo:
            fetch_page_type_config(
                client, "http://localhost:5402",
                "/content/dam/x/article-page-config",
            )
    assert "config" in str(excinfo.value).lower()


def test_fetch_or_cached_also_pulls_template_initial_content(tmp_path):
    """After fetching the CF, `fetch_or_cached` also GETs the template's
    initial jcr:content and stores the converted node tree on the config
    so the mapper can use it as the layout base."""
    cache_path = tmp_path / "article.pagetypeconfig.json"
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if url.endswith("/jcr:content/data/master.json"):
            return httpx.Response(200, json=CF_PAYLOAD)
        if url.endswith("/initial/jcr:content.infinity.json"):
            return httpx.Response(200, json={
                "jcr:primaryType": "cq:PageContent",
                "sling:resourceType": "site/components/page",
                "root": {
                    "jcr:primaryType": "nt:unstructured",
                    "sling:resourceType": "wcm/foundation/components/responsivegrid",
                },
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        cfg = fetch_or_cached(
            client, "http://localhost:5402",
            "/content/dam/x/article", cache_path,
        )
    # both URLs hit
    assert any(u.endswith("/data/master.json") for u in seen_urls)
    assert any(u.endswith("/initial/jcr:content.infinity.json") for u in seen_urls)
    # template stored on the config in converted-tree shape
    assert cfg.template_initial_content["name"] == "jcr:content"
    root = next(c for c in cfg.template_initial_content["children"] if c["name"] == "root")
    assert root["properties"]["sling:resourceType"] == "wcm/foundation/components/responsivegrid"


def test_fetch_or_cached_survives_template_fetch_failure(tmp_path):
    """CF fetches fine but template endpoint 404s -- fall back to no
    template, don't kill the whole pipeline."""
    cache_path = tmp_path / "article.pagetypeconfig.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/jcr:content/data/master.json"):
            return httpx.Response(200, json=CF_PAYLOAD)
        return httpx.Response(404)  # template endpoint

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        cfg = fetch_or_cached(
            client, "http://localhost:5402",
            "/content/dam/x/article", cache_path,
        )
    assert cfg.type == "article"
    assert cfg.template_initial_content == {}


def test_fetch_or_cached_raises_when_no_network_and_no_cache(tmp_path):
    cache_path = tmp_path / "article.pagetypeconfig.json"
    transport = _mock_transport(err=httpx.ConnectError)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(FetchError):
            fetch_or_cached(client, "http://localhost:4502",
                            "/content/dam/x/article", cache_path)


# ---- Task 8: CF collision probe ----

def test_cf_exists_reports_yes_on_200():
    def handler(request):
        return httpx.Response(200, json={"jcr:primaryType": "dam:Asset"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "yes"


def test_cf_exists_reports_no_on_404():
    def handler(request):
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "no"


def test_cf_exists_reports_unknown_when_aem_unreachable():
    """The package must still build offline; the report says so honestly
    rather than claiming the path is free."""
    def handler(request):
        raise httpx.ConnectError("refused")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "unknown"


def test_cf_exists_reports_unknown_on_unexpected_status():
    def handler(request):
        return httpx.Response(500)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "unknown"


def test_cf_exists_reports_unknown_on_malformed_base_url():
    """A fat-fingered aem_base_url (e.g. a non-numeric port) raises
    httpx.InvalidURL during URL construction, before any transport is
    touched -- no mock needed. Must degrade to "unknown", not crash."""
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert cf_exists(client, "http://localhost:abc", "/content/dam/x/q") == "unknown"


def test_cf_exists_reports_unknown_on_none_cf_path():
    """cf_path=None raises AttributeError on .startswith -- the contract is
    unconditional "never raises", so this must also degrade to "unknown"."""
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert cf_exists(client, "http://localhost:4502", None) == "unknown"


# ---------- explicit fetch must fail loudly ----------

def test_fetch_and_cache_raises_on_401_even_when_a_cache_exists(tmp_path):
    """An operator clicking "Fetch config" asked to talk to AEM. When AEM
    rejects them, silently handing back a months-old cache makes the click
    look like it worked and leaves the pipeline running on stale config --
    the exact failure that shipped an empty siteOrigin to a real batch."""
    cache_path = tmp_path / "article.pagetypeconfig.json"
    with httpx.Client(transport=_mock_transport(200, CF_PAYLOAD)) as client:
        fetch_and_cache(client, "http://localhost:4502",
                        "/content/dam/x/article", cache_path)
    assert cache_path.exists()

    with httpx.Client(transport=_mock_transport(401)) as client:
        with pytest.raises(FetchError) as excinfo:
            fetch_and_cache(client, "http://localhost:4502",
                            "/content/dam/x/article", cache_path)
    assert "401" in str(excinfo.value)


def test_fetch_and_cache_leaves_the_existing_cache_untouched_on_failure(tmp_path):
    """A failed fetch must not damage the config the pipeline is still
    using -- the operator keeps working while they fix their credentials."""
    cache_path = tmp_path / "article.pagetypeconfig.json"
    with httpx.Client(transport=_mock_transport(200, CF_PAYLOAD)) as client:
        fetch_and_cache(client, "http://localhost:4502",
                        "/content/dam/x/article", cache_path)
    before = cache_path.read_text(encoding="utf-8")

    with httpx.Client(transport=_mock_transport(err=httpx.ConnectError)) as client:
        with pytest.raises(FetchError):
            fetch_and_cache(client, "http://localhost:4502",
                            "/content/dam/x/article", cache_path)
    assert cache_path.read_text(encoding="utf-8") == before


def test_fetch_or_cached_still_falls_back_so_offline_runs_keep_working(tmp_path):
    """The read path keeps its fallback: generating packages with AEM
    unreachable is a supported workflow. Only the explicit fetch is loud."""
    cache_path = tmp_path / "article.pagetypeconfig.json"
    with httpx.Client(transport=_mock_transport(200, CF_PAYLOAD)) as client:
        fetch_or_cached(client, "http://localhost:4502",
                        "/content/dam/x/article", cache_path)
    with httpx.Client(transport=_mock_transport(401)) as client:
        cfg = fetch_or_cached(client, "http://localhost:4502",
                              "/content/dam/x/article", cache_path)
    assert cfg.type == "article"
