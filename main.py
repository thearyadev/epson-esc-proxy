import base64
import re
import os
import ssl
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from PIL import Image

# --- CONFIGURATION ---
SAVE_DIRECTORY = "./print_jobs"
RECEIPT_WIDTH = 576
CERT_FILE = "server.crt"
KEY_FILE = "server.key"
# ---------------------

class PrinterProxy(BaseHTTPRequestHandler):
    
    def send_cors_headers(self):
        """Send CORS headers for all responses"""
        # Allow your specific origin
        origin = self.headers.get('Origin', '*')
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Max-Age', '86400')
    
    def do_OPTIONS(self):
        """Handle CORS preflight request"""
        print(f"OPTIONS request from {self.headers.get('Origin')}")
        self.send_response(200)
        self.send_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()
    
    def do_GET(self):
        """Handle GET requests"""
        self.send_response(200)
        self.send_cors_headers()
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Printer proxy running')
    
    def raw_to_image(self, data, width=None):
        """Convert raw bitmap data to image"""
        
        start = 0
        for i, b in enumerate(data):
            if b != 0:
                start = i
                break
        
        end = len(data)
        for i in range(len(data) - 1, -1, -1):
            if data[i] != 0:
                end = i + 1
                break
        
        print(f"Non-zero data from byte {start} to {end}")
        if end > start:
            print(f"First non-zero bytes: {data[start:start+50].hex()}")
        
        widths_to_try = [72, 64, 58, 54, 48, 80, 42, 70, 56, 52]
        
        if width:
            widths_to_try = [width // 8] + widths_to_try
        
        images = []
        
        for width_bytes in widths_to_try:
            width_pixels = width_bytes * 8
            height = len(data) // width_bytes
            
            if height < 10:
                continue
            
            img = Image.new('RGB', (width_pixels, height), (255, 255, 255))
            pixels = img.load()
            
            black_pixels = 0
            
            for y in range(height):
                for x_byte in range(width_bytes):
                    idx = y * width_bytes + x_byte
                    if idx < len(data):
                        byte = data[idx]
                        for bit in range(8):
                            x = x_byte * 8 + bit
                            if (byte >> (7 - bit)) & 1:
                                pixels[x, y] = (0, 0, 0)
                                black_pixels += 1
            
            total_pixels = width_pixels * height
            ratio = black_pixels / total_pixels if total_pixels > 0 else 0
            
            print(f"Width {width_pixels}px ({width_bytes} bytes): {height}h, {black_pixels} black pixels ({ratio:.2%})")
            
            if 0.01 < ratio < 0.50:
                images.append((img, ratio, width_pixels, height))
        
        if images:
            images.sort(key=lambda x: abs(x[1] - 0.15))
            return images[0][0]
        
        width_bytes = 72
        width_pixels = 576
        height = len(data) // width_bytes
        
        img = Image.new('RGB', (width_pixels, height), (255, 255, 255))
        pixels = img.load()
        
        for y in range(height):
            for x_byte in range(width_bytes):
                idx = y * width_bytes + x_byte
                if idx < len(data):
                    byte = data[idx]
                    for bit in range(8):
                        x = x_byte * 8 + bit
                        if (byte >> (7 - bit)) & 1:
                            pixels[x, y] = (0, 0, 0)
        
        return img
    
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            
            print(f"\n{'='*50}")
            print(f"Received print job ({len(post_data)} bytes)")
            print(f"Path: {self.path}")
            print(f"Origin: {self.headers.get('Origin')}")

            image_match = re.search(r'<image[^>]*>(.*?)</image>', post_data, re.DOTALL)
            
            width_match = re.search(r'width=["\']?(\d+)["\']?', post_data)
            width = int(width_match.group(1)) if width_match else None
            
            height_match = re.search(r'height=["\']?(\d+)["\']?', post_data)
            height = int(height_match.group(1)) if height_match else None
            
            print(f"Width: {width}, Height: {height}")
            
            if image_match:
                print("Found image data, decoding...")
                b64_string = image_match.group(1).strip()
                raw_data = base64.b64decode(b64_string)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                
                print(f"Raw data: {len(raw_data)} bytes")
                
                if width and height:
                    width_bytes = width // 8
                    
                    img = Image.new('RGB', (width, height), (255, 255, 255))
                    pixels = img.load()
                    
                    for y in range(height):
                        for x_byte in range(width_bytes):
                            idx = y * width_bytes + x_byte
                            if idx < len(raw_data):
                                byte = raw_data[idx]
                                for bit in range(8):
                                    x = x_byte * 8 + bit
                                    if x < width and (byte >> (7 - bit)) & 1:
                                        pixels[x, y] = (0, 0, 0)
                    
                    filename = f"print_job_{timestamp}.png"
                    filepath = os.path.join(SAVE_DIRECTORY, filename)
                    img.save(filepath, 'PNG')
                    print(f"✓ Saved: {filepath} ({width}x{height})")
                else:
                    img = self.raw_to_image(raw_data, width)
                    if img:
                        filename = f"print_job_{timestamp}.png"
                        filepath = os.path.join(SAVE_DIRECTORY, filename)
                        img.save(filepath, 'PNG')
                        print(f"✓ Saved: {filepath}")
            else:
                print("No image data found")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                with open(os.path.join(SAVE_DIRECTORY, f"request_{timestamp}.txt"), 'w') as f:
                    f.write(post_data)

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
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            
            self.send_response(500)
            self.send_cors_headers()
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress default logging

def generate_self_signed_cert():
    """Generate a self-signed certificate"""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print(f"Using existing certificates: {CERT_FILE}, {KEY_FILE}")
        return True
    
    print("Generating self-signed SSL certificate...")
    
    # Get local IP for the certificate
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    # Generate certificate with SAN for IP address
    cmd = f'''openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} \
        -days 365 -nodes \
        -subj "/CN=PrinterProxy" \
        -addext "subjectAltName=IP:127.0.0.1,IP:{local_ip},IP:192.168.30.5"'''
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode != 0:
        # Fallback for older OpenSSL without -addext
        cmd = f'''openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} \
            -days 365 -nodes -subj "/CN=192.168.30.5"'''
        subprocess.run(cmd, shell=True)
    
    print(f"✓ Certificate generated!")
    return True

def run():
    os.makedirs(SAVE_DIRECTORY, exist_ok=True)
    
    generate_self_signed_cert()
    
    server_address = ('0.0.0.0', 8000)
    httpd = HTTPServer(server_address, PrinterProxy)
    
    # Wrap with SSL
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
    print(f"\n{'='*50}")
    print(f"HTTPS Printer Proxy running on port 8000")
    print(f"Saving to: {os.path.abspath(SAVE_DIRECTORY)}")
    print(f"{'='*50}")
    print(f"\nConfigure printer as: https://192.168.30.5:8000")
    print(f"\n⚠️  IMPORTANT: First visit https://192.168.30.5:8000 in your")
    print(f"   browser and accept the self-signed certificate!")
    print(f"{'='*50}\n")
    
    httpd.serve_forever()

if __name__ == '__main__':
    run()
