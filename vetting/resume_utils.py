"""
Resume text extraction utility functions for candidate vetting.

Extracted from CandidateVettingService - these functions don't depend
on class state (only used logging via self, replaced with module-level logger).
"""

import io
import os
import logging
import base64
from typing import Optional

logger = logging.getLogger(__name__)

MIN_TEXT_RATIO = 10


def _detect_file_format(file_content: bytes) -> str:
    """Detect actual file format from magic bytes, regardless of filename extension."""
    if not file_content or len(file_content) < 4:
        return 'unknown'
    if file_content[:4] == b'%PDF':
        return 'pdf'
    if file_content[:2] == b'PK':
        return 'docx'
    if file_content[:4] == b'\xd0\xcf\x11\xe0':
        return 'doc'
    return 'unknown'


from utils.text_sanitization import sanitize_text as _sanitize_text  # re-export for back-compat


def extract_resume_text(file_content: bytes, filename: str) -> Optional[str]:
    """
    Extract text content from a resume file (PDF, DOCX, DOC, TXT).
    Detects actual file format from content bytes and falls back across
    formats when the filename extension doesn't match the real content.
    
    Args:
        file_content: Raw file bytes
        filename: Original filename (for determining file type)
        
    Returns:
        Extracted text or None if extraction fails (NUL-byte sanitized)
    """
    return _sanitize_text(_extract_resume_text_raw(file_content, filename))


def _extract_resume_text_raw(file_content: bytes, filename: str) -> Optional[str]:
    if not file_content:
        return None
    
    filename_lower = filename.lower()
    actual_format = _detect_file_format(file_content)
    
    try:
        if filename_lower.endswith('.pdf'):
            return extract_text_from_pdf(file_content)
        elif filename_lower.endswith('.docx'):
            result = extract_text_from_docx(file_content)
            if result and len(result.strip()) > 10:
                return result
            if actual_format == 'doc':
                logging.info(f"🔄 File '{filename}' is labeled .docx but is actually a legacy .doc — retrying with .doc extractor")
                result = extract_text_from_doc(file_content)
                if result and len(result.strip()) > 10:
                    return result
            if actual_format == 'pdf':
                logging.info(f"🔄 File '{filename}' is labeled .docx but is actually a PDF — retrying with PDF extractor")
                return extract_text_from_pdf(file_content)
            logging.info(f"🔄 DOCX extraction failed for '{filename}' (detected format: {actual_format}) — attempting AI vision OCR")
            return _ocr_document_with_vision(file_content, filename)
        elif filename_lower.endswith('.doc'):
            result = extract_text_from_doc(file_content)
            if result and len(result.strip()) > 10:
                return result
            if actual_format == 'docx':
                logging.info(f"🔄 File '{filename}' is labeled .doc but is actually a .docx — retrying with DOCX extractor")
                result = extract_text_from_docx(file_content)
                if result and len(result.strip()) > 10:
                    return result
            if actual_format == 'pdf':
                logging.info(f"🔄 File '{filename}' is labeled .doc but is actually a PDF — retrying with PDF extractor")
                return extract_text_from_pdf(file_content)
            logging.info(f"🔄 DOC extraction failed for '{filename}' (detected format: {actual_format}) — attempting AI vision OCR")
            return _ocr_document_with_vision(file_content, filename)
        elif filename_lower.endswith('.txt'):
            return file_content.decode('utf-8', errors='ignore')
        else:
            return file_content.decode('utf-8', errors='ignore')
    except Exception as e:
        logging.error(f"Error extracting text from {filename}: {str(e)}")
        return None


def _ocr_document_with_vision(file_content: bytes, filename: str) -> Optional[str]:
    """Last-resort OCR for non-PDF documents by converting to PDF first, then using vision."""
    try:
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp_in:
            tmp_in.write(file_content)
            input_path = tmp_in.name

        output_dir = os.path.dirname(input_path)
        try:
            result = subprocess.run(
                ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', output_dir, input_path],
                capture_output=True, text=True, timeout=60
            )
            pdf_path = os.path.splitext(input_path)[0] + '.pdf'
            if result.returncode == 0 and os.path.exists(pdf_path):
                with open(pdf_path, 'rb') as f:
                    pdf_bytes = f.read()
                os.unlink(pdf_path)
                logging.info(f"📄 Converted '{filename}' to PDF ({len(pdf_bytes)} bytes) for OCR")
                return extract_text_from_pdf(pdf_bytes)
        except FileNotFoundError:
            logging.warning("LibreOffice not available for document conversion")
        except subprocess.TimeoutExpired:
            logging.warning("LibreOffice conversion timed out")
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)

        logging.info(f"📸 Direct AI vision OCR fallback for '{filename}'")
        ocr_text = _ocr_raw_file_with_vision(file_content, filename)
        return ocr_text
    except Exception as e:
        logging.error(f"Document OCR fallback failed for '{filename}': {str(e)}")
        return None


def _ocr_raw_file_with_vision(file_content: bytes, filename: str) -> Optional[str]:
    """Send raw file bytes as an image to AI vision for OCR (last resort for unreadable docs)."""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        b64_content = base64.b64encode(file_content).decode("utf-8")

        ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else 'bin'
        mime_map = {'pdf': 'application/pdf', 'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}
        mime = mime_map.get(ext, 'application/octet-stream')

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an OCR assistant. Extract ALL text from the document provided. "
                               "Reproduce the text exactly as it appears — preserve names, dates, job titles, skills, "
                               "phone numbers, emails, addresses, and formatting structure. Do not summarize or interpret. "
                               "Output only the extracted text, nothing else."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text from this resume document:"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_content}", "detail": "high"}}
                    ]
                }
            ],
            max_completion_tokens=4000
        )

        ocr_text = response.choices[0].message.content.strip() if response.choices else None
        if ocr_text:
            logging.info(f"📸 Raw file vision OCR extracted {len(ocr_text)} chars from '{filename}'")
        return ocr_text
    except Exception as e:
        logging.error(f"Raw file vision OCR failed for '{filename}': {str(e)}")
        return None


def extract_text_from_pdf(file_content: bytes) -> Optional[str]:
    """Extract text from PDF file, with AI vision OCR fallback for scanned/image-based PDFs."""
    try:
        import fitz  # PyMuPDF
        
        content_size = len(file_content) if file_content else 0
        first_bytes = file_content[:50] if file_content and len(file_content) >= 50 else file_content
        logging.info(f"PDF extraction: size={content_size} bytes, starts with: {first_bytes[:20] if first_bytes else 'empty'}")
        
        if not file_content or not file_content.startswith(b'%PDF'):
            logging.error(f"Invalid PDF content - doesn't start with %PDF header. First 100 bytes: {file_content[:100] if file_content else 'empty'}")
            return None
        
        doc = fitz.open(stream=file_content, filetype="pdf")
        text_parts = []
        
        for page in doc:
            text_parts.append(page.get_text())
        
        doc.close()
        extracted_text = "\n".join(text_parts).strip()
        logging.info(f"PDF extraction successful: {len(extracted_text)} chars extracted")
        
        if len(extracted_text) < 50 and content_size > 5000:
            logging.info(f"🔍 Image-based PDF detected ({len(extracted_text)} chars from {content_size} byte file) — attempting AI vision OCR")
            ocr_text = _ocr_pdf_with_vision(file_content)
            if ocr_text and len(ocr_text) > len(extracted_text):
                logging.info(f"📸 AI vision OCR successful: {len(ocr_text)} chars extracted from image-based PDF")
                return ocr_text
            else:
                logging.warning(f"AI vision OCR did not improve extraction (got {len(ocr_text) if ocr_text else 0} chars)")
        
        return extracted_text
    except ImportError:
        logging.warning("PyMuPDF not installed - trying pdfminer")
        try:
            from pdfminer.high_level import extract_text
            return extract_text(io.BytesIO(file_content))
        except ImportError:
            logging.error("No PDF extraction library available")
            return None
    except Exception as e:
        logging.error(f"PDF extraction error: {str(e)}")
        if file_content:
            logging.error(f"PDF content size: {len(file_content)} bytes, first 50 bytes: {file_content[:50]}")
        return None


def _ocr_pdf_with_vision(file_content: bytes, max_pages: int = 5) -> Optional[str]:
    """Use AI vision to extract text from image-based/scanned PDF pages."""
    try:
        import fitz
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        doc = fitz.open(stream=file_content, filetype="pdf")
        page_count = min(len(doc), max_pages)

        image_messages = []
        for i in range(page_count):
            page = doc[i]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            image_messages.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64_img}", "detail": "high"}
            })

        doc.close()

        if not image_messages:
            return None

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an OCR assistant. Extract ALL text from the resume page image(s) provided. "
                               "Reproduce the text exactly as it appears — preserve names, dates, job titles, skills, "
                               "phone numbers, emails, addresses, and formatting structure. Do not summarize or interpret. "
                               "Output only the extracted text, nothing else."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Extract all text from these {page_count} resume page(s):"},
                        *image_messages
                    ]
                }
            ],
            max_completion_tokens=4000
        )

        ocr_text = response.choices[0].message.content.strip() if response.choices else None
        if ocr_text:
            logging.info(f"📸 Vision OCR extracted {len(ocr_text)} chars from {page_count} page(s)")
        return ocr_text

    except Exception as e:
        logging.error(f"Vision OCR failed: {str(e)}")
        return None


def extract_text_from_docx(file_content: bytes) -> Optional[str]:
    """Extract text from DOCX file"""
    try:
        from docx import Document
        
        doc = Document(io.BytesIO(file_content))
        text_parts = []
        
        for para in doc.paragraphs:
            text_parts.append(para.text)
        
        return "\n".join(text_parts)
    except ImportError:
        logging.error("python-docx not installed for DOCX extraction")
        return None
    except Exception as e:
        logging.error(f"DOCX extraction error: {str(e)}")
        return None


def extract_text_from_doc(file_content: bytes) -> Optional[str]:
    """Extract text from legacy DOC file"""
    try:
        import subprocess
        import tempfile
        import os
        
        # Write to temp file and use antiword or similar
        with tempfile.NamedTemporaryFile(suffix='.doc', delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        
        try:
            # Try antiword
            result = subprocess.run(['antiword', tmp_path], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout
        except FileNotFoundError:
            logging.warning("antiword not available for DOC extraction")
        finally:
            os.unlink(tmp_path)
        
        return None
    except Exception as e:
        logging.error(f"DOC extraction error: {str(e)}")
        return None
