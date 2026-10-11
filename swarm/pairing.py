"""Pairing bundles, local TLS certificate provisioning and opt-in LAN discovery."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import time
from .private_listener import load_settings
from .providers import RouteError


def generate_certificate(root, hostname):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    path = Path(root) / "certificates"
    path.mkdir(exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    try: alternative = x509.IPAddress(ipaddress.ip_address(hostname))
    except ValueError: alternative = x509.DNSName(hostname)
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([alternative]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    suffix = str(time.time_ns())
    cert_file, key_file = path / (suffix + ".pem"), path / (suffix + "-key.pem")
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_file.chmod(0o600)
    return dict(cert_file=str(cert_file), key_file=str(key_file), fingerprint=certificate.fingerprint(hashes.SHA256()).hex(), hostname=hostname)


def create_bundle(root, pool):
    settings = load_settings(Path(root))
    if not settings.enabled:
        raise RouteError("Enable the private TLS listener before creating a pairing bundle")
    cert = Path(settings.cert_file).read_text(encoding="utf-8")
    if len(cert) > 16000: raise RouteError("Certificate chain is too large for a pairing bundle")
    invite = pool.invite()
    data = dict(version=1, origin=settings.public_origin, invite=invite["token"], expires_at=invite["expires_at"], ca_pem=cert)
    return dict(bundle=base64.urlsafe_b64encode(json.dumps(data).encode()).decode(), expires_at=invite["expires_at"], origin=settings.public_origin)


def qr_image(bundle):
    import io
    import qrcode
    from qrcode.image.svg import SvgPathImage
    try:
        image=qrcode.make(bundle,image_factory=SvgPathImage,border=4)
        output=io.BytesIO()
        image.save(output)
        return "data:image/svg+xml;base64,"+base64.b64encode(output.getvalue()).decode()
    except qrcode.exceptions.DataOverflowError as error:
        raise RouteError("This bundle exceeds QR capacity; use the copied pairing bundle") from error


def discover():
    from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
    values = []
    class Listener(ServiceListener):
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name, timeout=500)
            if info:
                origin = info.properties.get(b"origin", b"").decode(errors="replace")
                if origin.startswith("https://"):
                    values.append(dict(name=name, origin=origin, fingerprint=info.properties.get(b"fingerprint", b"").decode(errors="replace")))
        def update_service(self, *args): pass
        def remove_service(self, *args): pass
    with Zeroconf() as zc:
        browser = ServiceBrowser(zc, "_swarm-workbench._tcp.local.", Listener())
        time.sleep(2)
        browser.cancel()
    return values


def advertise(origin, certificate):
    from zeroconf import Zeroconf, ServiceInfo
    from urllib.parse import urlparse
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    parsed = urlparse(origin)
    cert = x509.load_pem_x509_certificate(Path(certificate).read_bytes())
    addresses = list({socket.inet_pton(family,address[0]) for family,_,_,_,address in socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM) if family in {socket.AF_INET,socket.AF_INET6}})
    info = ServiceInfo("_swarm-workbench._tcp.local.", socket.gethostname() + "._swarm-workbench._tcp.local.",
        addresses=addresses, port=parsed.port or 443, properties={"origin": origin, "fingerprint": cert.fingerprint(hashes.SHA256()).hex()},
        server=socket.gethostname() + ".local.")
    zc = Zeroconf()
    zc.register_service(info)
    return zc, info
