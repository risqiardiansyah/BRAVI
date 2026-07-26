"""Hand-built minimal-PDF byte generator for ingestion tests.

No PDF-authoring library is a project dependency (only `pypdf`, a *reader*), so
tests construct a minimal-but-valid multi-page PDF directly from raw PDF syntax —
enough for `pypdf` to parse real pages/text back out, without pulling in a new
dependency (`reportlab`/`fpdf`) purely for test fixtures.
"""

from __future__ import annotations


def build_minimal_pdf(pages_text: list[str]) -> bytes:
    """A syntactically valid single/multi-page PDF with one Helvetica text line per
    page, and a byte-accurate xref table (computed from actual object offsets)."""
    buf = bytearray()
    offsets: list[int] = []

    def add_obj(num: int, body: bytes) -> None:
        offsets.append(len(buf))
        buf.extend(f"{num} 0 obj\n".encode())
        buf.extend(body)
        buf.extend(b"\nendobj\n")

    buf.extend(b"%PDF-1.4\n")

    num_pages = len(pages_text)
    page_obj_nums = list(range(3, 3 + num_pages))
    font_obj_num = 3 + num_pages
    content_obj_nums = list(range(font_obj_num + 1, font_obj_num + 1 + num_pages))

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    add_obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {num_pages} >>".encode())

    for i, page_num in enumerate(page_obj_nums):
        content_num = content_obj_nums[i]
        add_obj(
            page_num,
            (
                f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
                f"/MediaBox [0 0 612 792] /Contents {content_num} 0 R >>"
            ).encode(),
        )

    add_obj(font_obj_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for i, content_num in enumerate(content_obj_nums):
        text = pages_text[i].replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
        add_obj(
            content_num,
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        )

    xref_offset = len(buf)
    total_objs = content_obj_nums[-1] + 1
    buf.extend(f"xref\n0 {total_objs}\n".encode())
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets:
        buf.extend(f"{off:010d} 00000 n \n".encode())
    buf.extend(
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(buf)


CORRUPT_PDF_BYTES = b"%PDF-1.4\nthis is not a valid pdf body at all, just garbage {{{ bytes"
