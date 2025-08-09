#!/usr/bin/env python3
"""
Test script for payment proof analyzer

This script tests the payment proof analyzer functionality by creating
a local HTTP server to serve a sample receipt file. This allows us to
test the analyzer with a local file, avoiding any rate limiting issues.

Usage:
    python test_payment_proof.py
"""
import os
import sys
import io
import asyncio
import json
import base64
import http.server
import socketserver
import threading
import tempfile
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from PIL import Image

# Load environment variables from .env file
load_dotenv()

# Explicitly set the OPENAI_API_KEY before importing the analyzer module
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

# Now import the analyzer module
from app.payment_proof_analyzer import analyze_payment_proof

# Constants
PORT = 8765
SAMPLE_DIR = "/home/robin/watibot3/test_samples"
SAMPLE_FILENAME = "sample_receipt.jpeg"
SAMPLE_PATH = os.path.join(SAMPLE_DIR, SAMPLE_FILENAME)


# Create a realistic sample CompraClick receipt image
def create_sample_receipt():
    """Create a realistic sample CompraClick receipt image with payment information"""
    print("Creating sample CompraClick receipt image...")
    
    # Create a white background image
    width, height = 800, 1000
    img = Image.new('RGB', (width, height), color='white')
    
    # Add simulated content based on the CompraClick receipt shared by the user
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    
    # Try to use a system font, or fall back to default
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        regular_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except IOError:
        title_font = ImageFont.load_default()
        regular_font = ImageFont.load_default()
    
    # Draw header
    draw.text((width/2, 100), "LAS HOJAS RESORT BP", fill="black", font=title_font, anchor="mm")
    draw.text((width/2, 140), "36156201", fill="black", font=regular_font, anchor="mm")
    draw.text((width/2, 180), "Teléfono: 2325-7000", fill="black", font=regular_font, anchor="mm")
    
    # Draw receipt info
    draw.text((width/2, 240), "RECIBO 20999514", fill="black", font=title_font, anchor="mm")
    draw.text((width/2, 280), "10/07/25 - 11:07 hrs.", fill="black", font=regular_font, anchor="mm")
    
    # Draw customer info
    draw.text((200, 350), "MOISES RIVAS ZAMORA 8773", fill="black", font=regular_font)
    
    # Draw amount
    draw.text((600, 350), "369.90 USD", fill="black", font=regular_font)
    
    # Draw totals
    draw.line([(100, 400), (700, 400)], fill="gray", width=1)
    draw.text((200, 430), "Subtotal", fill="black", font=regular_font)
    draw.text((600, 430), "369.90 USD", fill="black", font=regular_font)
    draw.text((200, 470), "Total", fill="black", font=title_font)
    draw.text((600, 470), "369.90 USD", fill="black", font=title_font)
    
    # Draw payment method
    draw.line([(100, 520), (700, 520)], fill="gray", width=1)
    draw.text((width/2, 550), "Compraclick", fill="black", font=title_font, anchor="mm")
    
    # Draw transaction details
    draw.text((200, 600), "Cliente:", fill="black", font=regular_font)
    draw.text((500, 600), "Papelera El Pital Moises Rivas Zamora", fill="black", font=regular_font)
    draw.text((200, 640), "Visa:", fill="black", font=regular_font)
    draw.text((500, 640), "419538XXXXXX4872-M", fill="black", font=regular_font)
    draw.text((200, 680), "Autorización:", fill="black", font=regular_font)
    draw.text((500, 680), "239037", fill="black", font=regular_font)
    
    # Draw footer
    draw.line([(100, 720), (700, 720)], fill="gray", width=1)
    draw.text((width/2, 750), "Transacción por Compra-Click", fill="black", font=title_font, anchor="mm")
    draw.text((width/2, 790), "No requiere firma", fill="black", font=regular_font, anchor="mm")
    
    # Save the image
    os.makedirs(os.path.dirname(SAMPLE_PATH), exist_ok=True)
    img.save(SAMPLE_PATH)
    print(f"Sample receipt saved to {SAMPLE_PATH}")
    return SAMPLE_PATH


# Simple HTTP Server to serve the sample file
class SimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SAMPLE_DIR, **kwargs)
    
    def log_message(self, format, *args):
        # Suppress log messages
        pass


# Start the HTTP server in a separate thread
def start_http_server():
    handler = SimpleHTTPRequestHandler
    with socketserver.TCPServer(("localhost", PORT), handler) as httpd:
        print(f"Serving at http://localhost:{PORT}/")
        httpd.serve_forever()


# Direct test using bytes instead of URL
async def test_direct_with_image(image_path: str) -> Dict[str, Any]:
    """Test the analyzer directly with an image file"""
    print(f"Testing with direct image: {image_path}")
    
    # Create a dummy analyze function that bypasses URL downloading
    from app.payment_proof_analyzer import analyze_with_o4_mini
    
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    
    # Convert image bytes to base64
    img = Image.open(io.BytesIO(image_bytes))
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    # Create image data for o4-mini
    image_data = [{"type": "image", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}]
    
    try:
        # Call the o4-mini analyzer directly
        result = await analyze_with_o4_mini(image_data)
        return result
    except Exception as e:
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "extracted_info": {},
            "error": f"Error in direct test: {str(e)}"
        }


# Create a fake PDF for testing
def create_sample_pdf() -> str:
    """Create a sample PDF receipt"""
    pdf_path = os.path.join(SAMPLE_DIR, "sample_receipt.pdf")
    
    try:
        # Use ReportLab to generate a simple PDF
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        
        c = canvas.Canvas(pdf_path, pagesize=letter)
        c.drawString(100, 750, "SAMPLE BANK TRANSFER RECEIPT")
        c.drawString(100, 700, "Date: 10/07/2025")
        c.drawString(100, 675, "Amount: $250.00")
        c.drawString(100, 650, "From: John Smith")
        c.drawString(100, 625, "To: Vacation Rentals")
        c.drawString(100, 600, "Transaction ID: TRX123456789")
        c.drawString(100, 575, "Status: COMPLETED")
        c.save()
        
        print(f"Sample PDF receipt created at {pdf_path}")
        return pdf_path
    except Exception as e:
        print(f"Could not create PDF: {str(e)}")
        return ""


async def main():
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is not set.")
        print("Please set it in your .env file or export it before running this script.")
        sys.exit(1)
    
    # Create sample directory if it doesn't exist
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    
    # Create the sample image
    sample_path = create_sample_receipt()
    
    # Start HTTP server in a thread
    server_thread = threading.Thread(target=start_http_server, daemon=True)
    server_thread.start()
    
    # Wait a moment for the server to start
    await asyncio.sleep(1)
    
    # Test URL for our local file
    test_url = f"http://localhost:{PORT}/{SAMPLE_FILENAME}"
    
    print(f"\nAnalyzing payment proof from URL: {test_url}")
    try:
        result = await analyze_payment_proof(test_url)
        print("\nAnalysis Results:")
        print(json.dumps(result, indent=2))
        
        if result["success"]:
            print("\n✅ Analysis completed successfully.")
            if result["is_valid_receipt"]:
                print(f"✅ Valid receipt detected: {result['receipt_type']}")
                print(f"📊 Extracted information:")
                for key, value in result["extracted_info"].items():
                    print(f"  - {key}: {value}")
            else:
                print("❌ No valid receipt detected in the image/PDF.")
        else:
            print(f"\n❌ Analysis failed: {result['error']}")
    except Exception as e:
        print(f"Error: {str(e)}")
        
    # Try with direct image test
    print("\n\nTrying direct image test (bypassing URL download)...")
    try:
        direct_result = await test_direct_with_image(sample_path)
        print("\nDirect Analysis Results:")
        print(json.dumps(direct_result, indent=2))
    except Exception as e:
        print(f"Direct test error: {str(e)}")
    
    print("\nTests completed.")
    

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
    except Exception as e:
        print(f"Test failed with error: {str(e)}")

