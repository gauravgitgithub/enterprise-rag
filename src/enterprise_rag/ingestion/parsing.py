import io
import zipfile

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import SectionHeaderItem, TableItem, TextItem, TitleItem

from enterprise_rag.constants.documents import (
    MAX_DOCUMENT_BYTES,
    PARSE_MAX_PAGES,
    PARSE_MAX_UNZIPPED_BYTES,
    PARSE_TIMEOUT_S,
    SECTION_SEPARATOR,
)
from enterprise_rag.ingestion.chunking import Section
from enterprise_rag.ingestion.pipeline import Parse

HEADING_SEPARATOR = " > "


class UnparseableDocument(ValueError):
    """The file cannot be parsed. Retrying will not help."""


def make_docling_parse(
    *, max_pages: int = PARSE_MAX_PAGES, max_unzipped_bytes: int = PARSE_MAX_UNZIPPED_BYTES
) -> Parse:
    """Layout-aware parsing with Docling. Build once per worker: it loads ML models."""
    # shortcut: OCR off, so scanned PDFs yield no text; enable do_ocr when they must be supported.
    pdf = PdfFormatOption(
        pipeline_options=PdfPipelineOptions(do_ocr=False, document_timeout=PARSE_TIMEOUT_S)
    )
    # Only formats we support: every other Docling backend stays unreachable from uploads.
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={InputFormat.PDF: pdf},
    )

    def parse(raw: bytes, filename: str) -> list[Section]:
        # Docling picks the format from the file name.
        try:
            # Sizes come from the ZIP directory, so nothing is decompressed to check them.
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                unzipped = sum(i.file_size for i in archive.infolist())
        except zipfile.BadZipFile:
            unzipped = 0  # not a ZIP (a PDF), or too broken for Docling to open either
        if unzipped > max_unzipped_bytes:
            raise UnparseableDocument(
                f"{filename!r} expands to {unzipped} uncompressed bytes "
                f"(limit {max_unzipped_bytes})"
            )
        try:
            doc = converter.convert(
                DocumentStream(name=filename, stream=io.BytesIO(raw)),
                max_num_pages=max_pages,
                max_file_size=MAX_DOCUMENT_BYTES,
            ).document
        except Exception as exc:  # Docling raises assorted types on malformed input
            raise UnparseableDocument(f"cannot parse {filename!r}: {exc}") from exc
        sections: list[Section] = []
        headings: list[tuple[int, str]] = []  # (level, title) of the open heading chain
        body: list[str] = []
        page: int | None = None

        def close() -> None:
            if body:
                offset = sum(len(s.text) + len(SECTION_SEPARATOR) for s in sections)
                path = HEADING_SEPARATOR.join(title for _, title in headings)
                sections.append(Section(path, "\n".join(body), page, offset))

        for item, _ in doc.iterate_items():
            if isinstance(item, SectionHeaderItem | TitleItem):
                close()
                body, page = [], None
                level = item.level if isinstance(item, SectionHeaderItem) else 0
                headings = [h for h in headings if h[0] < level] + [(level, item.text)]
                continue
            if isinstance(item, TableItem):
                text = item.export_to_markdown(doc=doc)
            elif isinstance(item, TextItem):
                text = item.text
            else:
                continue
            if text.strip():
                body.append(text)
                if page is None and item.prov:
                    page = item.prov[0].page_no
        close()
        return sections

    return parse
