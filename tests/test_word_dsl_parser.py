"""Regression checks for parser/word/dsl_parser.py and validators/word_structure.py.

Builds synthetic .docx files on the fly with python-docx (no Node/docx-js
dependency) so these run anywhere `pytest` runs.
"""

import base64
from pathlib import Path

import docx

from parser.word.dsl_parser import parse_docx
from validators.word_structure import validate_docx

# 1x1 transparent PNG -- smallest valid image python-docx can embed
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _build_docx(path: Path, builder) -> str:
    document = docx.Document()
    builder(document)
    document.save(str(path))
    return str(path)


def _add_metadata(doc):
    doc.add_paragraph("[metadata]")
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"


def _add_soft_broken(doc, *lines):
    """One paragraph holding several logical lines separated by soft line
    breaks (Shift+Enter) -- how the v2 client doc packs icon+title+body."""
    p = doc.add_paragraph()
    for i, line in enumerate(lines):
        run = p.add_run(line)
        if i < len(lines) - 1:
            run.add_break()
    return p


def _add_bullet(doc, text):
    p = doc.add_paragraph(text)
    numPr = p._p.get_or_add_pPr().get_or_add_numPr()
    numPr.get_or_add_ilvl().val = 0
    numPr.get_or_add_numId().val = 1
    return p


def test_v1_v2_component_uses_only_htmltext_for_rte(tmp_path):
    """The RTE field on any component is exposed under one canonical name --
    `htmltext` -- and the source-authoring key (`description:` / `body:`)
    does NOT leak through to the output NCM."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Hero Title")
        doc.add_paragraph("description:")
        doc.add_paragraph("Hello world.")

    path = _build_docx(tmp_path / "hero.docx", build)
    hero = parse_docx(path)["blocks"][0]
    assert hero["fields"]["htmltext"] == "<p>Hello world.</p>"
    assert "description" not in hero["fields"]
    assert "body" not in hero["fields"]
    assert "headings" not in hero  # dropped -- was never populated for v2/v3 anyway


def test_v1_cardandrte_body_wins_when_both_body_and_description_authored(tmp_path):
    """A component authored with BOTH `body:` and `description:` keys -- body
    wins for the canonical htmltext (matches how AEM RTE components are
    typically named)."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: cardandRTE]")
        doc.add_paragraph("description:")
        doc.add_paragraph("Description text.")
        doc.add_paragraph("body:")
        doc.add_paragraph("Body text.")

    path = _build_docx(tmp_path / "both.docx", build)
    card = parse_docx(path)["blocks"][0]
    assert card["fields"]["htmltext"] == "<p>Body text.</p>"
    # neither source key survives the rename
    assert "body" not in card["fields"]
    assert "description" not in card["fields"]


def test_v2_lenient_item_uses_only_htmltext_for_rte(tmp_path):
    """v2 lenient items with an implicit description RTE field expose it as
    `htmltext` -- no separate description key on the output."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: featurecards]")
        doc.add_paragraph().add_run("Cards").bold = True
        _add_soft_broken(doc, "icon: location1.png", "Card Title", "Card body text.")

    path = _build_docx(tmp_path / "v2card.docx", build)
    item = parse_docx(path)["blocks"][0]["items"][0]
    assert item["fields"]["htmltext"] == "<p>Card body text.</p>"
    assert "description" not in item["fields"]


def test_component_without_rte_field_gets_no_htmltext(tmp_path):
    """A pure-scalar component (no description/body) must not gain an empty
    htmltext key."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Only a title.")

    path = _build_docx(tmp_path / "scalar.docx", build)
    hero = parse_docx(path)["blocks"][0]
    assert "htmltext" not in hero["fields"]


def test_parse_docx_extracts_rte_html_and_plain_fields(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"

        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Hero Title")
        doc.add_paragraph("description:")
        p = doc.add_paragraph()
        p.add_run("Plain text with ")
        p.add_run("bold text").bold = True
        p.add_run(" after it.")

    path = _build_docx(tmp_path / "sample.docx", build)
    result = parse_docx(path)

    assert result["page"]["properties"]["title"] == "Test Page"
    hero = result["blocks"][0]
    assert hero["name"] == "hero-banner"
    assert hero["fields"]["title"] == "Hero Title"
    assert hero["fields"]["htmltext"] == "<p>Plain text with <strong>bold text</strong> after it.</p>"


def test_parse_docx_table_under_rte_field_becomes_html_table(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        meta = doc.add_table(rows=3, cols=2)
        meta.cell(0, 0).text, meta.cell(0, 1).text = "Property", "Value"
        meta.cell(1, 0).text, meta.cell(1, 1).text = "title", "Test Page"
        meta.cell(2, 0).text, meta.cell(2, 1).text = "description", "A test page"

        doc.add_paragraph("[component: cardandRTE]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Motor Insurance by IFFCO Tokio - A Quick Look")
        doc.add_paragraph("description:")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Key Features", "Benefits"
        table.cell(1, 0).text, table.cell(1, 1).text = "Own Damage Cover", "Available"

    path = _build_docx(tmp_path / "sample.docx", build)
    result = parse_docx(path)

    card = result["blocks"][0]
    assert card["name"] == "cardandRTE"
    assert card["fields"]["htmltext"] == (
        "<table><tr><th>Key Features</th><th>Benefits</th></tr>"
        "<tr><td>Own Damage Cover</td><td>Available</td></tr></table>"
    )
    assert card["table"] is None  # table belonged to the RTE field, not the component


def test_parse_docx_lenient_icon_lines_split_into_items(tmp_path):
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: featurecards]")
        doc.add_paragraph().add_run("Benefits of Buying").bold = True
        _add_soft_broken(doc, "icon: location1.png", "Financial Protection",
                         "Insurance helps manage unexpected expenses.")
        _add_soft_broken(doc, "icon: location2.png", "Peace of Mind",
                         "You can focus on recovery.")

    path = _build_docx(tmp_path / "lenient.docx", build)
    result = parse_docx(path)

    comp = result["blocks"][0]
    assert comp["fields"]["title"] == "Benefits of Buying"
    assert len(comp["items"]) == 2
    first, second = comp["items"]
    assert first["fields"]["image"] == {"source": "reference", "name": "location1.png", "path": None}
    assert first["fields"]["title"] == "Financial Protection"
    assert first["fields"]["htmltext"] == "<p>Insurance helps manage unexpected expenses.</p>"
    assert second["fields"]["image"]["name"] == "location2.png"
    assert second["fields"]["title"] == "Peace of Mind"


def test_parse_docx_lenient_bold_labels_split_numbered_items(tmp_path):
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: cards-with-full-width||numbered]")
        doc.add_paragraph().add_run("Step-by-Step Guide").bold = True
        doc.add_paragraph().add_run("Step 1: Identify Your Insurance Needs").bold = True
        doc.add_paragraph("Determine why you need insurance.")
        _add_bullet(doc, "Health emergencies")
        _add_bullet(doc, "Vehicle accidents")
        doc.add_paragraph().add_run("Step 2: Determine the Coverage Amount").bold = True
        doc.add_paragraph("Choose adequate coverage.")

    path = _build_docx(tmp_path / "numbered.docx", build)
    result = parse_docx(path)

    comp = result["blocks"][0]
    assert comp["fields"]["title"] == "Step-by-Step Guide"
    assert len(comp["items"]) == 2
    step1, step2 = comp["items"]
    assert step1["fields"]["title"] == "Step 1: Identify Your Insurance Needs"
    assert step1["fields"]["htmltext"] == (
        "<p>Determine why you need insurance.</p>"
        "<ul><li>Health emergencies</li><li>Vehicle accidents</li></ul>"
    )
    assert step2["fields"]["title"] == "Step 2: Determine the Coverage Amount"
    assert step2["fields"]["htmltext"] == "<p>Choose adequate coverage.</p>"


def test_parse_docx_lenient_hero_implicit_title_and_inline_keys(tmp_path):
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("Tips for Buying Insurance: A Complete Guide")
        doc.add_paragraph("An intro paragraph under the hero.")
        _add_soft_broken(doc, "Data-of-release: June 23, 2026", "Estimated-read-time: 4 min read")

    path = _build_docx(tmp_path / "hero.docx", build)
    result = parse_docx(path)

    hero = result["blocks"][0]
    assert hero["fields"]["title"] == "Tips for Buying Insurance: A Complete Guide"
    assert hero["fields"]["htmltext"] == "<p>An intro paragraph under the hero.</p>"
    assert hero["fields"]["data-of-release"] == "June 23, 2026"
    assert hero["fields"]["estimated-read-time"] == "4 min read"


def test_parse_docx_strict_keys_still_win_over_lenient_rules(tmp_path):
    # strict-format doc: explicit keys must behave exactly as before, no
    # implicit items or titles
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Explicit Title")
        doc.add_paragraph("description:")
        p = doc.add_paragraph()
        p.add_run("All bold description line.").bold = True

    path = _build_docx(tmp_path / "strict.docx", build)
    result = parse_docx(path)

    hero = result["blocks"][0]
    assert hero["fields"]["title"] == "Explicit Title"
    # bold paragraph under an active RTE key is content, not an item delimiter
    assert hero["fields"]["htmltext"] == "<p><strong>All bold description line.</strong></p>"
    assert hero["items"] == []


def test_parse_docx_extracts_embedded_image_to_assets_dir(tmp_path):
    img_path = tmp_path / "pic.png"
    img_path.write_bytes(PNG_1PX)

    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: featurecards]")
        doc.add_paragraph().add_run("Cards With Pasted Images").bold = True
        doc.add_picture(str(img_path))
        doc.add_paragraph("Pasted Card Title")
        doc.add_paragraph("Pasted card description.")

    path = _build_docx(tmp_path / "embedded.docx", build)
    assets_dir = tmp_path / "assets"
    result = parse_docx(path, assets_dir=str(assets_dir))

    comp = result["blocks"][0]
    assert len(comp["items"]) == 1
    item = comp["items"][0]
    image = item["fields"]["image"]
    assert image["source"] == "embedded"
    assert image["name"] == "image-1.png"
    assert image["path"] == (assets_dir / "image-1.png").as_posix()
    assert (assets_dir / "image-1.png").read_bytes() == PNG_1PX
    assert item["fields"]["title"] == "Pasted Card Title"
    assert item["fields"]["htmltext"] == "<p>Pasted card description.</p>"


def test_parse_docx_embedded_image_without_assets_dir_records_no_path(tmp_path):
    img_path = tmp_path / "pic.png"
    img_path.write_bytes(PNG_1PX)

    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: featurecards]")
        doc.add_paragraph().add_run("Cards").bold = True
        doc.add_picture(str(img_path))
        doc.add_paragraph("Card Title")

    path = _build_docx(tmp_path / "no_assets.docx", build)
    result = parse_docx(path)

    image = result["blocks"][0]["items"][0]["fields"]["image"]
    assert image == {"source": "embedded", "name": "image-1.png", "path": None}


def test_validate_docx_flags_unrecognized_marker_and_missing_metadata(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "Only Title"
        doc.add_paragraph("[component hero-banner]")  # missing colon -> unrecognized marker
        doc.add_paragraph("title:")
        doc.add_paragraph("dropped content")

    path = _build_docx(tmp_path / "broken.docx", build)
    issues = validate_docx(path)
    codes = {i.code for i in issues}

    assert "unrecognized-marker" in codes
    assert "missing-required-metadata" in codes  # description missing
    assert all(i.severity == "error" for i in issues)


def test_validate_docx_flags_card_outside_component(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"
        doc.add_paragraph("[card: 01]")  # no active component

    path = _build_docx(tmp_path / "orphan_card.docx", build)
    issues = validate_docx(path)

    assert any(i.code == "card-outside-component" for i in issues)


def test_validate_docx_accepts_teasercomponent_and_lenient_content(tmp_path):
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component: teasercomponent]")
        doc.add_paragraph("Expert Tips for Buying Insurance")
        _add_soft_broken(doc, "icon: location6.png", "Expert Tips", "Always assess your needs.")

    path = _build_docx(tmp_path / "teaser.docx", build)
    issues = validate_docx(path)

    assert not any(i.severity == "error" for i in issues)
    # lenient content counts as content -- no empty-component warning either
    assert not any(i.code == "empty-component" for i in issues)


def test_parse_real_v2_client_doc_end_to_end():
    """Regression against the actual client-authored v2 sample: lenient
    authoring (component markers, no field keys) must capture all content."""
    fixture = Path(__file__).parent / "fixtures" / "article-v2-sample.docx"

    issues = validate_docx(str(fixture))
    assert issues == []

    result = parse_docx(str(fixture))
    blocks = result["blocks"]
    assert [(b["name"], b["variant"]) for b in blocks] == [
        ("hero-banner", None),
        ("cards-with-full-width", "icon"),
        ("cards-with-full-width", "numbered"),
        ("featurecards", None),
        ("featurecards", None),
        ("featurecards", None),
        ("teasercomponent", None),
    ]

    hero = blocks[0]
    assert hero["fields"]["title"].startswith("Tips for Buying Insurance")
    assert hero["fields"]["data-of-release"] == "June 23, 2026"

    assert len(blocks[1]["items"]) == 3   # icon cards
    steps = blocks[2]["items"]
    assert len(steps) == 7                # Step 1..7
    assert steps[0]["fields"]["title"] == "Step 1: Identify Your Insurance Needs"
    assert [len(b["items"]) for b in blocks[3:6]] == [5, 7, 6]  # feature card groups
    assert blocks[3]["items"][0]["fields"]["image"]["name"] == "location1.png"

    teaser = blocks[6]["items"][0]
    assert teaser["fields"]["title"] == "Expert Tips for Buying Insurance"
    assert teaser["fields"]["htmltext"].count("<li>") == 6

    assert result["page"]["properties"]["title"] == "Family Health Insurance | IFFCO Tokio"


def test_validate_docx_clean_doc_has_no_errors(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"
        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Hero Title")

    path = _build_docx(tmp_path / "clean.docx", build)
    issues = validate_docx(path)

    assert not any(i.severity == "error" for i in issues)


def test_new_hero_banner_extracts_banner_image_filename(tmp_path):
    """The `Banner Image: <file>` line inside a `[component:new-hero-banner]`
    block is a component-specific asset marker: filename lands on the
    component's `imageFile` field, and does NOT leak into `htmltext` or
    the implicit `title` (which the mapper later overrides with meta title
    anyway)."""
    def build(doc):
        _add_metadata(doc)
        _add_soft_broken(
            doc,
            "[component:new-hero-banner]",
            "Banner Image: car-insurance.jpg",
            "",
            "A family health insurance plan helps pay hospital bills.",
        )

    path = _build_docx(tmp_path / "hero_asset.docx", build)
    hero = parse_docx(path)["blocks"][0]

    assert hero["name"] == "new-hero-banner"
    assert hero["fields"]["imageFile"] == "car-insurance.jpg"
    # description survives, without the marker line polluting it
    assert "Banner Image" not in hero["fields"].get("htmltext", "")
    assert "family health insurance" in hero["fields"].get("htmltext", "")
    # implicit title must NOT be the banner-image line
    assert hero["fields"].get("title", "") != "Banner Image: car-insurance.jpg"


def test_new_hero_banner_banner_image_marker_case_insensitive(tmp_path):
    """Filename lookup is case-insensitive; marker keyword tolerates casing
    variance (`banner image`, `BANNER IMAGE`)."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component:new-hero-banner]")
        doc.add_paragraph("BANNER IMAGE: HERO-BANNER.PNG")
        doc.add_paragraph("Body copy.")

    path = _build_docx(tmp_path / "hero_case.docx", build)
    hero = parse_docx(path)["blocks"][0]

    assert hero["fields"]["imageFile"] == "HERO-BANNER.PNG"
    assert "BANNER IMAGE" not in hero["fields"].get("htmltext", "")


def test_banner_image_marker_ignored_outside_new_hero_banner(tmp_path):
    """The `Banner Image:` shortcut only applies inside a
    `[component:new-hero-banner]` block. Any other component treats the
    same line as ordinary prose so we don't hijack unrelated authoring."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component:page-description]")
        doc.add_paragraph("Banner Image: not-a-banner.jpg")
        doc.add_paragraph("Content.")

    path = _build_docx(tmp_path / "other.docx", build)
    block = parse_docx(path)["blocks"][0]

    assert "imageFile" not in block["fields"]


def test_new_hero_banner_closed_on_first_heading_after_its_prose(tmp_path):
    """Real client docs use a SINGLE `[component:new-hero-banner]` marker
    then leave the article body unmarked (no `[component:page-description]`
    to switch to). A Word Heading paragraph is the natural end of the
    banner section -- close the hero and start an implicit
    page-description so the rest of the doc lands in a separate
    component instead of being folded into bannerDescription."""
    def build(doc):
        _add_metadata(doc)
        doc.add_paragraph("[component:new-hero-banner]")
        doc.add_paragraph("Banner Image: car.jpg")
        doc.add_paragraph("Banner intro prose one.")
        doc.add_paragraph("Banner intro prose two.")
        doc.add_paragraph()  # empty separator
        doc.add_paragraph("What is Cashless Treatment?", style="Heading 2")
        doc.add_paragraph("Body copy under the H2.")
        doc.add_paragraph("Another body paragraph.")

    path = _build_docx(tmp_path / "auto-close.docx", build)
    blocks = parse_docx(path)["blocks"]

    # Two blocks: hero-banner + implicit page-description
    assert [b["name"] for b in blocks] == ["new-hero-banner", "page-description"]

    hero = blocks[0]
    assert hero["fields"]["imageFile"] == "car.jpg"
    hero_html = hero["fields"].get("htmltext", "")
    # banner description holds only the intro paragraphs -- headings
    # and body copy that follow are NOT in it
    assert "Banner intro prose one" in hero_html
    assert "Banner intro prose two" in hero_html
    assert "Cashless Treatment" not in hero_html
    assert "Body copy" not in hero_html

    body = blocks[1]
    body_html = body["fields"].get("htmltext", "")
    # implicit page-description carries the H2 + body copy
    assert "Cashless Treatment" in body_html
    assert "Body copy under the H2." in body_html
    assert "Another body paragraph" in body_html
