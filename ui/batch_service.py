"""Batch mode -- multi-doc pipeline runs from a picked folder.

Each subfolder = one page. The subfolder NAME is only for grouping the
docx with its assets (Phase 2B); it does NOT leak into the JCR path.
Page path is derived from the doc's own content via pageNameStrategy,
same as a single-doc upload.
"""

import dataclasses
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from aem_client.config import PageTypeConfig
from ui.service import run_pipeline


@dataclass
class BatchEntry:
    folder: str          # first path segment of the relative path
    filename: str        # docx basename
    page_path: str       # computed JCR path, or "" on failure
    status: str          # passed | failed | collision
    issues: list[dict] = field(default_factory=list)
    aem_tree: dict | None = None
    error: str | None = None


@dataclass
class BatchResult:
    filename_top: str    # user-friendly label for the whole batch
    entries: list[BatchEntry]


def run_batch(
    files: list[tuple[str, bytes]],
    page_type_config: PageTypeConfig | None = None,
) -> BatchResult:
    """Run the pipeline once per (relative_path, docx_bytes) pair.

    Files are expected as tuples so the route handler can hand them off
    without the fastapi UploadFile abstraction leaking in here -- keeps
    this layer usable from tests + a future CLI.
    """
    top_folder = _pick_top_folder(files)
    entries: list[BatchEntry] = []
    seen_slugs: set[str] = set()

    with tempfile.TemporaryDirectory() as tmp:
        for rel_path, blob in files:
            parts = rel_path.replace("\\", "/").split("/")
            folder = parts[-2] if len(parts) >= 2 else ""
            filename = parts[-1]

            dest = Path(tmp) / f"{len(entries)}-{filename}"
            dest.write_bytes(blob)
            try:
                result = run_pipeline(
                    str(dest),
                    page_type_config=page_type_config,
                )
            except Exception as exc:
                entries.append(BatchEntry(
                    folder=folder, filename=filename, page_path="",
                    status="failed", error=str(exc),
                ))
                continue

            failed = result.status == "failed" and not result.aem_tree
            if failed:
                entries.append(BatchEntry(
                    folder=folder, filename=filename, page_path="",
                    status="failed",
                    issues=result.issues,
                    error=_first_error(result.issues),
                ))
                continue

            page_path = (result.aem_tree or {}).get("path", "")
            slug = page_path.rsplit("/", 1)[-1] if page_path else ""

            if slug and slug in seen_slugs:
                entries.append(BatchEntry(
                    folder=folder, filename=filename, page_path=page_path,
                    status="collision",
                    issues=result.issues,
                    error=(f"Duplicate page slug '{slug}' -- first docx wins; "
                           "this one skipped so no silent overwrite."),
                ))
                continue

            if slug:
                seen_slugs.add(slug)
            entries.append(BatchEntry(
                folder=folder, filename=filename,
                page_path=page_path, status=result.status,
                issues=result.issues, aem_tree=result.aem_tree,
            ))

    return BatchResult(filename_top=top_folder, entries=entries)


def _pick_top_folder(files: list[tuple[str, bytes]]) -> str:
    """Common first path segment across all files, or empty string."""
    if not files:
        return ""
    first = files[0][0].replace("\\", "/").split("/")[0]
    for path, _ in files[1:]:
        if path.replace("\\", "/").split("/")[0] != first:
            return ""
    return first


def _first_error(issues: list[dict]) -> str | None:
    for issue in issues:
        if issue.get("severity") == "error":
            return issue.get("message")
    return None


def entries_to_json_list(entries: list[BatchEntry]) -> list[dict]:
    return [dataclasses.asdict(e) for e in entries]


def entries_from_json_list(data: list[dict]) -> list[BatchEntry]:
    return [BatchEntry(**e) for e in data]
