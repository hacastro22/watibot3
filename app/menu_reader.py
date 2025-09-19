"""
Menu Image Reader Tool
Converts PDF menu to PNG images for visual interpretation by the assistant
"""

import logging
import os
import time as _time
import fitz  # PyMuPDF
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

def read_menu_content() -> List[str]:
    """
    Converts the current menu PDF to PNG images for visual interpretation.
    
    Returns:
        List[str]: List of file paths to menu page images, or error message if conversion fails
    """
    menu_pdf_path = "/home/robin/watibot4/app/resources/menu.pdf"
    
    try:
        logger.info("[MENU_READER] Converting menu PDF to images")
        
        # Create output directory
        out_dir = Path(__file__).resolve().parent / "resources" / "pictures" / "menu_converted"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate timestamp for unique filenames
        ts = int(_time.time())
        
        # Convert PDF to images
        doc = fitz.open(menu_pdf_path)
        image_paths = []
        
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=144)  # High resolution for clarity
            image_path = out_dir / f"menu_{ts}_p{i+1}.png"
            pix.save(str(image_path))
            image_paths.append(str(image_path))
            logger.info(f"[MENU_READER] Created menu page {i+1}: {image_path}")
        
        doc.close()
        
        if not image_paths:
            return ["Error: No se pudieron generar imágenes del menú PDF."]
        
        logger.info(f"[MENU_READER] Successfully converted menu to {len(image_paths)} images")
        return image_paths
        
    except FileNotFoundError:
        error_msg = "Error: No se encontró el archivo del menú PDF."
        logger.error(f"[MENU_READER] {error_msg}")
        return [error_msg]
        
    except Exception as e:
        error_msg = f"Error al convertir el menú PDF: {str(e)}"
        logger.error(f"[MENU_READER] {error_msg}")
        return [error_msg]

def get_latest_menu_images() -> List[str]:
    """
    Gets the most recently generated menu images from the converted directory.
    
    Returns:
        List[str]: List of paths to the latest menu images
    """
    try:
        out_dir = Path(__file__).resolve().parent / "resources" / "pictures" / "menu_converted"
        
        if not out_dir.exists():
            return ["Error: No existen imágenes del menú convertidas."]
        
        # Find all menu image files
        menu_files = list(out_dir.glob("menu_*_p*.png"))
        
        if not menu_files:
            return ["Error: No se encontraron imágenes del menú."]
        
        # Group by timestamp and get the latest set
        timestamps = set()
        for file in menu_files:
            # Extract timestamp from filename like "menu_1756480126_p1.png"
            parts = file.stem.split('_')
            if len(parts) >= 2:
                timestamps.add(parts[1])
        
        if not timestamps:
            return ["Error: No se pudieron identificar imágenes del menú válidas."]
        
        # Get the most recent timestamp
        latest_ts = max(timestamps)
        
        # Get all pages for the latest timestamp
        latest_images = []
        for i in range(1, 10):  # Support up to 9 pages
            image_path = out_dir / f"menu_{latest_ts}_p{i}.png"
            if image_path.exists():
                latest_images.append(str(image_path))
        
        logger.info(f"[MENU_READER] Found {len(latest_images)} latest menu images")
        return latest_images
        
    except Exception as e:
        error_msg = f"Error al obtener imágenes del menú: {str(e)}"
        logger.error(f"[MENU_READER] {error_msg}")
        return [error_msg]

async def read_menu_content_wrapper() -> str:
    """
    Async wrapper that returns image paths for menu analysis.
    
    Returns:
        str: Formatted message with menu image paths for visual analysis
    """
    try:
        # First try to get existing latest images
        existing_images = get_latest_menu_images()
        
        # If no existing images or error, create new ones
        if not existing_images or existing_images[0].startswith("Error:"):
            logger.info("[MENU_READER] No existing images found, creating new ones")
            image_paths = read_menu_content()
        else:
            image_paths = existing_images
        
        if not image_paths or image_paths[0].startswith("Error:"):
            return "Error: No se pudieron obtener las imágenes del menú."
        
        # Format response for the assistant
        response = "Imágenes del menú disponibles para análisis visual:\n\n"
        for i, path in enumerate(image_paths, 1):
            response += f"Página {i}: {path}\n"
        
        response += "\nNOTA IMPORTANTE: Estas son imágenes del menú actual. Analiza visualmente el contenido para responder con precisión sobre platos, precios y descripciones tal como aparecen en las imágenes."
        
        return response
        
    except Exception as e:
        error_msg = f"Error en menu reader wrapper: {str(e)}"
        logger.error(f"[MENU_READER] {error_msg}")
        return error_msg
