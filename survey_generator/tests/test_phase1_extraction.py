from __future__ import annotations

from survey_generator.phase1.extract import extract_file, extraction_report_markdown, extraction_report_xlsx_bytes
from .helpers import docx_bytes, minimal_pdf_bytes, pptx_bytes, xlsx_bytes


def test_file_extraction_supported_formats():
    cases = [
        ("brief.pdf", minimal_pdf_bytes("PDF extraction unique text"), "PDF extraction unique text"),
        ("brief.docx", docx_bytes("DOCX extraction unique text"), "DOCX extraction unique text"),
        ("deck.pptx", pptx_bytes("PPTX extraction unique text"), "PPTX extraction unique text"),
        ("data.xlsx", xlsx_bytes(), "Alpha"),
        ("notes.txt", b"Plain text extraction unique text", "Plain text extraction unique text"),
        ("notes.csv", b"A,B\nAlpha,Beta\n", "Alpha"),
    ]
    docs = []
    for filename, payload, expected in cases:
        doc = extract_file(filename, payload)
        docs.append(doc)
        assert expected in doc.text
        assert doc.extracted_char_count > 0
        assert not doc.errors
    assert "phase 1 extraction report" in extraction_report_markdown(docs).lower()
    assert len(extraction_report_xlsx_bytes(docs)) > 1000


def test_unsupported_empty_and_weak_extraction_are_flagged():
    unsupported = extract_file("legacy.doc", b"not a real doc")
    assert unsupported.weak_extraction
    assert unsupported.errors
    empty = extract_file("empty.txt", b"")
    assert empty.weak_extraction
    assert any("No extractable text" in w for w in empty.warnings)
    weak = extract_file("short.txt", b"tiny")
    assert weak.weak_extraction
    assert any("Weak extraction" in w for w in weak.warnings)


def test_pptx_control_characters_are_sanitized_for_excel_exports():
    doc = extract_file("deck_with_control_char.pptx", pptx_bytes("Hidden\x0bcontrol character"))
    assert "\x0b" not in doc.text
    assert "Hidden control character" in doc.text

    # These calls used to raise openpyxl IllegalCharacterError when extracted
    # PPTX text contained invisible control characters.
    assert len(extraction_report_xlsx_bytes([doc])) > 1000

    from survey_generator.phase1.export import hypotheses_export_xlsx_bytes

    assert len(hypotheses_export_xlsx_bytes([
        {
            "hypothesis_id": "H001",
            "review_status": "Approved",
            "priority": "Medium",
            "text": "Control\x0bcharacter hypothesis",
        }
    ], [doc], [])) > 1000
