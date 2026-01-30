"""
Resume Parsing Service
Resume text extraction with HTML formatting preservation for Bullhorn description field
Uses GPT-4o for intelligent PDF formatting when regex-based detection fails
"""
import logging
import re
import tempfile
import os
import html
import json
from typing import Dict, Optional, Union
from werkzeug.datastructures import FileStorage

# OpenAI for AI-assisted formatting
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logging.warning("OpenAI not available - AI-assisted PDF formatting disabled")

try:
    import fitz  # PyMuPDF - better at preserving spaces in text extraction
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logging.warning("PyMuPDF not available - falling back to PyPDF2")

try:
    import PyPDF2
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False
    logging.warning("PyPDF2 not available")

PDF_AVAILABLE = PYMUPDF_AVAILABLE or PYPDF2_AVAILABLE
if not PDF_AVAILABLE:
    logging.warning("No PDF parsing library available - PDF parsing disabled")

try:
    from docx import Document
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logging.warning("python-docx not available - DOCX parsing disabled")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ResumeParser:
    """Resume parser with HTML formatting preservation for Bullhorn"""
    
    HEADING_KEYWORDS = [
        'experience', 'education', 'skills', 'summary', 'objective',
        'professional summary', 'work history', 'employment', 'qualifications',
        'certifications', 'projects', 'achievements', 'accomplishments',
        'technical skills', 'core competencies', 'professional experience',
        'work experience', 'career summary', 'profile', 'about me',
        'languages', 'references', 'awards', 'publications', 'training',
        'licenses', 'affiliations', 'volunteer', 'interests', 'hobbies'
    ]
    
    def __init__(self):
        self.patterns = {
            'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'phone': r'(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
            'name': r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
        }
        self.openai_client = None
        self._init_openai()
    
    def _init_openai(self):
        """Initialize OpenAI client for AI-assisted formatting"""
        if not OPENAI_AVAILABLE:
            return
        
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
            logger.info("OpenAI client initialized for AI resume formatting")
        else:
            logger.warning("OPENAI_API_KEY not set - AI-assisted PDF formatting disabled")
    
    def _normalize_pdf_text(self, text: str) -> str:
        """
        Normalize PDF text to fix common extraction issues:
        - Normalize Unicode whitespace characters
        - Fix non-breaking spaces
        - Clean up ligatures
        - Add spaces around common word boundaries (camelCase transitions)
        
        This is a deterministic pre-processing step before AI or regex formatting.
        """
        if not text:
            return text
        
        # Normalize various Unicode whitespace to regular space
        import unicodedata
        normalized = unicodedata.normalize('NFKC', text)
        
        # Replace non-breaking spaces and other whitespace variants
        whitespace_chars = [
            '\u00A0',  # Non-breaking space
            '\u2002',  # En space
            '\u2003',  # Em space
            '\u2009',  # Thin space
            '\u200A',  # Hair space
            '\u200B',  # Zero-width space
            '\u3000',  # Ideographic space
        ]
        for ws in whitespace_chars:
            normalized = normalized.replace(ws, ' ')
        
        # Detect if text has obvious concatenation issues
        # (very few spaces relative to text length)
        space_ratio = normalized.count(' ') / max(len(normalized), 1)
        
        if space_ratio < 0.08:  # Less than 8% spaces is very suspicious
            logger.warning(f"Detected concatenated text (space ratio: {space_ratio:.1%}), attempting repair")
            
            # Add spaces at common word boundaries:
            # - Lowercase to uppercase transitions (camelCase)
            # - Before common section keywords in all caps
            
            # Add space before uppercase after lowercase: "experienceEducation" -> "experience Education"
            normalized = re.sub(r'([a-z])([A-Z])', r'\1 \2', normalized)
            
            # Add space before common resume section words
            section_words = [
                'SUMMARY', 'EXPERIENCE', 'EDUCATION', 'SKILLS', 'OBJECTIVE',
                'QUALIFICATIONS', 'CERTIFICATIONS', 'PROJECTS', 'ACHIEVEMENTS',
                'REFERENCES', 'AWARDS', 'LANGUAGES', 'Summary', 'Experience',
                'Education', 'Skills', 'Objective'
            ]
            for word in section_words:
                # Add space before section word if preceded by lowercase letter
                normalized = re.sub(rf'([a-z])({word})', rf'\1 \2', normalized)
            
            # Add space after period followed by uppercase: "company.SKILLS" -> "company. SKILLS"
            normalized = re.sub(r'\.([A-Z])', r'. \1', normalized)
        
        # Clean up multiple consecutive spaces
        normalized = re.sub(r' +', ' ', normalized)
        
        return normalized
    
    def _format_pdf_with_ai(self, raw_text: str) -> Optional[str]:
        """
        Use GPT-4o to intelligently format raw PDF text into clean HTML.
        
        This handles the structural ambiguity of PDFs where text extraction
        loses all formatting information (headings, bullets, paragraphs).
        
        Args:
            raw_text: Raw extracted text from PDF
            
        Returns:
            Clean HTML-formatted version or None if AI unavailable
        """
        if not self.openai_client:
            logger.info("OpenAI not available, falling back to regex-based formatting")
            return None
        
        if not raw_text or len(raw_text.strip()) < 50:
            return None
        
        max_text_len = 8000
        truncated_text = raw_text[:max_text_len] if len(raw_text) > max_text_len else raw_text
        
        prompt = f"""Convert this raw resume text into clean, well-structured HTML for display in a web interface.

IMPORTANT RULES:
1. Preserve ALL information from the original text - don't summarize or omit details
2. If words are concatenated together (like "PROFESSIONALSUMMARYAnITprofessional"), ADD SPACES between words to make the text readable (becomes "PROFESSIONAL SUMMARY An IT professional")
3. Identify and wrap section headings (like "Experience", "Education", "Skills") in <h4><strong>...</strong></h4> tags
4. Convert bullet points (including symbols like •, ▢, -, *) into proper <ul><li>...</li></ul> lists
5. Wrap job entries with company names and dates in <p><strong>...</strong></p>
6. Group related text into proper <p>...</p> paragraphs
7. Add spacing between sections for readability
8. Handle contact info at the top cleanly (name, email, phone, location, links)
9. Don't add any content that isn't in the original - only format what's there
10. Use semantic HTML only - no inline styles or classes

RAW RESUME TEXT:
{truncated_text}

OUTPUT: Return ONLY the formatted HTML, nothing else. No explanation, no markdown code blocks."""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a resume formatting expert. Your job is to convert raw, unstructured resume text into clean, readable HTML while preserving all original content exactly."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=4000,
                timeout=30.0
            )
            
            formatted_html = response.choices[0].message.content.strip()
            
            if formatted_html.startswith('```html'):
                formatted_html = formatted_html[7:]
            if formatted_html.startswith('```'):
                formatted_html = formatted_html[3:]
            if formatted_html.endswith('```'):
                formatted_html = formatted_html[:-3]
            formatted_html = formatted_html.strip()
            
            logger.info(f"AI-formatted PDF resume ({len(raw_text)} chars -> {len(formatted_html)} chars HTML)")
            return formatted_html
            
        except Exception as e:
            logger.error(f"AI formatting failed: {str(e)}")
            return None
    
    def parse_resume(self, file: Union[FileStorage, str]) -> Dict[str, any]:
        """
        Parse resume file and extract information with HTML formatting
        
        Returns:
            Dict with parsed_data, raw_text (plain text), and formatted_html
        """
        try:
            if isinstance(file, str):
                file_path = file
                filename = os.path.basename(file_path)
                temp_created = False
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
                    file.save(temp_file.name)
                    file_path = temp_file.name
                    filename = file.filename
                    temp_created = True
            
            try:
                if filename.lower().endswith('.pdf'):
                    raw_text, formatted_html = self._extract_pdf_with_formatting(file_path)
                elif filename.lower().endswith('.docx'):
                    raw_text, formatted_html = self._extract_docx_with_formatting(file_path)
                elif filename.lower().endswith('.doc'):
                    raw_text = ""
                    formatted_html = ""
                    logger.info("Legacy DOC format not supported - allowing manual entry")
                else:
                    raw_text = ""
                    formatted_html = ""
                    logger.warning(f"Unsupported file type: {filename}")
            finally:
                if temp_created and os.path.exists(file_path):
                    os.unlink(file_path)
            
            if not raw_text:
                return {
                    'success': True,
                    'parsed_data': {
                        'first_name': None,
                        'last_name': None,
                        'email': None,
                        'phone': None
                    },
                    'raw_text': '',
                    'formatted_html': ''
                }
            
            parsed_data = self._parse_text(raw_text)
            
            return {
                'success': True,
                'parsed_data': parsed_data,
                'raw_text': raw_text,
                'formatted_html': formatted_html
            }
            
        except Exception as e:
            logger.error(f"Error parsing resume: {str(e)}")
            return {
                'success': False,
                'error': f'Error parsing resume: {str(e)}',
                'parsed_data': {},
                'raw_text': '',
                'formatted_html': ''
            }
    
    def _extract_docx_with_formatting(self, file_path: str) -> tuple:
        """Extract text from DOCX with HTML formatting preserved"""
        if not DOCX_AVAILABLE:
            logger.warning("DOCX parsing not available")
            return "", ""
        
        try:
            doc = Document(file_path)
            raw_lines = []
            html_parts = []
            in_list = False
            
            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    continue
                
                raw_lines.append(text)
                escaped_text = html.escape(text)
                
                style_name = para.style.name.lower() if para.style else ''
                is_heading = (
                    'heading' in style_name or
                    'title' in style_name or
                    self._is_likely_heading(text)
                )
                
                is_bullet = (
                    text.startswith('•') or
                    text.startswith('-') or
                    text.startswith('*') or
                    'list' in style_name
                )
                
                if is_heading:
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    
                    if 'heading 1' in style_name or 'title' in style_name:
                        html_parts.append(f'<h2>{escaped_text}</h2>')
                    elif 'heading 2' in style_name:
                        html_parts.append(f'<h3>{escaped_text}</h3>')
                    else:
                        html_parts.append(f'<h4><strong>{escaped_text}</strong></h4>')
                
                elif is_bullet:
                    clean_text = re.sub(r'^[•\-\*]\s*', '', escaped_text)
                    if not in_list:
                        html_parts.append('<ul>')
                        in_list = True
                    html_parts.append(f'<li>{clean_text}</li>')
                
                else:
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    
                    formatted_text = self._apply_inline_formatting(para, escaped_text)
                    html_parts.append(f'<p>{formatted_text}</p>')
            
            if in_list:
                html_parts.append('</ul>')
            
            raw_text = '\n'.join(raw_lines)
            formatted_html = '\n'.join(html_parts)
            
            return raw_text, formatted_html
            
        except Exception as e:
            logger.warning(f"Could not extract DOCX with formatting: {str(e)}")
            return "", ""
    
    def _apply_inline_formatting(self, para, escaped_text: str) -> str:
        """Apply bold/italic formatting from paragraph runs"""
        try:
            if not para.runs:
                return escaped_text
            
            formatted_parts = []
            for run in para.runs:
                run_text = html.escape(run.text) if run.text else ''
                if not run_text:
                    continue
                
                if run.bold and run.italic:
                    formatted_parts.append(f'<strong><em>{run_text}</em></strong>')
                elif run.bold:
                    formatted_parts.append(f'<strong>{run_text}</strong>')
                elif run.italic:
                    formatted_parts.append(f'<em>{run_text}</em>')
                else:
                    formatted_parts.append(run_text)
            
            return ''.join(formatted_parts) if formatted_parts else escaped_text
        except Exception:
            return escaped_text
    
    def _extract_pdf_with_formatting(self, file_path: str) -> tuple:
        """Extract text from PDF with AI-assisted HTML formatting
        
        Uses PyMuPDF (fitz) for better text extraction with proper spacing,
        then GPT-4o to intelligently format the raw PDF text into clean HTML,
        falling back to regex-based heuristics if AI is unavailable.
        """
        if not PDF_AVAILABLE:
            logger.warning("PDF parsing not available")
            return "", ""
        
        try:
            raw_text = ""
            
            # Try PyMuPDF first (better at preserving spaces)
            if PYMUPDF_AVAILABLE:
                try:
                    doc = fitz.open(file_path)
                    text_parts = []
                    for page in doc:
                        # Use get_text with "text" option for best spacing
                        page_text = page.get_text("text")
                        if page_text:
                            text_parts.append(page_text)
                    doc.close()
                    raw_text = '\n'.join(text_parts)
                    logger.info(f"Extracted PDF with PyMuPDF ({len(raw_text)} chars)")
                except Exception as e:
                    logger.warning(f"PyMuPDF extraction failed: {str(e)}, falling back to PyPDF2")
                    raw_text = ""
            
            # Fall back to PyPDF2 if PyMuPDF failed
            if not raw_text and PYPDF2_AVAILABLE:
                try:
                    raw_lines = []
                    with open(file_path, 'rb') as file:
                        pdf_reader = PyPDF2.PdfReader(file)
                        for page in pdf_reader.pages:
                            page_text = page.extract_text()
                            if page_text:
                                lines = page_text.split('\n')
                                raw_lines.extend(lines)
                    raw_text = '\n'.join(raw_lines)
                    logger.info(f"Extracted PDF with PyPDF2 ({len(raw_text)} chars)")
                except Exception as e:
                    logger.warning(f"PyPDF2 extraction failed: {str(e)}")
                    raw_text = ""
            
            if not raw_text:
                return "", ""
            
            # Apply deterministic text normalization to fix common PDF extraction issues
            # This handles Unicode whitespace, non-breaking spaces, and attempts to repair
            # concatenated text by detecting camelCase transitions
            raw_text = self._normalize_pdf_text(raw_text)
            logger.info(f"Normalized PDF text ({len(raw_text)} chars after normalization)")
            
            # Use AI formatting which can help with spacing issues too
            ai_formatted = self._format_pdf_with_ai(raw_text)
            if ai_formatted:
                logger.info("Using AI-formatted HTML for PDF resume")
                return raw_text, ai_formatted
            
            logger.info("Falling back to regex-based PDF formatting")
            raw_lines = raw_text.split('\n')
            formatted_html = self._convert_pdf_lines_to_html(raw_lines)
            
            return raw_text, formatted_html
            
        except Exception as e:
            logger.warning(f"Could not extract PDF with formatting: {str(e)}")
            return "", ""
    
    def _convert_pdf_lines_to_html(self, lines: list) -> str:
        """Convert PDF text lines to HTML with detected formatting"""
        html_parts = []
        in_list = False
        paragraph_buffer = []
        
        for line in lines:
            line = line.strip()
            if not line:
                if paragraph_buffer:
                    html_parts.append(f'<p>{" ".join(paragraph_buffer)}</p>')
                    paragraph_buffer = []
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                continue
            
            escaped_line = html.escape(line)
            
            if self._is_likely_heading(line):
                if paragraph_buffer:
                    html_parts.append(f'<p>{" ".join(paragraph_buffer)}</p>')
                    paragraph_buffer = []
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                html_parts.append(f'<h4><strong>{escaped_line}</strong></h4>')
                continue
            
            is_bullet = (
                line.startswith('•') or
                line.startswith('-') or
                line.startswith('*') or
                re.match(r'^\d+[\.\)]\s', line)
            )
            
            if is_bullet:
                if paragraph_buffer:
                    html_parts.append(f'<p>{" ".join(paragraph_buffer)}</p>')
                    paragraph_buffer = []
                
                clean_text = re.sub(r'^[•\-\*]\s*', '', escaped_line)
                clean_text = re.sub(r'^\d+[\.\)]\s*', '', clean_text)
                
                if not in_list:
                    html_parts.append('<ul>')
                    in_list = True
                html_parts.append(f'<li>{clean_text}</li>')
                continue
            
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            
            if len(line) < 60 and not line.endswith(('.', ',', ';', ':')):
                if paragraph_buffer:
                    html_parts.append(f'<p>{" ".join(paragraph_buffer)}</p>')
                    paragraph_buffer = []
                html_parts.append(f'<p>{escaped_line}</p>')
            else:
                paragraph_buffer.append(escaped_line)
        
        if paragraph_buffer:
            html_parts.append(f'<p>{" ".join(paragraph_buffer)}</p>')
        if in_list:
            html_parts.append('</ul>')
        
        return '\n'.join(html_parts)
    
    def _is_likely_heading(self, text: str) -> bool:
        """Detect if text is likely a section heading"""
        text_lower = text.lower().strip()
        
        if len(text) > 50:
            return False
        
        if text.isupper() and len(text) > 3 and len(text) < 40:
            return True
        
        for keyword in self.HEADING_KEYWORDS:
            if text_lower == keyword or text_lower.startswith(keyword + ':'):
                return True
            if text_lower.endswith(keyword) and len(text_lower) < 30:
                return True
        
        if text.endswith(':') and len(text) < 30:
            return True
        
        return False
    
    def _parse_text(self, text: str) -> Dict[str, Optional[str]]:
        """Parse extracted text to find candidate information"""
        original_text = text
        normalized_text = ' '.join(text.split())
        
        parsed_data = {
            'first_name': None,
            'last_name': None,
            'email': None,
            'phone': None
        }
        
        email_match = re.search(self.patterns['email'], normalized_text, re.IGNORECASE)
        if email_match:
            parsed_data['email'] = email_match.group().lower()
        
        phone_patterns = [
            r'(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
            r'(\d{3})[-.\s](\d{3})[-.\s](\d{4})',
            r'\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})'
        ]
        
        for pattern in phone_patterns:
            phone_match = re.search(pattern, normalized_text)
            if phone_match:
                phone = f"({phone_match.group(1)}) {phone_match.group(2)}-{phone_match.group(3)}"
                parsed_data['phone'] = phone
                break
        
        name_found = False
        
        name_with_contact = r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*\([^)]*(?:@|phone|\d{3}[-.\s]?\d{3}[-.\s]?\d{4})[^)]*\)'
        name_match = re.search(name_with_contact, original_text, re.IGNORECASE | re.MULTILINE)
        if name_match:
            name_text = name_match.group(1).strip()
            name_parts = name_text.split()
            if len(name_parts) >= 2:
                parsed_data['first_name'] = name_parts[0]
                parsed_data['last_name'] = name_parts[-1]
                name_found = True
        
        if not name_found:
            lines = original_text.split('\n')
            for line in lines[:5]:
                line = line.strip()
                if len(line) > 50 or len(line) < 3:
                    continue
                
                skip_words = ['resume', 'cv', 'curriculum', 'vitae', 'profile', 'contact', 'phone', 'email', 'address']
                if any(skip_word in line.lower() for skip_word in skip_words):
                    continue
                
                words = line.split()
                if 2 <= len(words) <= 3:
                    if all(word[0].isupper() and word.replace('-', '').replace("'", "").isalpha() for word in words):
                        parsed_data['first_name'] = words[0]
                        parsed_data['last_name'] = words[-1]
                        name_found = True
                        break
        
        if not name_found and parsed_data.get('email'):
            email_part = parsed_data['email'].split('@')[0]
            if '.' in email_part:
                email_parts = email_part.split('.')
                if len(email_parts) == 2 and all(part.isalpha() for part in email_parts):
                    parsed_data['first_name'] = email_parts[0].capitalize()
                    parsed_data['last_name'] = email_parts[1].capitalize()
                    name_found = True
            elif len(email_part) > 3 and email_part.isalpha():
                camel_split = re.findall(r'[A-Z][a-z]*', email_part.capitalize())
                if len(camel_split) >= 2:
                    parsed_data['first_name'] = camel_split[0]
                    parsed_data['last_name'] = camel_split[-1]
                    name_found = True
        
        found_items = [k for k, v in parsed_data.items() if v]
        logger.info(f"Resume parsing found: {', '.join(found_items)}")
        
        return parsed_data
    
    def _extract_text_from_file(self, file: Union[FileStorage, str]) -> str:
        """Legacy method for backwards compatibility - returns plain text only"""
        result = self.parse_resume(file)
        return result.get('raw_text', '')
