import datetime
import io

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from tgagent.context.pdf import extract_pdf, pdf_text

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def text_pdf(*pages: str) -> bytes:
    """A real PDF with one line of Helvetica text per page."""
    count = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
    ]
    font = 3 + 2 * count
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def signed(data: bytes, encoding: serialization.Encoding = serialization.Encoding.DER) -> bytes:
    """An attached (non-detached) PKCS#7 signature, like documents signed with a qualified e-signature."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signer")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(data)
        .add_signer(cert, key, hashes.SHA256())
        .sign(encoding, [pkcs7.PKCS7Options.Binary])
    )


def test_plain_pdf_is_returned_unchanged() -> None:
    assert extract_pdf(PDF) == PDF


def test_pdf_is_extracted_from_signed_container() -> None:
    assert extract_pdf(signed(PDF)) == PDF


def test_signed_container_without_pdf_is_rejected() -> None:
    assert extract_pdf(signed(b"just some text")) is None


def test_garbage_is_rejected() -> None:
    assert extract_pdf(b"\x00\x01not a pdf at all") is None
    assert extract_pdf(b"") is None


def test_pdf_text_reads_every_page() -> None:
    assert pdf_text(text_pdf("first page", "second page"), limit=1000) == "first page\nsecond page"


def test_pdf_text_unwraps_signed_documents_and_stops_at_the_limit() -> None:
    text = pdf_text(signed(text_pdf("a" * 50, "b" * 50, "c" * 50)), limit=60)

    assert text is not None
    assert "c" not in text


def test_pdf_text_of_garbage_is_none() -> None:
    assert pdf_text(b"\x00garbage", limit=100) is None
