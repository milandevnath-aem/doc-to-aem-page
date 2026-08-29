"""Business logic for the pipeline operator UI.

Deliberately has zero FastAPI/HTML imports: this is the layer that survives
unchanged if the UI later moves from server-rendered templates to a JSON
API + separate SPA frontend.
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from docx.opc.exceptions import PackageNotFoundError

from aem_client.config import PageTypeConfig
from mapping.mapper import map_ncm_to_node_tree
from parser.word.dsl_parser import parse_docx
from validators.word_structure import Issue, validate_docx


@dataclass
class RunResult:
    status: str  # "passed" or "failed"
    filename: str
    issues: list[dict]
    ncm: dict | None
    aem_tree: dict | None = None


def run_pipeline(
    docx_path: str,
    force: bool = False,
    assets_dir: str | None = None,
    format: str | None = None,
    page_type_config: PageTypeConfig | None = None,
) -> RunResult:
    filename = Path(docx_path).name

    try:
        issues = validate_docx(docx_path)
    except PackageNotFoundError:
        issue = Issue("error", "unreadable-file", f"'{filename}' could not be opened as a .docx file.", -1)
        return RunResult(status="failed", filename=filename, issues=[dataclasses.asdict(issue)], ncm=None)

    has_errors = any(i.severity == "error" for i in issues)
    status = "failed" if has_errors else "passed"

    ncm = None
    aem_tree = None
    if not has_errors or force:
        ncm = parse_docx(docx_path, assets_dir=assets_dir, format=format)
        if page_type_config is not None:
            aem_tree = map_ncm_to_node_tree(ncm, page_type_config)

    return RunResult(
        status=status, filename=filename,
        issues=[dataclasses.asdict(i) for i in issues],
        ncm=ncm, aem_tree=aem_tree,
    )
