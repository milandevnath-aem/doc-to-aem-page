"""FastAPI app for the pipeline operator UI -- thin HTTP glue over
ui/service.py and ui/runs.py. No pipeline logic lives here.
"""

import json
import os
import shutil
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aem_client.config import (
    load_page_type_config,
    load_page_type_from_sample,
    load_site_config,
    save_page_type_config,
    save_site_config,
    SiteConfig,
)
from aem_client.fetcher import FetchError, fetch_or_cached
from ui.runs import get_run, list_runs, save_run
from ui.service import run_pipeline

BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent

# On-disk cache for AEM configs. All under a single dir so it's easy to
# delete/inspect during development.
AEM_CONFIG_DIR = REPO_ROOT / "config" / "cache"
SITE_CONFIG_PATH = AEM_CONFIG_DIR / "site-config.json"


def _page_type_cache_path(page_type: str) -> Path:
    return AEM_CONFIG_DIR / f"{page_type}.pagetypeconfig.json"

app = FastAPI(title="Pipeline Operator UI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _html_json(value, indent: int | None = None) -> str:
    """Serialize `value` to JSON keeping `<` and `>` as real characters --
    unlike Jinja's built-in `tojson`, which escapes them to \\u003c/\\u003e
    for safe embedding inside <script> blocks. HTML autoescape in the
    template already covers the same safety inside <pre> and attributes,
    so this produces a JSON dump whose displayed and clipboard-copied
    form contains readable `<p>`, `<h2>`, `<table>`, etc.
    """
    return json.dumps(value, indent=indent, ensure_ascii=False)


templates.env.filters["html_json"] = _html_json

UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# extracted (pasted) images land here, per run, so the result page can serve them
ASSETS_DIR = BASE_DIR / "static" / "assets"


def _current_page_type_config(page_type: str):
    """Return (config-or-None, source-label). Prefers the operator's cached
    fetch; falls back to a bundled sample; None only when neither exists."""
    cache = _page_type_cache_path(page_type)
    if cache.exists():
        return load_page_type_config(cache), f"cached ({cache.name})"
    try:
        return load_page_type_from_sample(page_type), "sample (config/samples)"
    except FileNotFoundError:
        return None, "none"


@app.get("/")
def home(request: Request):
    site = load_site_config(SITE_CONFIG_PATH)
    active_cfg, source = _current_page_type_config(site.active_page_type)
    ctx = {"site": site, "active_config": active_cfg, "active_source": source}
    return templates.TemplateResponse(request, "home.html", ctx)


@app.post("/config/site")
def save_site(
    aem_base_url: str = Form(...),
    active_page_type: str = Form(...),
    auth_token_env: str = Form("AEM_AUTH_TOKEN"),
    article_cf_path: str = Form(""),
):
    """Persist operator-facing settings from the home-page form."""
    cf_paths = {"article": article_cf_path} if article_cf_path else {}
    cfg = SiteConfig(
        aem_base_url=aem_base_url.strip(),
        active_page_type=active_page_type.strip(),
        auth_token_env=auth_token_env.strip() or "AEM_AUTH_TOKEN",
        cf_paths=cf_paths,
    )
    save_site_config(cfg, SITE_CONFIG_PATH)
    return RedirectResponse("/", status_code=303)


@app.post("/config/fetch")
def fetch_config():
    """Hit the configured AEM CF endpoint, cache the result."""
    site = load_site_config(SITE_CONFIG_PATH)
    cf_path = site.cf_paths.get(site.active_page_type)
    if not cf_path:
        raise HTTPException(400, f"No CF path configured for '{site.active_page_type}'.")
    auth_token = os.environ.get(site.auth_token_env)
    cache = _page_type_cache_path(site.active_page_type)
    with httpx.Client() as client:
        try:
            fetch_or_cached(client, site.aem_base_url, cf_path, cache, auth_token)
        except FetchError as exc:
            raise HTTPException(502, f"AEM fetch failed: {exc}") from exc
    return RedirectResponse("/", status_code=303)


@app.post("/config/load-sample")
def load_sample_config():
    """Copy the bundled sample for the active page type into the cache
    dir -- makes it the effective config until the next fetch."""
    site = load_site_config(SITE_CONFIG_PATH)
    try:
        sample = load_page_type_from_sample(site.active_page_type)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    save_page_type_config(sample, _page_type_cache_path(site.active_page_type))
    return RedirectResponse("/", status_code=303)


@app.post("/run")
def run(
    docx_file: UploadFile = File(...),
    force: str = Form(default=""),
    format: str = Form(default=""),
):
    if not (docx_file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are accepted.")

    safe_filename = Path(docx_file.filename).name
    upload_id = uuid.uuid4()
    dest = UPLOADS_DIR / f"{upload_id}-{safe_filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(docx_file.file, f)

    result = run_pipeline(
        str(dest),
        force=(force == "true"),
        assets_dir=str(ASSETS_DIR / str(upload_id)),
        format=(format or None),
    )
    run_id = save_run(result)

    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return templates.TemplateResponse(request, "result.html", {"run": record})


@app.get("/history")
def history(request: Request):
    records = list_runs()
    return templates.TemplateResponse(request, "history.html", {"runs": records})
