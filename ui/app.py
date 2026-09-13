"""FastAPI app for the pipeline operator UI -- thin HTTP glue over
ui/service.py and ui/runs.py. No pipeline logic lives here.
"""

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aem.package_generator.package import PackageMeta, build_batch_package, build_package
from aem_client.config import (
    load_page_type_config,
    load_page_type_from_sample,
    load_site_config,
    save_page_type_config,
    save_site_config,
    SiteConfig,
)
from aem_client.fetcher import FetchError, fetch_and_cache
from ui.batch_service import run_batch
from ui.report import build_report_zip
from ui.runs import (
    get_batch_run, get_run, list_runs,
    save_batch_run, save_run,
)
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
        # Surface the cache's age: a fetch that failed leaves an old file in
        # place, and without a date there is nothing on screen to tell the
        # operator the config they are looking at is not what AEM holds.
        fetched = datetime.fromtimestamp(
            cache.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return load_page_type_config(cache), f"cached ({cache.name}, fetched {fetched})"
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
    """Persist operator-facing settings from the home-page form.

    Preserves cf_paths entries for other page types -- form only edits the
    article path; a blog/category config the operator set from an earlier
    save survives the round-trip.
    """
    current = load_site_config(SITE_CONFIG_PATH)
    cf_paths = dict(current.cf_paths)  # keep other page types intact
    if article_cf_path:
        cf_paths["article"] = article_cf_path.strip()
    else:
        cf_paths.pop("article", None)
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
            # fetch_and_cache, NOT fetch_or_cached: the operator asked to talk
            # to AEM, so a rejection has to surface. The cache-fallback variant
            # would swallow a 401 and redirect as though the fetch worked,
            # leaving the pipeline on a stale config with no indication.
            fetch_and_cache(client, site.aem_base_url, cf_path, cache, auth_token)
        except FetchError as exc:
            hint = ""
            if "401" in str(exc) or "403" in str(exc):
                hint = (f" AEM rejected the request -- set ${site.auth_token_env}"
                        " to an authorization header value before starting the"
                        " server. The previously cached config is unchanged and"
                        " still in use.")
            raise HTTPException(502, f"AEM fetch failed: {exc}.{hint}") from exc
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

    site = load_site_config(SITE_CONFIG_PATH)
    page_type_cfg, _ = _current_page_type_config(site.active_page_type)
    result = run_pipeline(
        str(dest),
        force=(force == "true"),
        assets_dir=str(ASSETS_DIR / str(upload_id)),
        format=(format or None),
        page_type_config=page_type_cfg,
    )
    run_id = save_run(result)

    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return templates.TemplateResponse(request, "result.html", {"run": record})


@app.post("/run-batch")
def run_batch_route(all_files: list[UploadFile] = File(...)):
    """Batch mode -- multiple files from one picked folder (docx + assets).

    Client-side JavaScript sets each file's transferred filename to its
    `webkitRelativePath` so we receive `<top-folder>/<subfolder>/<name>.<ext>`.
    `ui.batch_service.run_batch` partitions on the docx extension: docx
    files feed the pipeline, sibling non-docx files ride as per-page
    assets scoped to the same subfolder.
    """
    files: list[tuple[str, bytes]] = []
    for f in all_files:
        name = f.filename or ""
        if not name:
            continue
        blob = f.file.read()
        files.append((name, blob))
    if not any(name.lower().endswith(".docx") for name, _ in files):
        raise HTTPException(400, "No .docx files found in the picked folder.")

    site = load_site_config(SITE_CONFIG_PATH)
    page_type_cfg, _ = _current_page_type_config(site.active_page_type)
    result = run_batch(
        files,
        page_type_config=page_type_cfg,
        aem_base_url=site.aem_base_url,
        auth_token=os.environ.get(site.auth_token_env),
    )
    run_id = save_batch_run(result)
    return RedirectResponse(f"/batch-runs/{run_id}", status_code=303)


@app.get("/batch-runs/{run_id}")
def show_batch_run(request: Request, run_id: str):
    record = get_batch_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    return templates.TemplateResponse(request, "batch_result.html", {"run": record})


@app.get("/batch-runs/{run_id}/package.zip")
def download_batch_package(run_id: str):
    record = get_batch_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    trees: list[dict] = []
    for entry in record.entries:
        if entry.status != "passed" or entry.aem_tree is None:
            continue
        # Re-attach the resolved assets and FAQ CFs to the tree so the
        # packager sees them. Both were popped off at run time -- `_assets`
        # to keep the DB payload JSON-safe (bytes -> base64 in `assets`),
        # `_faq_cfs` just to keep the same pattern -- and now go back onto
        # a shallow copy for this one build. `_faq_cf_root` was left on the
        # tree by batch_service, so it survives untouched on `entry.aem_tree`.
        tree = {**entry.aem_tree}
        if entry.assets:
            tree["_assets"] = entry.assets
        if entry.faq_cfs:
            tree["_faq_cfs"] = entry.faq_cfs
        trees.append(tree)
    if not trees:
        raise HTTPException(status_code=400,
                            detail="No successful pages in this batch to package.")
    meta = PackageMeta(
        name=(record.filename_top or "batch") + "-batch",
        description=f"Batch package: {len(trees)} pages from {record.filename_top or 'folder'}",
    )
    data = build_batch_package(trees, meta)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{meta.name}.zip"'},
    )


@app.get("/batch-runs/{run_id}/report.zip")
def download_batch_report(run_id: str):
    record = get_batch_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    data = build_report_zip(record)
    name = (record.filename_top or "batch") + "-report"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@app.get("/runs/{run_id}/package.zip")
def download_package(run_id: str):
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if record.aem_tree is None:
        raise HTTPException(status_code=400,
                            detail="No AEM node tree on this run; set a page-type config and re-run.")
    meta = PackageMeta(
        name=Path(record.filename).stem.lower().replace(" ", "-") or "content-package",
        description=f"Generated from {record.filename}",
    )
    data = build_package(record.aem_tree, meta)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{meta.name}.zip"'},
    )


@app.get("/history")
def history(request: Request):
    records = list_runs()
    return templates.TemplateResponse(request, "history.html", {"runs": records})
