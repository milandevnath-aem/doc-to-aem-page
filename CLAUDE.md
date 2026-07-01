# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AEM Content Automation Framework — Python framework that ingests structured content (Phase 1: Microsoft Word `.docx`) and generates AEM pages from it. Folder hierarchy of the source maps to AEM page hierarchy; document filename becomes the page name (slugified).

Example: `Products/Insurance/Health/Family Plan.docx` → `/content/site/products/insurance/health/family-plan`

Status: project skeleton only — package directories and `__init__.py` stubs exist, no implementation yet.

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

The framework follows a pipeline: discover → parse → normalize → validate → map → generate/publish. Each stage is a separate package so parsing logic stays decoupled from AEM-specific output.

- `connectors/` — source-specific readers (`word/`, `google_drive/`, `google_docs/`, `google_sheets/`). Phase 1 implements `word/` only; others are placeholders for future phases. A connector's job ends at producing raw parsed content — it must not know about AEM components.
- `parser/` — turns raw connector output into structured content.
- `models/` — the normalized, source-agnostic intermediate content model (JSON-serializable). This is the contract every connector must produce and every mapper/generator consumes — it is the integration point between source format and AEM output.
- `validators/` — validate parsed content against the content contract before mapping/publishing.
- `mapping/` — configuration-driven content-section → AEM-component mapping (e.g. Heading 1 → Title Component, Paragraph → Text Component). Mapping rules must live in config, not hardcoded in code.
- `transformers/` — apply mapping rules to convert the normalized model into AEM page/component structures.
- `aem/package_generator/` — Mode 1 (local dev): emits AEM Content Packages for a local Author instance.
- `aem/author_api/` — Mode 2 (future): creates/updates pages directly via AEM Author APIs. The publishing layer is abstracted so switching between package generation and direct API requires minimal change — code against this boundary, not against either implementation directly.
- `assets/` — asset handling/upload for images and other binaries referenced by content.
- `config/` — mapping rules, connector config, environment config.
- `utils/` — shared helpers (logging, slug generation, etc).

Design constraints to preserve when implementing:
- Connectors are source-agnostic at the boundary — they all produce the same `models/` content model, regardless of source format.
- Component mapping is config-driven, not hardcoded per content type.
- Publishing (package generation vs. Author API) is abstracted behind a common interface so deployment mode is swappable.
