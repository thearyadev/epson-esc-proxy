"""HTTP server and request handler for ePOS requests."""

import base64
import platform
import re
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer

from epos_proxy.certs import generate_self_signed_cert
from epos_proxy.config import (
    CERT_FILE,
    DEFAULT_PRINTER_DEVICE,
    DEFAULT_RECEIPT_WIDTH,
    KEY_FILE,
    config,
)
from epos_proxy.printer import kick_drawer, print_receipt


class PrinterProxy(BaseHTTPRequestHandler):
    """HTTP request handler for Epson ePOS print requests."""

    def send_cors_headers(self):
        """Send CORS headers for all responses."""
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):
        """Handle CORS preflight request."""
        print(f"  OPTIONS request from {self.headers.get('Origin')}")
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests - health check."""
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Printer proxy running")

    def do_POST(self):
        """Handle POST requests - print jobs."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")

            print(f"\n[REQUEST] Received {len(post_data)} bytes")
            print(f"  Path: {self.path}")
            print(f"  Origin: {self.headers.get('Origin')}")

            # Check for drawer kick (pulse) command
            pulse_match = re.search(r"<pulse\s*([^/]*)/?\s*>", post_data)
            if pulse_match:
                # Extract drawer pin if specified (default pin 0)
                attrs = pulse_match.group(1)
                pin_match = re.search(r'drawer=["\']?(\d+)["\']?', attrs)
                pin = int(pin_match.group(1)) if pin_match else 0
                print(f"  Drawer kick command (pin {pin})")
                try:
                    kick_drawer(pin)
                except Exception as e:
                    print(f"  Drawer kick failed: {e}")

            image_match = re.search(
                r"<image[^>]*>(.*?)</image>", post_data, re.DOTALL
            )

            width_match = re.search(r'width=["\']?(\d+)["\']?', post_data)
            width = int(width_match.group(1)) if width_match else None

            height_match = re.search(r'height=["\']?(\d+)["\']?', post_data)
            height = int(height_match.group(1)) if height_match else None

            print(f"  Dimensions: {width}x{height}")

            if image_match:
                print("  Decoding image data...")
                b64_string = image_match.group(1).strip()
                raw_data = base64.b64decode(b64_string)

                print(f"  Raw data: {len(raw_data)} bytes")

                # Determine dimensions if not provided
                if not width:
                    width = config.get("receipt_width", DEFAULT_RECEIPT_WIDTH)
                if not height:
                    width_bytes = width // 8
                    height = len(raw_data) // width_bytes if width_bytes > 0 else 0

                if width and height:
                    try:
                        print_receipt(raw_data, width, height)
                        print(f"  Printed successfully ({width}x{height})")
                    except Exception as e:
                        print(f"  Print failed: {e}")
                else:
                    print("  Could not determine image dimensions")
            elif not pulse_match:
                print("  No image data (unhandled command)")

            # EPSON ePOS response format
            response = b"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body>
<response success="true" code="" status="123456" battery="0"/>
</s:Body>
</s:Envelope>"""

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        except Exception as e:
            print(f"  Error: {e}")
            import traceback

            traceback.print_exc()

            self.send_response(500)
            self.send_cors_headers()
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging


def run_server(host: str, port: int, use_https: bool):
    """Start the printer proxy server."""
    server_address = (host, port)
    httpd = HTTPServer(server_address, PrinterProxy)

    protocol = "http"

    if use_https:
        generate_self_signed_cert()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERT_FILE, KEY_FILE)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        protocol = "https"

    # Get display address
    display_host = host if host != "0.0.0.0" else "localhost"
    printer_device = config.get("printer_device") or DEFAULT_PRINTER_DEVICE

    print("")
    print("=" * 50)
    print("  epos-proxy - Epson ePOS Printer Proxy")
    print("=" * 50)
    print(f"  Protocol : {protocol.upper()}")
    print(f"  Address  : {host}:{port}")
    print(f"  Printer  : {printer_device}")
    print(f"  Platform : {platform.system()}")
    print("=" * 50)
    print(f"  URL: {protocol}://{display_host}:{port}")
    if use_https:
        print("")
        print("  Note: Visit the URL in your browser and accept")
        print("  the self-signed certificate before printing.")
    print("=" * 50)
    print("")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()
