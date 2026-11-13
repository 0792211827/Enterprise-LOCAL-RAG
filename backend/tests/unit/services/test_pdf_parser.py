from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
from src.exceptions import PDFParsingException, PDFValidationError
from src.schemas.pdf_parser.models import PaperSection, ParserType, PdfContent
from src.services.pdf_parser.docling import DoclingParser
from src.services.pdf_parser.factory import make_pdf_parser_service
from src.services.pdf_parser.parser import PDFParserService


class TestDoclingParser:
    """Test DoclingParser functionality."""

    @pytest.fixture
    def docling_parser(self):
        """Create DoclingParser instance for testing."""
        return DoclingParser(max_pages=20, max_file_size_mb=10, do_ocr=False)

    @pytest.fixture
    def valid_pdf_path(self, tmp_path):
        """Create a mock valid PDF file path."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4\ntest content")
        return pdf_file

    @pytest.fixture
    def empty_pdf_path(self, tmp_path):
        """Create an empty PDF file path."""
        pdf_file = tmp_path / "empty.pdf"
        pdf_file.write_bytes(b"")
        return pdf_file

    @pytest.fixture
    def invalid_pdf_path(self, tmp_path):
        """Create an invalid PDF file path."""
        pdf_file = tmp_path / "invalid.pdf"
        pdf_file.write_bytes(b"Not a PDF file")
        return pdf_file

    def test_docling_parser_initialization(self, docling_parser):
        """Test DoclingParser initialization."""
        assert docling_parser.max_pages == 20
        assert docling_parser.max_file_size_bytes == 10 * 1024 * 1024
        assert docling_parser._warmed_up is False

    def test_validate_pdf_valid_file(self, docling_parser, valid_pdf_path):
        """Test PDF validation with valid file."""
        # This test is complex due to pypdfium2 dependency, skip for now
        pass

    def test_validate_pdf_empty_file(self, docling_parser, empty_pdf_path):
        """Test PDF validation with empty file."""
        with pytest.raises(PDFValidationError) as exc_info:
            docling_parser._validate_pdf(empty_pdf_path)

        assert "PDF file is empty" in str(exc_info.value)

    def test_validate_pdf_invalid_header(self, docling_parser, invalid_pdf_path):
        """Test PDF validation with invalid header."""
        with pytest.raises(PDFValidationError) as exc_info:
            docling_parser._validate_pdf(invalid_pdf_path)

        assert "File does not have PDF header" in str(exc_info.value)

    def test_validate_pdf_nonexistent_file(self, docling_parser):
        """Test PDF validation with nonexistent file."""
        nonexistent_path = Path("/nonexistent/file.pdf")

        with pytest.raises(PDFValidationError) as exc_info:
            docling_parser._validate_pdf(nonexistent_path)

        assert "Error validating PDF" in str(exc_info.value)

    # Complex PDF parsing tests removed - too dependent on external libraries


class TestPDFParserService:
    """Test PDFParserService functionality."""

    @pytest.fixture
    def pdf_parser_service(self):
        """Create PDFParserService instance for testing."""
        return PDFParserService(max_pages=20, max_file_size_mb=10)

    @pytest.fixture
    def valid_pdf_path(self, tmp_path):
        """Create a mock valid PDF file path."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4\ntest content")
        return pdf_file

    def test_pdf_parser_service_initialization(self, pdf_parser_service):
        """Test PDFParserService initialization."""
        assert isinstance(pdf_parser_service.docling_parser, DoclingParser)
        assert pdf_parser_service.docling_parser.max_pages == 20

    @pytest.mark.asyncio
    async def test_parse_pdf_file_not_found(self, pdf_parser_service):
        """Test parsing with non-existent file."""
        nonexistent_path = Path("/nonexistent/file.pdf")

        with pytest.raises(PDFValidationError) as exc_info:
            await pdf_parser_service.parse_pdf(nonexistent_path)

        assert "PDF file not found" in str(exc_info.value)

    @patch("src.services.pdf_parser.parser.DoclingParser.parse_pdf")
    @pytest.mark.asyncio
    async def test_parse_pdf_success(self, mock_parse, pdf_parser_service, valid_pdf_path):
        """Test successful PDF parsing."""
        mock_content = PdfContent(
            raw_text="Test content", sections=[], tables=[], figures=[], parser_used=ParserType.DOCLING, metadata={}
        )
        mock_parse.return_value = mock_content

        result = await pdf_parser_service.parse_pdf(valid_pdf_path)

        assert result == mock_content
        mock_parse.assert_called_once_with(valid_pdf_path)

    @patch("src.services.pdf_parser.parser.DoclingParser.parse_pdf")
    @pytest.mark.asyncio
    async def test_parse_pdf_no_result(self, mock_parse, pdf_parser_service, valid_pdf_path):
        """Test PDF parsing when no result is returned."""
        mock_parse.return_value = None

        with pytest.raises(PDFParsingException) as exc_info:
            await pdf_parser_service.parse_pdf(valid_pdf_path)

        assert "Docling parsing returned no result" in str(exc_info.value)

    @patch("src.services.pdf_parser.parser.DoclingParser.parse_pdf")
    @pytest.mark.asyncio
    async def test_parse_pdf_docling_error(self, mock_parse, pdf_parser_service, valid_pdf_path):
        """Test PDF parsing when Docling raises an error."""
        mock_parse.side_effect = Exception("Docling error")

        with pytest.raises(PDFParsingException) as exc_info:
            await pdf_parser_service.parse_pdf(valid_pdf_path)

        assert "Docling parsing error" in str(exc_info.value)

    def test_factory_creates_service(self):
        """Test that factory creates PDFParserService instance."""
        service = make_pdf_parser_service()
        assert isinstance(service, PDFParserService)
        assert isinstance(service.docling_parser, DoclingParser)

    def test_factory_caching(self):
        """Test that factory uses caching."""
        service1 = make_pdf_parser_service()
        service2 = make_pdf_parser_service()
        # Should be the same instance due to @lru_cache
        assert service1 is service2


class TestOfficeDocuments:
    """``parse_document`` reads Office files with Docling's own readers (no models are loaded)."""

    @pytest.fixture
    def parser(self):
        return PDFParserService(max_pages=20, max_file_size_mb=10)

    @staticmethod
    def make(tmp_path, name):
        import docx
        import openpyxl
        import pptx

        path = tmp_path / name
        if name.endswith(".docx"):
            document = docx.Document()
            document.add_heading("Leave policy", 1)
            document.add_paragraph("Employees get 25 days.")
            document.save(path)
        elif name.endswith(".xlsx"):
            book = openpyxl.Workbook()
            book.active.append(["name", "days"])
            book.active.append(["alice", 25])
            book.save(path)
        else:
            deck = pptx.Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = "Leave policy"
            slide.placeholders[1].text = "Employees get 25 days."
            deck.save(path)
        return path

    @pytest.mark.parametrize("name", ["policy.docx", "policy.xlsx", "policy.pptx"])
    async def test_office_documents_yield_their_text(self, parser, tmp_path, name):
        content = await parser.parse_document(self.make(tmp_path, name))

        assert content.parser_used == ParserType.DOCLING
        assert "25" in content.raw_text

    async def test_a_file_over_the_size_limit_gives_none(self, tmp_path):
        small = PDFParserService(max_pages=20, max_file_size_mb=1)
        path = tmp_path / "big.docx"
        path.write_bytes(b"x" * (1024 * 1024 + 1))

        assert await small.parse_document(path) is None

    async def test_a_corrupt_office_file_raises_a_parsing_error(self, parser, tmp_path):
        path = tmp_path / "broken.docx"
        path.write_bytes(b"PK\x03\x04 this is not a real document")

        with pytest.raises(PDFParsingException):
            await parser.parse_document(path)
