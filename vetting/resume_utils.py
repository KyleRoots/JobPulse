"""
Resume text extraction utility functions for candidate vetting.

Extracted from CandidateVettingService - these functions don't depend
on class state (only used logging via self, replaced with module-level logger).
"""

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_resume_text(file_content: bytes, filename: str) -> Optional[str]:
    """
    Extract text content from a resume file (PDF, DOCX, DOC, TXT).
    
    Args:
        file_content: Raw file bytes
        filename: Original filename (for determining file type)
        
    Returns:
        Extracted text or None if extraction fails
    """
    if not file_content:
        return None
    
    filename_lower = filename.lower()
    
    try:
        if filename_lower.endswith('.pdf'):
            return extract_text_from_pdf(file_content)
        elif filename_lower.endswith('.docx'):
            return extract_text_from_docx(file_content)
        elif filename_lower.endswith('.doc'):
            return extract_text_from_doc(file_content)
        elif filename_lower.endswith('.txt'):
            return file_content.decode('utf-8', errors='ignore')
        else:
            # Try to decode as text
            return file_content.decode('utf-8', errors='ignore')
    except Exception as e:
        logging.error(f"Error extracting text from {filename}: {str(e)}")
        return None


def extract_text_from_pdf(file_content: bytes) -> Optional[str]:
    """Extract text from PDF file"""
    try:
        import fitz  # PyMuPDF
        
        # Debug: Check content size and first bytes
        content_size = len(file_content) if file_content else 0
        first_bytes = file_content[:50] if file_content and len(file_content) >= 50 else file_content
        logging.info(f"PDF extraction: size={content_size} bytes, starts with: {first_bytes[:20] if first_bytes else 'empty'}")
        
        # Check if content starts with %PDF (valid PDF header)
        if not file_content or not file_content.startswith(b'%PDF'):
            logging.error(f"Invalid PDF content - doesn't start with %PDF header. First 100 bytes: {file_content[:100] if file_content else 'empty'}")
            return None
        
        doc = fitz.open(stream=file_content, filetype="pdf")
        text_parts = []
        
        for page in doc:
            text_parts.append(page.get_text())
        
        doc.close()
        extracted_text = "\n".join(text_parts)
        logging.info(f"PDF extraction successful: {len(extracted_text)} chars extracted")
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
        # Additional debug for the specific error
        if file_content:
            logging.error(f"PDF content size: {len(file_content)} bytes, first 50 bytes: {file_content[:50]}")
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
