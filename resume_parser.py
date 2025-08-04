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
        
        # Extract phone number with multiple patterns
        phone_patterns = [
            r'(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',  # Standard US format
            r'(\d{3})[-.\s](\d{3})[-.\s](\d{4})',  # Simple format
            r'\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})'  # Flexible format
        ]
        
        for pattern in phone_patterns:
            phone_match = re.search(pattern, text)
            if phone_match:
                # Format phone number consistently
                phone = f"({phone_match.group(1)}) {phone_match.group(2)}-{phone_match.group(3)}"
                parsed_data['phone'] = phone
                break
        
        # Extract name using multiple strategies
        name_found = False
        
        # Strategy 1: Look for "FirstName LastName (email | phone)" pattern
        name_with_contact_pattern = r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*\([^)]*(?:@|phone|\d{3}[-.\s]?\d{3}[-.\s]?\d{4})[^)]*\)'
        name_match = re.search(name_with_contact_pattern, text, re.IGNORECASE | re.MULTILINE)
        if name_match:
            name_text = name_match.group(1).strip()
            name_parts = name_text.split()
            if len(name_parts) >= 2:
                parsed_data['first_name'] = name_parts[0]
                parsed_data['last_name'] = name_parts[-1]
                name_found = True
        
        # Strategy 2: Look at first few lines for name patterns (if strategy 1 didn't work)
        if not name_found:
            lines = text.split('\n')
            for line in lines[:5]:  # Check first 5 lines
                line = line.strip()
                
                # Skip lines that are too long (likely not just a name)
                if len(line) > 50 or len(line) < 3:
                    continue
                
                # Skip lines with common header words
                skip_words = ['resume', 'cv', 'curriculum', 'vitae', 'profile', 'contact', 'phone', 'email', 'address']
                if any(skip_word in line.lower() for skip_word in skip_words):
                    continue
                
                # Look for 2-3 capitalized words (potential name)
                words = line.split()
                if 2 <= len(words) <= 3:
                    # Check if all words start with capital and are alphabetic
                    if all(word[0].isupper() and word.replace('-', '').replace("'", "").isalpha() for word in words):
                        parsed_data['first_name'] = words[0]
                        parsed_data['last_name'] = words[-1]
                        name_found = True
                        break
        
        # Strategy 3: Extract from email if name still not found
        if not name_found and parsed_data.get('email'):
            email_part = parsed_data['email'].split('@')[0]
            # Common email patterns like firstname.lastname or firstnamelastname
            if '.' in email_part:
                email_parts = email_part.split('.')
                if len(email_parts) == 2 and all(part.isalpha() for part in email_parts):
                    parsed_data['first_name'] = email_parts[0].capitalize()
                    parsed_data['last_name'] = email_parts[1].capitalize()
                    name_found = True
            elif len(email_part) > 3 and email_part.isalpha():
                # Try to split camelCase or combined names
                import re
                camel_split = re.findall(r'[A-Z][a-z]*', email_part.capitalize())
                if len(camel_split) >= 2:
                    parsed_data['first_name'] = camel_split[0]
                    parsed_data['last_name'] = camel_split[-1]
                    name_found = True
        
# Name extraction strategies are now handled above
        
        # Log what was found
        found_items = [k for k, v in parsed_data.items() if v]
        logger.info(f"Resume parsing found: {', '.join(found_items)}")
        
        # Log the actual values for debugging (first few chars only for privacy)
        debug_info = {}
        for key, value in parsed_data.items():
            if value:
                if key == 'email':
                    debug_info[key] = f"{value[:3]}***@{value.split('@')[1] if '@' in value else '***'}"
                elif key == 'phone':
                    debug_info[key] = f"{value[:6]}***"
                else:
                    debug_info[key] = f"{value[:3]}***" if len(value) > 3 else value
        logger.info(f"Resume parsing values: {debug_info}")
        
        return parsed_data