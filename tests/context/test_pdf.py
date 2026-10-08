import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from tgagent.context.pdf import extract_pdf

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


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
