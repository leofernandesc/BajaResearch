"""Small structurally valid PDF for HTTP mock responses."""

from io import BytesIO

from pypdf import PdfWriter


def make_pdf_bytes(pages: int = 2) -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    writer.write(output)
    return output.getvalue()


PDF_BYTES = make_pdf_bytes()
