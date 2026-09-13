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

import httpx

from aem_client.config import PageTypeConfig
from aem_client.fetcher import cf_exists
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
    # Task 7: FAQ Content Fragment node trees (Task 6). Already JSON-safe
    # (no raw bytes), so unlike `assets` it round-trips with no base64
    # step. Package build re-attaches it to aem_tree under `_faq_cfs`.
    faq_cfs: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class BatchResult:
    filename_top: str    # user-friendly label for the whole batch
    entries: list[BatchEntry]


def _annotate_collisions(entries: list[BatchEntry], aem_base_url: str | None,
                         auth_token: str | None) -> None:
    """Stamp every fragment with whether its path is already occupied in AEM.

    The chosen policy is overwrite, so this changes nothing about what the
    package does -- it only makes the report able to say what was overwritten.
    With no AEM configured, every fragment reports "unknown" rather than the
    guess that its path is free.
    """
    pending = [cf for entry in entries for cf in entry.faq_cfs]
    if not pending:
        return
    if not aem_base_url:
        for cf in pending:
            cf["collision"] = "unknown"
        return
    with httpx.Client() as client:
        for cf in pending:
            cf["collision"] = cf_exists(client, aem_base_url, cf["path"], auth_token)


def _dedupe_cf_paths(entries: list[BatchEntry]) -> None:
    """Make Content Fragment paths unique across the whole batch.

    `build_faq_cf_trees` only dedupes within one document, so two docs in
    the same URL section asking the same question derive the SAME fragment
    path. The packager keys entries by path, so the second fragment would
    overwrite the first and one answer would ship missing -- no error, no
    row, nothing. Suffix the later one exactly like the in-document rule
    does, and record what it clashed with so the report can show it:
    silently renaming would trade lost content for an invisible rename.
    """
    taken: set[str] = set()
    for entry in entries:
        original = [cf["path"] for cf in entry.faq_cfs]
        clashes = 0
        for cf in entry.faq_cfs:
            path = cf["path"]
            if path not in taken:
                taken.add(path)
                continue
            n = 2
            while f"{path}-{n}" in taken:
                n += 1
            new_path = f"{path}-{n}"
            taken.add(new_path)
            cf["batch_clash"] = path
            cf["path"] = new_path
            cf["name"] = new_path.rsplit("/", 1)[-1]
            clashes += 1
        if clashes:
            # The page's faq reference node (when the AEM component is
            # configured) holds the OLD paths -- re-stamp it or the page
            # would point at fragments this batch never writes.
            _rewrite_cf_paths(entry.aem_tree, original,
                              [cf["path"] for cf in entry.faq_cfs])
        if clashes and entry.aem_tree is not None:
            entry.aem_tree.setdefault("_warnings", []).append(
                f"{clashes} FAQ fragment path(s) clashed with another document "
                "in this batch and were suffixed so no answer is lost -- see "
                "batch_clash in faqs.csv.")


def _rewrite_cf_paths(node: dict | None, old_paths: list[str],
                      new_paths: list[str]) -> None:
    """Re-stamp the page's faq reference list from the final paths, in one go.

    Matched by the whole list the mapper built it from, then replaced
    wholesale. Substituting one old->new value at a time instead is wrong:
    a later rename re-matches the value an earlier rename just wrote (doc B
    asking "Q" and "Q 2" ends up referencing one fragment twice and
    orphaning the other), and the package still looks fine because only the
    page's property is wrong. `faq_cfs` is in the same order the property
    list was built in, so a positional re-stamp is exact.
    """
    if not node:
        return
    props = node.get("properties") or {}
    for key, value in props.items():
        if value == old_paths:
            props[key] = list(new_paths)
    for child in node.get("children") or []:
        _rewrite_cf_paths(child, old_paths, new_paths)


def run_batch(
    files: list[tuple[str, bytes]],
    page_type_config: PageTypeConfig | None = None,
    aem_base_url: str | None = None,
    auth_token: str | None = None,
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
                    error=_unresolved_reason(result, page_type_config),
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
            # Pop `_faq_cfs` the same way -- `_faq_cf_root` and
            # `_source_url` stay on the tree: plain strings, they
            # survive the JSON round-trip untouched.
            page_cfs_out = (
                result.aem_tree.pop("_faq_cfs", [])
                if result.aem_tree else []
            )
            entries.append(BatchEntry(
                folder=folder, filename=filename,
                page_path=page_path, status=result.status,
                issues=result.issues, aem_tree=result.aem_tree,
                assets=page_assets_out,
                faq_cfs=page_cfs_out,
            ))

    _dedupe_cf_paths(entries)
    _annotate_collisions(entries, aem_base_url, auth_token)
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


def _unresolved_reason(result, page_type_config: PageTypeConfig | None) -> str:
    """Explain why no page slug could be derived.

    Under `slug_from_url`, `split_site_url` returns no slug both when the
    doc has no URL at all AND when the URL just doesn't sit under the
    configured `site_origin` -- and `site_origin` defaults to "", so an
    operator who hasn't set it yet would otherwise see every page blamed
    on "doc metadata" when the doc is fine and the config isn't set.
    Distinguish the two so the operator looks in the right place.
    """
    strategy = page_type_config.page_name_strategy if page_type_config else None
    if strategy == "slug_from_url":
        url = ((result.ncm or {}).get("page", {}).get("properties", {})
               .get("url") or "").strip()
        if not url:
            return ("Could not derive a page name: the doc has no page URL "
                    "(active pageNameStrategy is slug_from_url). Add a URL "
                    "to the doc metadata and re-upload.")
        return (f"Could not derive a page name: the doc's URL ('{url}') "
                f"does not match the configured siteOrigin "
                f"('{page_type_config.site_origin}'). Check siteOrigin in "
                "the AEM config -- the doc metadata may be fine.")
    return ("Could not derive a page name from the doc "
            "(no URL, no Meta Title, no other seed for the active "
            "pageNameStrategy). Fix the doc metadata and re-upload.")


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
