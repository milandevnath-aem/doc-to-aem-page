"""Config data + on-disk cache for the AEM half of the pipeline.

Two shapes:

- `SiteConfig`: small operator-facing settings that live in the UI form --
  base URL, active page type, name of the env-var holding the AEM auth
  token, and per-page-type CF paths. Cached to a single JSON file.
- `PageTypeConfig`: fat config fetched from AEM (a Content Fragment) --
  template path, content/DAM roots, page-name strategy, and the
  component-name -> sling:resourceType mapping. One JSON file per page
  type on disk so refreshes don't stomp unrelated types.

The pipeline reads from disk (cache/sample) at package-generation time so
it can run offline once configs have been fetched.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Location of ready-to-use sample configs, used when there is no cached
# copy on disk and no live AEM to fetch from. Ships in the repo so a fresh
# checkout can generate packages without waiting for AEM team hand-off.
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "config" / "samples"


@dataclass
class SiteConfig:
    aem_base_url: str = "http://localhost:4502"
    active_page_type: str = "article"
    auth_token_env: str = "AEM_AUTH_TOKEN"
    # per-page-type Content Fragment paths -- where the PageTypeConfig CFs
    # live in AEM. New page type -> new entry.
    cf_paths: dict[str, str] = field(default_factory=lambda: {
        "article": "/content/dam/iffcotokio/site-config/article",
    })


@dataclass
class PageTypeConfig:
    type: str
    template_path: str        # AEM cq:Template JCR path
    content_root: str         # parent under /content where pages land
    dam_root: str             # DAM folder for pages of this type
    page_name_strategy: str   # slug_from_meta_title | slug_from_url | docx_filename
    component_mappings: dict[str, str]  # NCM component name -> sling:resourceType

    # ----- camelCase / JCR-key input from AEM CF -> snake_case fields ----
    _RENAME = {
        "templatePath": "template_path",
        "contentRoot": "content_root",
        "damRoot": "dam_root",
        "pageNameStrategy": "page_name_strategy",
        "componentMappings": "component_mappings",
    }

    @classmethod
    def from_dict(cls, raw: dict) -> "PageTypeConfig":
        """Build from an AEM CF payload OR a cached snake_case dict. Extra
        keys (jcr:primaryType, sling metadata, whatever else the CF carries)
        are ignored so we don't have to keep the parser in lockstep with
        AEM's serialization quirks."""
        # normalize keys: prefer explicit rename, then already-snake-case
        normalized: dict = {}
        for k, v in raw.items():
            snake = cls._RENAME.get(k, k)
            if snake in cls.__dataclass_fields__:
                normalized[snake] = v
        required = {"type", "template_path", "content_root", "dam_root",
                    "page_name_strategy", "component_mappings"}
        missing = required - normalized.keys()
        if missing:
            raise KeyError(f"PageTypeConfig missing required fields: {sorted(missing)}")
        # component_mappings values may be a bare resourceType string OR a
        # dict {resourceType, fieldRenames, itemFieldRenames}. Validate the
        # dict form early so a missing resourceType surfaces at config
        # load, not deep in the mapper.
        for source_name, entry in normalized["component_mappings"].items():
            if isinstance(entry, dict) and "resourceType" not in entry:
                raise KeyError(
                    f"component_mappings['{source_name}'] is a dict but is "
                    "missing 'resourceType'"
                )
        return cls(**normalized)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def load_site_config(path: Path) -> SiteConfig:
    if not path.exists():
        return SiteConfig()
    return SiteConfig(**_read_json(path))


def save_site_config(cfg: SiteConfig, path: Path) -> None:
    _write_json(asdict(cfg), path)


def load_page_type_config(path: Path) -> PageTypeConfig:
    return PageTypeConfig(**_read_json(path))


def save_page_type_config(cfg: PageTypeConfig, path: Path) -> None:
    data = {k: v for k, v in asdict(cfg).items() if not k.startswith("_")}
    _write_json(data, path)


def load_page_type_from_sample(page_type: str, samples_dir: Path | None = None) -> PageTypeConfig:
    samples_dir = samples_dir or SAMPLES_DIR
    path = samples_dir / f"{page_type}.pagetypeconfig.json"
    if not path.exists():
        raise FileNotFoundError(f"No sample PageTypeConfig for '{page_type}' at {path}")
    return PageTypeConfig.from_dict(_read_json(path))
