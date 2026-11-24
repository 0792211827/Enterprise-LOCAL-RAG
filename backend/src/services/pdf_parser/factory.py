from functools import lru_cache

from src.config import get_settings

from .images import ImageRules
from .parser import PDFParserService


@lru_cache(maxsize=1)
def make_pdf_parser_service() -> PDFParserService:
    """Create cached PDF parser service using Docling."""
    settings = get_settings()
    return PDFParserService(
        max_pages=settings.pdf_parser.max_pages,
        max_file_size_mb=settings.pdf_parser.max_file_size_mb,
        do_ocr=settings.pdf_parser.do_ocr,
        do_table_structure=settings.pdf_parser.do_table_structure,
        image_rules=ImageRules(min_px=settings.ingestion.min_image_px, max_images=settings.ingestion.max_images_per_document),
    )
