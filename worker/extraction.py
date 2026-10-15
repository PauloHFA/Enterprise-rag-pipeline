"""Structured extraction; positions refer to normalized page/section text."""

import io
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import docx
import fitz
from bs4 import BeautifulSoup
from docx.table import Table
from docx.text.paragraph import Paragraph


@dataclass
class TextSection:
    text: str
    page: int | None = None
    section: str | None = None


@dataclass
class ExtractedDocument:
    title: str
    sections: list[TextSection]
    metadata: dict = field(default_factory=dict)


def normalize(text):
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _group_blocks(blocks):
    """Keep headings, tables and paragraphs in source order without duplicating text."""
    sections: list[TextSection] = []
    parts: list[str] = []
    headings: list[tuple[int, str]] = []

    def flush():
        if parts:
            value = normalize("\n\n".join(parts))
            if value:
                sections.append(
                    TextSection(value, section=" > ".join(label for _, label in headings) or None)
                )
            parts.clear()

    for level, value in blocks:
        if level:
            flush()
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, value))
        parts.append(value)
    flush()
    return sections


def extract_document(
    content,
    mime_type,
    filename,
    *,
    title_override=None,
    ocr_enabled=False,
    ocr_languages="por+eng",
    ocr_tessdata=None,
):
    mime_type = (mime_type or "").split(";")[0].lower().strip()
    fallback = Path(filename).stem
    metadata, title = {}, fallback
    if mime_type == "application/pdf":
        sections, ocr_pages = [], []
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if len(pdf) > int(os.getenv("MAX_PDF_PAGES", "1000")) or pdf.needs_pass:
                raise ValueError("PDF page limit exceeded or document encrypted")
            title = (pdf.metadata or {}).get("title") or fallback
            metadata = {
                "format": "pdf",
                "author": (pdf.metadata or {}).get("author"),
                "pages": len(pdf),
            }
            for number, page in enumerate(pdf, 1):
                value = page.get_text("text", sort=True)
                if ocr_enabled and (
                    not value.strip() or (len(value.strip()) < 32 and page.get_images())
                ):
                    textpage = page.get_textpage_ocr(
                        language=ocr_languages, dpi=300, full=True, tessdata=ocr_tessdata
                    )
                    value = page.get_text("text", textpage=textpage, sort=True)
                    ocr_pages.append(number)
                value = normalize(value)
                if value:
                    sections.append(TextSection(value, page=number))
        metadata["ocr_pages"] = ocr_pages
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        document = docx.Document(io.BytesIO(content))
        title = document.core_properties.title or fallback
        blocks = []
        for node in document.element.body.iterchildren():
            if node.tag.endswith("}p"):
                paragraph = Paragraph(node, document)
                value = paragraph.text.strip()
                if not value:
                    continue
                style = paragraph.style.style_id if paragraph.style else ""
                match = re.match(r"Heading(\d+)", style)
                level = int(match.group(1)) if match else 0
                if style == "Title" and title == fallback:
                    title = value
                blocks.append((level, value))
            elif node.tag.endswith("}tbl"):
                table = Table(node, document)
                blocks.append(
                    (0, "\n".join("\t".join(cell.text for cell in row.cells) for row in table.rows))
                )
        sections = _group_blocks(blocks)
        metadata = {"format": "docx", "author": document.core_properties.author}
    elif mime_type == "text/html":
        soup = BeautifulSoup(content, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else fallback
        for tag in soup(["script", "style", "nav"]):
            tag.decompose()
        body = soup.body or soup
        blocks = []
        tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre"]
        for tag in body.find_all(tags):
            if any(parent.name in tags for parent in tag.parents if parent is not body):
                continue
            value = tag.get_text(" ", strip=True)
            if tag.name == "table":
                value = "\n".join(
                    "\t".join(cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"]))
                    for row in tag.find_all("tr")
                )
            if value:
                blocks.append((int(tag.name[1]) if tag.name.startswith("h") else 0, value))
        sections = (
            _group_blocks(blocks) if blocks else [TextSection(normalize(body.get_text("\n")))]
        )
        metadata = {"format": "html"}
    elif mime_type in ("text/markdown", "text/x-markdown") or Path(filename).suffix.lower() in (
        ".md",
        ".markdown",
    ):
        blocks = []
        parts: list[str] = []
        fenced = False
        for line in content.decode("utf-8-sig").splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*$", line) if not fenced else None
            if match:
                if parts:
                    blocks.append((0, "\n".join(parts)))
                    parts = []
                heading = match.group(2)
                if len(match.group(1)) == 1 and title == fallback:
                    title = heading
                blocks.append((len(match.group(1)), heading))
            else:
                parts.append(line)
        if parts:
            blocks.append((0, "\n".join(parts)))
        sections = _group_blocks(blocks)
        metadata = {"format": "markdown"}
    elif mime_type.startswith("text/"):
        sections = [TextSection(normalize(content.decode("utf-8-sig")))]
        metadata = {"format": "text"}
    else:
        raise ValueError("Unsupported document format")
    sections = [section for section in sections if section.text.strip()]
    if not sections:
        raise ValueError("No text extracted; scanned PDFs may require OCR")
    if isinstance(title_override, str) and title_override.strip():
        title = title_override.strip()
    return ExtractedDocument(normalize(title), sections, metadata)
