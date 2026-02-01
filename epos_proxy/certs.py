"""SSL certificate generation."""

import os
import socket
import subprocess

from .config import CERT_FILE, DEFAULT_HOST, KEY_FILE, config


def generate_self_signed_cert() -> bool:
    """Generate a self-signed certificate for HTTPS."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print(f"Using existing certificates: {CERT_FILE}, {KEY_FILE}")
        return True

    print("Generating self-signed SSL certificate...")

    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = "127.0.0.1"

    host = config.get("host", DEFAULT_HOST)
    if host == "0.0.0.0":
        host = local_ip

    # Generate certificate with SAN for IP address
    cmd = f"""openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} \
        -days 365 -nodes \
        -subj "/CN=PrinterProxy" \
        -addext "subjectAltName=IP:127.0.0.1,IP:{local_ip},IP:{host}" """

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        # Fallback for older OpenSSL without -addext
        cmd = f"""openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} \
            -days 365 -nodes -subj "/CN={host}" """
        subprocess.run(cmd, shell=True)

    print("Certificate generated!")
    return True
