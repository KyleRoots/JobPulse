"""
Resume Parsing Service
Simple resume text extraction and parsing for basic candidate information
"""
import logging
import re
import tempfile
import os
from typing import Dict, Optional, Union
from werkzeug.datastructures import FileStorage

# PDF parsing
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    logging.warning("PyPDF2 not available - PDF parsing disabled")

# DOC/DOCX parsing
try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logging.warning("python-docx not available - DOCX parsing disabled")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResumeParser:
    """Simple resume parser for extracting basic candidate information"""
    
    def __init__(self):
        self.patterns = {
            'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'phone': r'(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
            'name': r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',  # Names at start of lines
        }
    
    def parse_resume(self, file: Union[FileStorage, str]) -> Dict[str, any]:
        """
        Parse resume file and extract basic information
        
        Args:
            file: FileStorage object or file path
            
        Returns:
            Dict with parsed information and success status
        """
        try:
            # Extract text from file
            text = self._extract_text_from_file(file)
            
            if not text:
                return {
                    'success': False,
                    'error': 'Could not extract text from resume file',
                    'parsed_data': {}
                }
            
            # Parse extracted text
            parsed_data = self._parse_text(text)
            
            return {
                'success': True,
                'parsed_data': parsed_data,
                'raw_text': text[:500]  # First 500 chars for debugging
            }
            
        except Exception as e:
            logger.error(f"Error parsing resume: {str(e)}")
            return {
                'success': False,
                'error': f'Error parsing resume: {str(e)}',
                'parsed_data': {}
            }
    
    def _extract_text_from_file(self, file: Union[FileStorage, str]) -> str:
        """Extract text content from uploaded file"""
        
        if isinstance(file, str):
            # File path provided
            file_path = file
            filename = os.path.basename(file_path)
        else:
            # FileStorage object - save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
                file.save(temp_file.name)
                file_path = temp_file.name
                filename = file.filename
        
        try:
            # Determine file type and extract text
            if filename.lower().endswith('.pdf'):
                return self._extract_pdf_text(file_path)
            elif filename.lower().endswith('.docx'):
                return self._extract_docx_text(file_path)
            elif filename.lower().endswith('.doc'):
                return self._extract_doc_text(file_path)
            else:
                raise ValueError(f"Unsupported file type: {filename}")
                
        finally:
            # Clean up temporary file if created
            if not isinstance(file, str) and os.path.exists(file_path):
                os.unlink(file_path)
    
    def _extract_pdf_text(self, file_path: str) -> str:
        """Extract text from PDF file"""
        if not PDF_AVAILABLE:
            raise ValueError("PDF parsing not available - PyPDF2 not installed")
        
        text = ""
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
        except Exception as e:
            logger.error(f"Error extracting PDF text: {str(e)}")
            raise ValueError(f"Could not extract text from PDF: {str(e)}")
        
        return text.strip()
    
    def _extract_docx_text(self, file_path: str) -> str:
        """Extract text from DOCX file"""
        if not DOCX_AVAILABLE:
            raise ValueError("DOCX parsing not available - python-docx not installed")
        
        try:
            doc = Document(file_path)
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text.strip()
        except Exception as e:
            logger.error(f"Error extracting DOCX text: {str(e)}")
            raise ValueError(f"Could not extract text from DOCX: {str(e)}")
    
    def _extract_doc_text(self, file_path: str) -> str:
        """Extract text from DOC file (legacy format)"""
        # For simplicity, we'll suggest users convert to PDF or DOCX
        raise ValueError("Legacy DOC format not supported. Please convert to PDF or DOCX format.")
    
    def _parse_text(self, text: str) -> Dict[str, Optional[str]]:
        """Parse extracted text to find candidate information"""
        
        # Clean up text
        text = text.replace('\n', ' ').replace('\r', ' ')
        text = ' '.join(text.split())  # Normalize whitespace
        
        parsed_data = {
            'first_name': None,
            'last_name': None,
            'email': None,
            'phone': None
        }
        
        # Extract email
        email_match = re.search(self.patterns['email'], text, re.IGNORECASE)
        if email_match:
            parsed_data['email'] = email_match.group().lower()
        
        # Extract phone number
        phone_match = re.search(self.patterns['phone'], text)
        if phone_match:
            # Format phone number consistently
            phone = f"({phone_match.group(1)}) {phone_match.group(2)}-{phone_match.group(3)}"
            parsed_data['phone'] = phone
        
        # Extract name (simple approach - look for capitalized words at start)
        lines = text.split('\n')
        potential_names = []
        
        for line in lines[:10]:  # Check first 10 lines
            line = line.strip()
            if len(line) > 0 and len(line) < 50:  # Reasonable name length
                # Look for lines with 2-3 capitalized words
                words = line.split()
                if 2 <= len(words) <= 3:
                    if all(word[0].isupper() and word[1:].islower() for word in words if word.isalpha()):
                        potential_names.append(words)
        
        # Use the first reasonable name found
        if potential_names:
            name_parts = potential_names[0]
            if len(name_parts) >= 2:
                parsed_data['first_name'] = name_parts[0]
                parsed_data['last_name'] = name_parts[-1]  # Last word as last name
        
        # Alternative name extraction from email if name not found
        if not parsed_data['first_name'] and parsed_data['email']:
            email_part = parsed_data['email'].split('@')[0]
            # Look for common email patterns like firstname.lastname
            if '.' in email_part:
                email_parts = email_part.split('.')
                if len(email_parts) == 2:
                    parsed_data['first_name'] = email_parts[0].capitalize()
                    parsed_data['last_name'] = email_parts[1].capitalize()
        
        # Log what was found
        found_items = [k for k, v in parsed_data.items() if v]
        logger.info(f"Resume parsing found: {', '.join(found_items)}")
        
        return parsed_data