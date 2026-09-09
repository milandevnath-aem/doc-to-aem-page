"""Batch mode -- multi-doc pipeline runs from a picked folder.

Each subfolder = one page. The subfolder NAME is only for grouping the
docx with its assets (Phase 2B); it does NOT leak into the JCR path.
Page path is derived from the doc's own content via pageNameStrategy,
same as a single-doc upload.
"""

import base64
import dataclasses
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from aem_client.config import PageTypeConfig
from mapping.slug import UNRESOLVED_SLUG
from ui.service import run_pipeline


@dataclass
class BatchEntry:
    folder: str          # first path segment of the relative path
    filename: str        # docx basename
    page_path: str       # computed JCR path, or "" on failure
    status: str          # passed | failed | collision | unresolved
    issues: list[dict] = field(default_factory=list)
    aem_tree: dict | None = None
    # Phase 2B: per-page assets resolved from the folder's non-docx
    # siblings. Bytes stay in memory during a run; entries_to_json_list
    # base64-encodes them for DB persistence and entries_from_json_list
    # decodes them back. Package build re-attaches them to aem_tree
    # under `_assets` for the packager to bundle.
    assets: list[dict] = field(default_factory=list)
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

    Non-docx files in each subfolder ride the batch as page assets: they
    are grouped by their parent folder and passed to the pipeline for
    every docx in that same folder. The mapper resolves per-block
    `Banner Image:` markers against this dict; the packager bundles the
    ones that resolved.
    """
    top_folder = _pick_top_folder(files)
    docx_files, assets_by_folder = _partition_files_by_folder(files)
    entries: list[BatchEntry] = []
    seen_slugs: set[str] = set()

    with tempfile.TemporaryDirectory() as tmp:
        for rel_path, blob in docx_files:
            parts = rel_path.replace("\\", "/").split("/")
            folder = parts[-2] if len(parts) >= 2 else ""
            filename = parts[-1]

            dest = Path(tmp) / f"{len(entries)}-{filename}"
            dest.write_bytes(blob)
            try:
                result = run_pipeline(
                    str(dest),
                    page_type_config=page_type_config,
                    page_assets=assets_by_folder.get(folder, {}),
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

            # Unresolved slug -- the doc has no URL / meta title / other
            # value the pageNameStrategy can seed a slug from. Drop the
            # aem_tree so this page never lands in the package, but keep
            # the row in the log so the operator sees which doc to fix.
            if slug == UNRESOLVED_SLUG:
                entries.append(BatchEntry(
                    folder=folder, filename=filename, page_path="",
                    status="unresolved",
                    issues=result.issues,
                    error=("Could not derive a page name from the doc "
                           "(no URL, no Meta Title, no other seed for the "
                           "active pageNameStrategy). Fix the doc metadata "
                           "and re-upload."),
                ))
                continue

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
            # Pull assets off the tree BEFORE storing -- bytes are not
            # JSON-serializable, and the packager reads them from the
            # entry (not the tree) so keeping them here is cleaner
            # anyway. Package build re-attaches them to a tree copy.
            page_assets_out = (
                result.aem_tree.pop("_assets", [])
                if result.aem_tree else []
            )
            entries.append(BatchEntry(
                folder=folder, filename=filename,
                page_path=page_path, status=result.status,
                issues=result.issues, aem_tree=result.aem_tree,
                assets=page_assets_out,
            ))

    return BatchResult(filename_top=top_folder, entries=entries)


def _partition_files_by_folder(
    files: list[tuple[str, bytes]],
) -> tuple[list[tuple[str, bytes]], dict[str, dict[str, bytes]]]:
    """Split the flat file list into (docx_files, assets_by_folder).

    docx_files preserves original order so pipeline runs match the
    user's picked order in the UI. assets_by_folder is keyed by the
    docx's OWN parent folder (last path segment before the filename)
    -- exactly the segment `run_batch` uses when it labels the entry --
    so per-page asset lookup stays scoped to a page's own subfolder.
    A single Banner Image marker cannot silently reach an image sitting
    in a sibling folder even when filenames match.
    """
    docxs: list[tuple[str, bytes]] = []
    assets: dict[str, dict[str, bytes]] = {}
    for rel_path, blob in files:
        parts = rel_path.replace("\\", "/").split("/")
        filename = parts[-1]
        folder = parts[-2] if len(parts) >= 2 else ""
        if filename.lower().endswith(".docx"):
            docxs.append((rel_path, blob))
        else:
            assets.setdefault(folder, {})[filename] = blob
    return docxs, assets


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
    """Round-trip an entries list through JSON safely. `assets[*].bytes`
    are base64-encoded here so the DB layer can `json.dumps` the value;
    `entries_from_json_list` decodes them back to raw bytes."""
    out: list[dict] = []
    for entry in entries:
        row = dataclasses.asdict(entry)
        row["assets"] = [_asset_to_json(a) for a in row.get("assets", [])]
        out.append(row)
    return out


def entries_from_json_list(data: list[dict]) -> list[BatchEntry]:
    result: list[BatchEntry] = []
    for row in data:
        row = dict(row)
        row["assets"] = [_asset_from_json(a) for a in row.get("assets", [])]
        result.append(BatchEntry(**row))
    return result


def _asset_to_json(asset: dict) -> dict:
    return {
        "dam_path": asset["dam_path"],
        "filename": asset["filename"],
        "bytes_b64": base64.b64encode(asset["bytes"]).decode("ascii"),
    }


def _asset_from_json(row: dict) -> dict:
    return {
        "dam_path": row["dam_path"],
        "filename": row["filename"],
        "bytes": base64.b64decode(row["bytes_b64"]),
    }
