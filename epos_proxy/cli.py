"""Command-line interface."""

import argparse

from epos_proxy.config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_RECEIPT_WIDTH, config
from epos_proxy.server import run_server


def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        prog="epos-proxy",
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
        """,
    )

    parser.add_argument(
        "-H",
        "--host",
        default=DEFAULT_HOST,
        metavar="ADDR",
        help=f"Server bind address (default: {DEFAULT_HOST})",
    )

    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"Server port (default: {DEFAULT_PORT})",
    )

    parser.add_argument(
        "--https",
        action="store_true",
        help="Enable HTTPS with self-signed certificate",
    )

    parser.add_argument(
        "--printer",
        metavar="DEVICE",
        help="Printer: IP address, USB:vid:pid, COM port, or device path",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_RECEIPT_WIDTH,
        metavar="PX",
        help=f"Receipt width in pixels (default: {DEFAULT_RECEIPT_WIDTH})",
    )

    args = parser.parse_args()

    # Store config globally
    config["host"] = args.host
    config["port"] = args.port
    config["printer_device"] = args.printer
    config["receipt_width"] = args.width

    run_server(args.host, args.port, args.https)


if __name__ == "__main__":
    main()
