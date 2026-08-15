from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pypdf import PdfReader


@dataclass
class OCRResult:
    text: str
    method: str
    preview: bytes | None = None
    warning: str = ""


def _configure_tesseract() -> None:
    import pytesseract

    configured = os.getenv("TESSERACT_CMD", "").strip()
    common = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if configured:
        pytesseract.pytesseract.tesseract_cmd = configured
    elif os.name == "nt" and common.exists():
        pytesseract.pytesseract.tesseract_cmd = str(common)


def preprocess_image(data: bytes, max_dimension: int = 2400) -> Image.Image:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    try:
        import cv2
        import numpy as np

        array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        array = cv2.fastNlMeansDenoising(array, None, 8, 7, 21)
        array = cv2.adaptiveThreshold(array, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
        return Image.fromarray(array)
    except ImportError:
        gray = ImageOps.grayscale(image)
        gray = ImageEnhance.Contrast(gray).enhance(1.8)
        return gray.filter(ImageFilter.SHARPEN)


def ocr_image(data: bytes) -> OCRResult:
    try:
        import pytesseract

        _configure_tesseract()
        image = preprocess_image(data)
        text = pytesseract.image_to_string(image, config="--oem 3 --psm 6", timeout=25)
        return OCRResult(text=text, method="Tesseract OCR", preview=data)
    except Exception as exc:  # OCR failures must become reviewable, not fatal.
        return OCRResult(text="", method="OCR failed", preview=data, warning=str(exc))


def _pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _render_pdf_pages(data: bytes, dpi: int = 180) -> list[bytes]:
    import fitz

    document = fitz.open(stream=data, filetype="pdf")
    pages: list[bytes] = []
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    for page in document:
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
        pages.append(pix.tobytes("png"))
    return pages


def extract_document(data: bytes, filename: str) -> OCRResult:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        try:
            text = _pdf_text(data)
            if len(text) >= 80:
                return OCRResult(text=text, method="Direct PDF text")
        except Exception:
            pass
        try:
            pages = _render_pdf_pages(data)
            page_results = [ocr_image(page) for page in pages]
            warnings = "; ".join(r.warning for r in page_results if r.warning)
            return OCRResult(
                text="\n".join(r.text for r in page_results),
                method="Scanned PDF + Tesseract OCR",
                preview=pages[0] if pages else None,
                warning=warnings,
            )
        except Exception as exc:
            return OCRResult("", "PDF read failed", warning=str(exc))
    return ocr_image(data)
