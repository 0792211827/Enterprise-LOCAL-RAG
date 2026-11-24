import logging
from pathlib import Path
from typing import Optional

from src.exceptions import PDFParsingException, PDFValidationError
from src.schemas.indexing.elements import ImageExtraction
from src.schemas.pdf_parser.models import PdfContent

from .docling import DoclingParser
from .images import ImageRules

logger = logging.getLogger(__name__)


class PDFParserService:
    """Main PDF parsing service using Docling only."""

    def __init__(
        self,
        max_pages: int,
        max_file_size_mb: int,
        do_ocr: bool = False,
        do_table_structure: bool = True,
        image_rules: Optional[ImageRules] = None,
    ):
        """Initialize PDF parser service with configurable limits."""
        self.docling_parser = DoclingParser(
            max_pages=max_pages,
            max_file_size_mb=max_file_size_mb,
            do_ocr=do_ocr,
            do_table_structure=do_table_structure,
            image_rules=image_rules,
        )

    async def parse_pdf(self, pdf_path: Path) -> Optional[PdfContent]:
        """Parse PDF using Docling parser only.

        :param pdf_path: Path to PDF file
        :returns: PdfContent object or None if parsing failed
        """
        if not pdf_path.exists():
            logger.error(f"PDF file not found: {pdf_path}")
            raise PDFValidationError(f"PDF file not found: {pdf_path}")

        try:
            result = await self.docling_parser.parse_pdf(pdf_path)
            if result:
                logger.info(f"Parsed {pdf_path.name}")
                return result
            else:
                logger.error(f"Docling parsing returned no result for {pdf_path.name}")
                raise PDFParsingException(f"Docling parsing returned no result for {pdf_path.name}")

        except (PDFValidationError, PDFParsingException):
            raise
        except Exception as e:
            logger.error(f"Docling parsing error for {pdf_path.name}: {e}")
            raise PDFParsingException(f"Docling parsing error for {pdf_path.name}: {e}")

    async def parse_document(self, path: Path) -> Optional[PdfContent]:
        """Parse a PDF or an Office document (DOCX, PPTX, XLSX) with Docling.

        :returns: the content, or ``None`` if the file is over the size or page limit.
        :raises PDFParsingException: the file could not be read.
        """
        if not path.exists():
            raise PDFValidationError(f"File not found: {path}")
        try:
            return await self.docling_parser.parse_document(path)
        except (PDFValidationError, PDFParsingException):
            raise
        except Exception as e:
            raise PDFParsingException(f"Docling parsing error for {path.name}: {e}")

    async def extract_images(self, path: Path, load: bool = True) -> ImageExtraction:
        """The pictures of a PDF, PPTX or DOCX worth describing; none for other types. Never raises: pictures do not fail a Document."""
        try:
            return await self.docling_parser.extract_images(path, load)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not extract images from {path.name}: {e}")
            return ImageExtraction()
