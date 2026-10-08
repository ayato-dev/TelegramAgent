"""PDF bytes from a document that may be wrapped in an attached e-signature (CMS/PKCS#7).

Signed documents (e.g. from government services) often keep the .pdf name while the file
is a PKCS#7 container; Claude only accepts real PDFs.
"""

from asn1crypto import cms

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
