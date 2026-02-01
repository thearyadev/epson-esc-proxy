"""Printer connection management and ESC/POS commands."""

import platform
import re
import sys
import time
from functools import wraps

from escpos.printer import File, Network, Usb

from epos_proxy.config import (
    DEFAULT_PRINTER_DEVICE,
    DEFAULT_RECEIPT_WIDTH,
    MAX_RETRIES,
    RETRY_DELAY,
    config,
)

# Global printer instance
_printer = None


def reset_printer():
    """Reset the printer connection."""
    global _printer
    if _printer is not None:
        try:
            _printer.close()
        except Exception:
            pass
        _printer = None


def create_printer():
    """Create a new printer instance based on platform and config."""
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
    if re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", device):
        if ":" in device:
            host, port = device.rsplit(":", 1)
            return Network(host, int(port))
        else:
            return Network(device)  # Default port 9100

    # USB by vendor:product ID
    if device.startswith("USB:"):
        # Format: USB:vendor_id:product_id[:out_ep:in_ep] (hex)
        # e.g., USB:0x04b8:0x0202 or USB:0x154f:0x154f:0x02:0x82
        parts = device.split(":")
        vendor_id = int(parts[1], 16)
        product_id = int(parts[2], 16)
        if len(parts) >= 5:
            out_ep = int(parts[3], 16)
            in_ep = int(parts[4], 16)
            return Usb(vendor_id, product_id, out_ep=out_ep, in_ep=in_ep)
        return Usb(vendor_id, product_id)

    # File-based (Linux /dev/usb/lp0, serial COM port, Windows share)
    return File(device)


def get_printer():
    """Get or create printer instance."""
    global _printer

    if _printer is not None:
        return _printer

    _printer = create_printer()
    return _printer


def with_reconnect(func):
    """Decorator to handle printer reconnection on failure."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                error_type = type(e).__name__
                error_msg = str(e) or "(no message)"
                print(f"  Error (attempt {attempt + 1}/{MAX_RETRIES}): [{error_type}] {error_msg}")
                reset_printer()
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    print("  Reconnecting...")

        raise last_error

    return wrapper


@with_reconnect
def kick_drawer(pin: int = 0) -> bool:
    """Kick the cash drawer."""
    p = get_printer()
    # ESC p m t1 t2 - Generate pulse on pin m
    # m: pin (0 or 1)
    # t1: pulse on time (units of 2ms), 25 = 50ms
    # t2: pulse off time (units of 2ms), 25 = 50ms
    p._raw(bytes([0x1B, 0x70, pin & 1, 25, 25]))
    print(f"  Drawer kicked (pin {pin})")
    return True


@with_reconnect
def print_receipt(image_data: bytes, width: int, height: int) -> bool:
    """Send raster image to printer and cut."""
    p = get_printer()

    # Calculate width in bytes
    width_bytes = width // 8
    raster_data = image_data[: width_bytes * height]

    # Get printer paper width for centering
    paper_width = config.get("receipt_width", DEFAULT_RECEIPT_WIDTH)
    paper_width_bytes = paper_width // 8

    # Center the image if narrower than paper
    if width < paper_width:
        padding_bytes = (paper_width_bytes - width_bytes) // 2
        centered_data = bytearray()

        for y in range(height):
            row_start = y * width_bytes
            row_end = row_start + width_bytes
            row_data = raster_data[row_start:row_end]

            # Pad left, add row, pad right
            centered_data.extend(b"\x00" * padding_bytes)
            centered_data.extend(row_data)
            centered_data.extend(
                b"\x00" * (paper_width_bytes - width_bytes - padding_bytes)
            )

        raster_data = bytes(centered_data)
        width_bytes = paper_width_bytes
        print(f"  Centered: {width}px -> {paper_width}px (padding: {padding_bytes * 8}px)")

    # GS v 0 - Print raster bit image
    xL = width_bytes & 0xFF
    xH = (width_bytes >> 8) & 0xFF
    yL = height & 0xFF
    yH = (height >> 8) & 0xFF

    # Send raster graphics command
    p._raw(b"\x1d\x76\x30\x00")  # GS v 0 m (m=0 normal)
    p._raw(bytes([xL, xH, yL, yH]))
    p._raw(raster_data)

    # Feed and cut
    p._raw(b"\x1b\x64\x06")  # ESC d 6 - Feed 6 lines
    p.cut()

    print(f"  Sent {len(raster_data)} bytes to printer")
    return True
