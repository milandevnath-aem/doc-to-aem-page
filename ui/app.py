"""FastAPI app for the pipeline operator UI -- thin HTTP glue over
ui/service.py and ui/runs.py. No pipeline logic lives here.
"""

import json
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ui.runs import get_run, list_runs, save_run
from ui.service import run_pipeline

BASE_DIR = Path(__file__).parent

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


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


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
