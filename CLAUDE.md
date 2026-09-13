# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AEM Content Automation Framework — Python framework that ingests structured content (Phase 1: Microsoft Word `.docx`) and emits installable AEM Content Packages. No AEM SDK and no custom AEM server code: output is a FileVault `.zip` an operator installs through Package Manager.

Page paths do NOT mirror the source folder tree. The page name comes from the active `pageNameStrategy` (`slug_from_meta_title` | `slug_from_url` | `docx_filename`). Under `slug_from_url` the page lands under `contentRoot` + the section hierarchy stated by the document's own URL; every other strategy lands it flat, directly under `contentRoot`. In batch mode a subfolder groups a `.docx` with its images and never appears in the JCR path.

Example (`slug_from_url`, `contentRoot` `/content/iffcotokio/en` — what the bundled sample config does):
`Articles/Article1/V2-Cashless Treatment vs Reimbursement Claims.docx`
carrying `New Required URL: https://www.iffcotokio.co.in/health-insurance/cashless-treatment-vs-reimbursement-claims`
→ `/content/iffcotokio/en/health-insurance/cashless-treatment-vs-reimbursement-claims`

The same URL hierarchy drives the FAQ Content Fragment folder (`faq.cfRoot` + hierarchy) and the fragments' `faqCategory` tags, so page and fragment placement stay in step. With `siteOrigin` unset or not matching the doc's URL, no hierarchy can be derived — the batch report says so in `pages.csv`'s `warnings` column.

## Commands

```bash
# create/activate venv (Windows)
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip

# install project + dev deps (editable)
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# run all tests
.venv/Scripts/python.exe -m pytest

# run a single test
.venv/Scripts/python.exe -m pytest tests/path/to/test_file.py::test_name

# lint
.venv/Scripts/python.exe -m ruff check .
```

## Architecture

    .docx → format_detect → validators → parser → NCM dict → mapping/mapper → node tree → aem/package_generator → .zip

- `parser/word/` — the engine. `dsl_parser.py` handles the v1/v2 bracket-marker DSL and opens the `.docx` itself via python-docx; `v3_parser.py` handles the markerless article format; `format_detect.py` picks between them. Both emit the NCM dict.
- `validators/` — pre-flight structure checks. `word_structure.py` subclasses the real parser so validation can never drift from what the parser actually accepts.
- `mapping/` — NCM + `PageTypeConfig` → abstract AEM node tree (`mapper.py`), plus the JCR slug helper (`slug.py`) and the Sling-JSON → node-tree converter (`template_convert.py`). This is the seam between source-agnostic NCM and AEM-specific output.
- `aem_client/` — the AEM-side config half. `config.py` holds `SiteConfig` / `PageTypeConfig` and their on-disk cache; `fetcher.py` pulls `PageTypeConfig` from an AEM Content Fragment plus the template's initial content.
- `aem/package_generator/` — Mode 1. Node tree → FileVault `.content.xml` (`filevault.py`) → installable `.zip` (`package.py`), single-page and batch, byte-deterministic.
- `aem/author_api/` — Mode 2, not started. Create/update pages directly via AEM Author APIs.
- `ui/` — FastAPI operator UI. `app.py` is the only file that knows HTTP; `service.py` is the web-framework-free glue; `batch_service.py` runs folder batches; `runs.py` is SQLite run history.
- `config/` — `samples/` (bundled `PageTypeConfig`) and `cache/` (fetched configs). JSON only, no Python.
- `connectors/`, `models/` — scaffold only, still empty. See Known gaps.

Design constraints to preserve:

- Component mapping is config-driven. Rules live in the AEM Content Fragment (`PageTypeConfig.component_mappings`), never hardcoded per content type.
- Publishing is abstracted behind the node tree. Whatever installs pages — FileVault package today, Sling POST later — consumes the same plain dict, so `mapping/mapper.py` never learns the destination mechanism.
- `ui/service.py` keeps zero web-framework imports, so the UI can become a JSON API without touching pipeline logic.
- An NCM block the config has no mapping for must stay visible: the mapper marks it `__mapping_gap` rather than letting the content vanish at install time.

## Known gaps

- `models/` is empty. The NCM is an untyped dict, hand-built independently by both parsers and read by the mapper through `.get()` chains. `pydantic` is a declared dependency and entirely unused. Renaming a key such as `htmltext` fails silently instead of loudly.
- `connectors/` is empty. `dsl_parser.py` opens the `.docx` directly, so the source-agnostic connector boundary described in Phase 1 does not exist yet — adding a second source means extracting it first.
- No logging anywhere in the codebase. Batch failures surface only as `BatchEntry.error` strings in the UI table.
- `ALLOWED_KEYS` (`parser/word/dsl_parser.py`) and `KNOWN_COMPONENTS` (`validators/word_structure.py`) are hardcoded, duplicating `component_mappings` from the CF.
