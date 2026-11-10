import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def certificate_paths(settings, directory):
    if settings.local_certificate or settings.local_private_key:
        if not settings.local_certificate or not settings.local_private_key:
            raise ValueError("Zertifikat und privater Schluessel muessen zusammen gesetzt sein.")
        return Path(settings.local_certificate), Path(settings.local_private_key)
    root = Path(directory) / "tls"
    return root / "server.pem", root / "server-key.pem"


def ensure_certificate(settings, directory):
    certificate_path, key_path = certificate_paths(settings, directory)
    if settings.local_certificate or certificate_path.exists() or key_path.exists():
        if not certificate_path.is_file() or not key_path.is_file():
            raise ValueError("TLS-Zertifikatspaar fehlt oder ist unvollstaendig.")
        return certificate_path, key_path
    certificate_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    names = {"localhost", socket.gethostname(), socket.getfqdn()}
    addresses = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    for name in names:
        try:
            addresses.update(ipaddress.ip_address(item[4][0].split("%", 1)[0])
                             for item in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM))
        except OSError:
            pass
    alternatives = [x509.DNSName(name.encode("idna").decode("ascii")) for name in sorted(names)]
    alternatives.extend(x509.IPAddress(address) for address in sorted(addresses, key=str))
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SMTPProxy")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    key_bytes = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(key_bytes)
    try:
        with certificate_path.open("xb") as stream:
            stream.write(certificate.public_bytes(serialization.Encoding.PEM))
    except Exception:
        key_path.unlink()
        raise
    return certificate_path, key_path