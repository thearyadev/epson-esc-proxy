"""Configuration defaults and global state."""

# Server defaults
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

# Printer defaults
DEFAULT_RECEIPT_WIDTH = 576  # 80mm paper at 203dpi
DEFAULT_PRINTER_DEVICE = "/dev/usb/lp0"  # Linux default

# SSL certificate files
CERT_FILE = "server.crt"
KEY_FILE = "server.key"

# Reconnection settings
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# Runtime configuration (populated by CLI)
config = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "printer_device": None,
    "receipt_width": DEFAULT_RECEIPT_WIDTH,
}
