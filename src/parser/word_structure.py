from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from docx.table import Table, _Cell
from lxml import etree


def iter_paragraphs(document):
    """Body and nested table paragraphs in XML order; merged cells visited once."""
    def walk(element, parent):
        for child in element:
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                table = Table(child, parent)
                for row in child.findall(qn("w:tr")):
                    for cell in row.findall(qn("w:tc")):
                        yield from walk(cell, _Cell(cell, table))
    yield from walk(document.element.body, document)


def theme_colors(document):
    for relationship in document.part.rels.values():
        if relationship.reltype.endswith("/theme"):
            root = etree.fromstring(relationship.target_part.blob,
                                    parser=etree.XMLParser(resolve_entities=False, no_network=True))
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            scheme = root.find(".//a:clrScheme", ns)
            if scheme is None: return {}
            colors = {}
            for slot in scheme:
                if len(slot): colors[etree.QName(slot).localname] = slot[0].get("lastClr") or slot[0].get("val")
            colors.update({"dark1": colors.get("dk1"), "light1": colors.get("lt1"),
                           "dark2": colors.get("dk2"), "light2": colors.get("lt2")})
            return colors
    return {}


def iter_runs(paragraph):
    """Include hyperlink display text without following or opening its URL."""
    for child in paragraph._p:
        if child.tag == qn("w:r"):
            yield Run(child, paragraph)
        elif child.tag == qn("w:hyperlink"):
            for run in child.findall(qn("w:r")):
                yield Run(run, paragraph)


def is_heading(paragraph):
    style = paragraph.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        if style.style_id.lower().startswith(("heading", "title", "subtitle")): return True
        style = style.base_style
    return False
