"""PDF bytes from a document that may be wrapped in an attached e-signature (CMS/PKCS#7).

Signed documents (e.g. from government services) often keep the .pdf name while the file
is a PKCS#7 container; Claude only accepts real PDFs.
"""

import io
import logging

from asn1crypto import cms
from pypdf import PdfReader

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
MAX_NESTING = 3


def extract_pdf(data: bytes) -> bytes | None:
    for _ in range(MAX_NESTING):
        if data.startswith(PDF_MAGIC):
            return data
        try:
            info = cms.ContentInfo.load(data)
            if info["content_type"].native != "signed_data":
                return None
            inner = info["content"]["encap_content_info"]["content"].native
        except Exception:
            return None
        if not isinstance(inner, bytes):
            return None
        data = inner
    return None


def pdf_text(data: bytes, *, limit: int) -> str | None:
    """Text of a PDF (signed ones unwrapped) for models that cannot read PDFs, about ``limit`` characters."""
    pdf = extract_pdf(data)
    if pdf is None:
        return None
    pages: list[str] = []
    size = 0
    try:
        for page in PdfReader(io.BytesIO(pdf)).pages:
            if size >= limit:
                break
            text = page.extract_text() or ""
            pages.append(text)
            size += len(text)
    except Exception:
        log.warning("could not extract PDF text", exc_info=True)
        return None
    return "\n".join(pages).strip()
