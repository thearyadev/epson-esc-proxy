#!/usr/bin/env python3
"""
Priont - Epson ePOS Printer Proxy

Receives Epson ePOS requests over HTTP/HTTPS and prints them via a connected
thermal receipt printer.
"""

import argparse
import base64
import os
import platform
import re
import ssl
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

from escpos.printer import Usb, File, Network

# --- CONFIGURATION DEFAULTS ---
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_RECEIPT_WIDTH = 576
CERT_FILE = "server.crt"
KEY_FILE = "server.key"

# Linux default
DEFAULT_PRINTER_DEVICE = "/dev/usb/lp0"
# Windows: use printer share name or USB ids
# ---------------------------------

# Global printer instance
printer = None
config = {}


def get_printer():
    """Get or create printer instance based on platform and config."""
    global printer

    if printer is not None:
        return printer

    system = platform.system()
    device = config.get("printer_device")

    if not device:
        if system == "Windows":
            print("Error: On Windows, you must specify a printer device.")
            print("  Use --printer with one of:")
            print("    - 192.168.1.87        (Network printer IP, recommended)")
            print("    - 192.168.1.87:9100   (Network printer IP:port)")
            print("    - USB:0x04b8:0x0202   (USB vendor:product IDs in hex)")
            print("    - COM3                (Serial port)")
            sys.exit(1)
        else:
            device = DEFAULT_PRINTER_DEVICE

    # Network printer (IP address or IP:port)
    if re.match(r'^\d+\.\d+\.\d+\.\d+(:\d+)?$', device):
        if ':' in device:
            host, port = device.rsplit(':', 1)
            printer = Network(host, int(port))
        else:
            printer = Network(device)  # Default port 9100
        return printer

    # USB by vendor:product ID
    if device.startswith("USB:"):
        # Format: USB:vendor_id:product_id (hex)
        # e.g., USB:0x04b8:0x0202
        parts = device.split(":")
        vendor_id = int(parts[1], 16)
        product_id = int(parts[2], 16)
        printer = Usb(vendor_id, product_id)
        return printer

    # File-based (Linux /dev/usb/lp0, serial COM port, Windows share)
    printer = File(device)
    return printer


def kick_drawer(pin: int = 0) -> bool:
    """Kick the cash drawer."""
    try:
        p = get_printer()
        # ESC p m t1 t2 - Generate pulse on pin m
        # m: pin (0 or 1)
        # t1: pulse on time (units of 2ms), 25 = 50ms
        # t2: pulse off time (units of 2ms), 25 = 50ms
        p._raw(bytes([0x1b, 0x70, pin & 1, 25, 25]))
        print(f"  Drawer kicked (pin {pin})")
        return True
    except Exception as e:
        print(f"  Drawer kick error: {e}")
        return False


def print_receipt(image_data: bytes, width: int, height: int) -> bool:
    """Send raster image to printer and cut."""
    try:
        p = get_printer()

        # Calculate width in bytes
        width_bytes = width // 8

        # Build raster data with proper dimensions
        raster_data = image_data[:width_bytes * height]

        # Use escpos to print raster image
        # The image method expects a PIL Image, so we use _raw for direct raster
        # GS v 0 - Print raster bit image
        xL = width_bytes & 0xFF
        xH = (width_bytes >> 8) & 0xFF
        yL = height & 0xFF
        yH = (height >> 8) & 0xFF

        # Send raster graphics command
        p._raw(b'\x1d\x76\x30\x00')  # GS v 0 m (m=0 normal)
        p._raw(bytes([xL, xH, yL, yH]))
        p._raw(raster_data)

        # Feed and cut
        p._raw(b'\x1b\x64\x06')  # ESC d 6 - Feed 6 lines
        p.cut()

        print(f"  Sent {len(raster_data)} bytes to printer")
        return True
    except Exception as e:
        print(f"  Printer error: {e}")
        return False


class PrinterProxy(BaseHTTPRequestHandler):
    """HTTP request handler for Epson ePOS print requests."""

    def send_cors_headers(self):
        """Send CORS headers for all responses."""
        origin = self.headers.get('Origin', '*')
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Max-Age', '86400')

    def do_OPTIONS(self):
        """Handle CORS preflight request."""
        print(f"  OPTIONS request from {self.headers.get('Origin')}")
        self.send_response(200)
        self.send_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests - health check."""
        self.send_response(200)
        self.send_cors_headers()
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Printer proxy running')

    def do_POST(self):
        """Handle POST requests - print jobs."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')

            print(f"\n[REQUEST] Received {len(post_data)} bytes")
            print(f"  Path: {self.path}")
            print(f"  Origin: {self.headers.get('Origin')}")

            # Check for drawer kick (pulse) command
            pulse_match = re.search(r'<pulse\s*([^/]*)/?\s*>', post_data)
            if pulse_match:
                # Extract drawer pin if specified (default pin 0)
                attrs = pulse_match.group(1)
                pin_match = re.search(r'drawer=["\']?(\d+)["\']?', attrs)
                pin = int(pin_match.group(1)) if pin_match else 0
                print(f"  Drawer kick command (pin {pin})")
                kick_drawer(pin)

            image_match = re.search(r'<image[^>]*>(.*?)</image>', post_data, re.DOTALL)

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
                    if print_receipt(raw_data, width, height):
                        print(f"  Printed successfully ({width}x{height})")
                    else:
                        print("  Print failed")
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
            self.send_header('Content-Type', 'text/xml; charset=utf-8')
            self.send_header('Content-Length', str(len(response)))
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


def generate_self_signed_cert():
    """Generate a self-signed certificate for HTTPS."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print(f"Using existing certificates: {CERT_FILE}, {KEY_FILE}")
        return True

    print("Generating self-signed SSL certificate...")

    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = "127.0.0.1"

    host = config.get("host", DEFAULT_HOST)
    if host == "0.0.0.0":
        host = local_ip

    # Generate certificate with SAN for IP address
    cmd = f'''openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} \
        -days 365 -nodes \
        -subj "/CN=PrinterProxy" \
        -addext "subjectAltName=IP:127.0.0.1,IP:{local_ip},IP:{host}"'''

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        # Fallback for older OpenSSL without -addext
        cmd = f'''openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} \
            -days 365 -nodes -subj "/CN={host}"'''
        subprocess.run(cmd, shell=True)

    print("Certificate generated!")
    return True


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

    print("")
    print("=" * 50)
    print(f"  Priont - Epson ePOS Printer Proxy")
    print("=" * 50)
    print(f"  Protocol : {protocol.upper()}")
    print(f"  Address  : {host}:{port}")
    print(f"  Printer  : {config.get('printer_device', DEFAULT_PRINTER_DEVICE)}")
    print(f"  Platform : {platform.system()}")
    print("=" * 50)
    print(f"  URL: {protocol}://{display_host}:{port}")
    if use_https:
        print("")
        print(f"  Note: Visit the URL in your browser and accept")
        print(f"  the self-signed certificate before printing.")
    print("=" * 50)
    print("")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()


def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        prog="priont",
        description="Epson ePOS Printer Proxy - Receive ePOS requests and print to a thermal printer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          Start HTTP server on 0.0.0.0:8000
  %(prog)s --https                  Start HTTPS server with self-signed cert
  %(prog)s -p 9100                  Use port 9100
  %(prog)s -H 192.168.1.100         Bind to specific IP
  %(prog)s --printer /dev/usb/lp1   Use different printer device (Linux)

Network Printer (recommended for Windows):
  %(prog)s --printer 192.168.1.87         Network printer (default port 9100)
  %(prog)s --printer 192.168.1.87:9100    Network printer with explicit port

Other Options:
  %(prog)s --printer USB:0x04b8:0x0202    USB printer by vendor:product ID
  %(prog)s --printer COM3                  Serial printer (Windows)
  %(prog)s --printer /dev/usb/lp0          USB device file (Linux)
        """
    )

    parser.add_argument(
        "-H", "--host",
        default=DEFAULT_HOST,
        metavar="ADDR",
        help=f"Server bind address (default: {DEFAULT_HOST})"
    )

    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"Server port (default: {DEFAULT_PORT})"
    )

    parser.add_argument(
        "--https",
        action="store_true",
        help="Enable HTTPS with self-signed certificate"
    )

    parser.add_argument(
        "--printer",
        metavar="DEVICE",
        help="Printer: IP address, USB:vid:pid, COM port, or device path"
    )

    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_RECEIPT_WIDTH,
        metavar="PX",
        help=f"Receipt width in pixels (default: {DEFAULT_RECEIPT_WIDTH})"
    )

    args = parser.parse_args()

    # Store config globally
    config["host"] = args.host
    config["port"] = args.port
    config["printer_device"] = args.printer
    config["receipt_width"] = args.width

    run_server(args.host, args.port, args.https)


if __name__ == '__main__':
    main()
