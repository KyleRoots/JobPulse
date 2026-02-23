#!/usr/bin/env python3
"""
Proof of Concept: Hybrid Years-of-Experience Calculation
Tests GPT's ability to extract structured dates from diverse resume formats,
then calculates years in Python and compares against GPT's own estimates.
"""

import json
import os
import sys
from datetime import date
from openai import OpenAI

# Load API key
from dotenv import load_dotenv
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─── TEST CASES: 15 diverse resume format variations ───
# Each has: resume_text, skill_to_check, required_years, expected_months (ground truth)

TEST_CASES = [
    # 1. Standard format with "Current"
    {
        "name": "Standard_Current",
        "resume": """Data Scientist | OpenAI, USA | Jan 2024 – Current
- Built RAG systems, LLM chatbots, fine-tuned open-source models
Data Scientist | Accenture, India | Jul 2021 – Aug 2023
- Built ML models, NLP pipelines, REST APIs""",
        "skill": "Data Science",
        "required_years": 5,
        "expected_months": 51,  # 25 + 26
        "description": "Standard format, 'Current' end date"
    },
    # 2. "Present" instead of "Current"
    {
        "name": "Present_EndDate",
        "resume": """Senior Backend Engineer - Amazon (March 2022 - Present)
- Designed microservices architecture using Python and AWS
Software Engineer - Startup Inc (June 2019 - February 2022)
- Full stack development with Python, Django, PostgreSQL""",
        "skill": "Python",
        "required_years": 5,
        "expected_months": 81,  # 47 + 34 (Mar 2022-Feb 2026 + Jun 2019-Feb 2022)
        "description": "'Present' end date, full month names"
    },
    # 3. YYYY-MM format (ISO-ish)
    {
        "name": "ISO_Format",
        "resume": """Machine Learning Engineer | Google | 2023-03 to 2026-02
- Deployed production ML models at scale
Data Analyst | Facebook | 2020-06 to 2022-12
- Statistical analysis and A/B testing""",
        "skill": "Machine Learning",
        "required_years": 3,
        "expected_months": 35,  # only Google counts for ML
        "description": "YYYY-MM format, 'to' separator"
    },
    # 4. Slash date format (1/2024)
    {
        "name": "Slash_Format",
        "resume": """Cloud Architect - AWS Consulting (1/2023 - ongoing)
- Multi-region cloud infrastructure
DevOps Engineer - TechCorp (6/2020 - 12/2022)
- CI/CD pipelines, Kubernetes deployment""",
        "skill": "Cloud",
        "required_years": 4,
        "expected_months": 67,  # 37 + 30
        "description": "Slash date format, 'ongoing' end date"
    },
    # 5. Only years (no months)
    {
        "name": "Year_Only",
        "resume": """Full Stack Developer at WebCo, 2022 - 2025
- React, Node.js, MongoDB application development
Junior Developer at StartupXYZ, 2020 - 2021
- Frontend development with React and TypeScript""",
        "skill": "React",
        "required_years": 4,
        "expected_months": 48,  # ~36 + ~12 (year-only = approximate)
        "description": "Year-only dates, no months"
    },
    # 6. Internship (50% weight)
    {
        "name": "Internship_Weight",
        "resume": """Software Engineer | Microsoft | Aug 2023 - Present
- Backend services in Python/Go
Software Engineering Intern | Google | May 2023 - Jul 2023
- Summer internship, built internal tooling in Python
Computer Science Student | MIT | Sep 2019 - May 2023
- Coursework in Python, data structures, algorithms""",
        "skill": "Python",
        "required_years": 3,
        "expected_months": 31,  # 30 months full + 3*0.5=1.5mo intern (coursework=0)
        "description": "Internship at 50% weight, coursework at 0%"
    },
    # 7. Overlapping concurrent roles
    {
        "name": "Overlapping_Roles",
        "resume": """Lead Data Engineer | Uber | Mar 2023 - Feb 2026
- Led data platform team, Spark/Snowflake/dbt
Senior Data Engineer (Contract) | Netflix | Jan 2024 - Jun 2024
- Concurrent contract work, data pipeline optimization
Data Engineer | Lyft | Sep 2020 - Feb 2023
- Built ETL pipelines, Airflow orchestration""",
        "skill": "Data Engineering",
        "required_years": 5,
        "expected_months": 65,  # Mar 2023-Feb 2026 = 35, Sep 2020-Feb 2023 = 29, Netflix overlaps with Uber
        "description": "Overlapping concurrent roles, must not double-count"
    },
    # 8. Mixed title, skill embedded in duties
    {
        "name": "Skill_In_Duties",
        "resume": """Technology Consultant | Deloitte | Feb 2021 - Present
- Delivered 12+ Salesforce implementations
- Certified Salesforce Administrator and Platform Developer
Business Analyst | Acme Corp | Jan 2019 - Jan 2021
- Managed Salesforce CRM customization and reporting""",
        "skill": "Salesforce",
        "required_years": 5,
        "expected_months": 85,  # 60 + 25
        "description": "Skill embedded in duties, not in job title"
    },
    # 9. Very short stints
    {
        "name": "Short_Stints",
        "resume": """React Developer | StartupA | Nov 2025 - Jan 2026 (2 months)
React Developer | StartupB | Jul 2025 - Oct 2025 (4 months)
React Developer | StartupC | Jan 2025 - Jun 2025 (6 months)
Frontend Engineer | BigCo | Mar 2024 - Dec 2024 (10 months)
- All positions focused on React/Next.js development""",
        "skill": "React",
        "required_years": 2,
        "expected_months": 22,  # 2+4+6+10
        "description": "Many short stints with parenthetical durations"
    },
    # 10. Non-English date conventions (European)
    {
        "name": "European_Dates",
        "resume": """PROFESSIONAL EXPERIENCE
Java Developer, SAP AG, Walldorf — 01.2022 – present
- Enterprise Java backend development, Spring Boot microservices
Software Developer, Siemens, Munich — 09.2019 – 12.2021
- Java/Kotlin Android development""",
        "skill": "Java",
        "required_years": 5,
        "expected_months": 77,  # 49 + 28
        "description": "European DD.MM format, German companies"
    },
    # 11. Summary statement with explicit years claim
    {
        "name": "Summary_Claim",
        "resume": """SUMMARY: Seasoned DevOps engineer with 7+ years of experience in cloud infrastructure and automation.

EXPERIENCE:
DevOps Lead | CloudScale Inc | Apr 2021 - Feb 2026
- Terraform, Ansible, AWS infrastructure
DevOps Engineer | InfraWorks | Jun 2018 - Mar 2021
- CI/CD, Docker, Kubernetes""",
        "skill": "DevOps",
        "required_years": 7,
        "expected_months": 92,  # 58 + 34
        "description": "Summary claims 7+ years, dates must verify"
    },
    # 12. Academic experience mixed with professional
    {
        "name": "Academic_Mixed",
        "resume": """Research Scientist | NVIDIA | Jun 2024 - Present
- Deep learning model optimization
Graduate Research Assistant | Stanford AI Lab | Sep 2021 - May 2024
- PhD research in computer vision (ACADEMIC)
Machine Learning Intern | Apple | Jun 2021 - Aug 2021
- Summer internship in Siri ML team""",
        "skill": "Machine Learning",
        "required_years": 3,
        "expected_months": 21,  # 20mo NVIDIA + 1.5mo intern (academic=0)
        "description": "Academic research + internship, only professional counts"
    },
    # 13. Bullet-point only (no explicit dates for some roles)
    {
        "name": "Missing_Dates",
        "resume": """EXPERIENCE:
• Product Manager at TechVentures (2023 - Current) — Led product roadmap
• Product Analyst at DataDriven — Approximately 2 years — Market analysis and user research
• Marketing Coordinator at BrandCo — 1 year — Campaign management""",
        "skill": "Product Management",
        "required_years": 3,
        "expected_months": -1,  # Ambiguous, expect low confidence
        "description": "Vague durations, missing specific dates"
    },
    # 14. Balakrishna Jurollu's actual resume (the case that triggered this investigation)
    {
        "name": "Balakrishna_Actual",
        "resume": """PROFESSIONAL EXPERIENCE:
Data Scientist | OpenAI, USA | Jan 2024 – Current
- Developed Retrieval-Augmented Generation (RAG) systems using OpenAI, LangChain, and FAISS
- Built LLM-based internal chatbot using GPT-4, Pinecone, and LangChain
- Fine-tuned open-source models (LLaMA2, Falcon) using domain-specific datasets
- Deployed scalable AI models using Docker, Kubernetes, and FastAPI
- Automated model training, evaluation, and tracking using MLflow and Airflow pipelines
- Engineered prompts for LLMs such as GPT-4, Claude, and Gemini
- Applied advanced NLP techniques using NLTK, Transformers, and LangChain
- Performed A/B testing and model performance evaluation using SQL and Power BI

Data Scientist | Accenture, India | Jul 2021 – Aug 2023
- Developed supervised ML models for demand forecasting
- Built NLP pipelines for text classification and sentiment analysis
- Created REST APIs with Flask and FastAPI
- Implemented computer vision models with TensorFlow and OpenCV
- Optimized data pipelines using Apache Spark and Airflow
- Used AWS SageMaker for end-to-end ML workflows
- Built interactive dashboards with Tableau, Seaborn, and Plotly""",
        "skill": "Data Science",
        "required_years": 5,
        "expected_months": 51,  # 25 (Jan 2024-Feb 2026) + 26 (Jul 2021-Aug 2023)
        "description": "ACTUAL CASE: Balakrishna Jurollu, GPT said ~2.5yr, correct is ~4.25yr"
    },
    # 15. Multiple skills with different durations
    {
        "name": "Multi_Skill",
        "resume": """Senior Engineer | Stripe | Sep 2022 - Present
- Backend: Python, Go, PostgreSQL
- Infrastructure: AWS, Terraform, Docker
Engineer | Square | Mar 2020 - Aug 2022
- Backend: Python, Ruby, MySQL
- Some DevOps: basic Docker, Jenkins
Junior Developer | Startup | Jan 2019 - Feb 2020
- Frontend only: React, JavaScript, CSS""",
        "skill": "Python",
        "required_years": 5,
        "expected_months": 71,  # 41 (Stripe) + 30 (Square) = 71. Junior didn't use Python
        "description": "Multi-skill, Python only in 2 of 3 roles"
    },
]

# ─── GPT EXTRACTION PROMPT ───
EXTRACTION_PROMPT = """Extract structured work history from this resume. For EACH position, normalize dates to YYYY-MM format.

RULES:
1. "Current", "Present", "ongoing", "Now" → use "current" as the end date
2. If only a year is given (e.g., "2022"), use "YYYY-01" for start or "YYYY-12" for end
3. For slash dates like "1/2024", convert to "2024-01"
4. For European format "01.2022", convert to "2022-01"
5. Mark each position as: "full_time", "internship", "academic", or "contract"
6. List the PRIMARY skills used in each role
7. If dates are vague or missing, set confidence to "low"

RESUME:
{resume}

Respond in this exact JSON format:
{{
    "work_history": [
        {{
            "title": "Job Title",
            "company": "Company Name",
            "start": "YYYY-MM",
            "end": "YYYY-MM or current",
            "type": "full_time|internship|academic|contract",
            "skills": ["skill1", "skill2"],
            "confidence": "high|medium|low"
        }}
    ]
}}"""

# ─── GPT CALCULATION PROMPT (current approach) ───
CALCULATION_PROMPT = """Analyze this resume and calculate TOTAL years of experience for the skill "{skill}".

YEARS OF EXPERIENCE ANALYSIS (MANDATORY):
1. Identify ALL roles where the candidate used {skill}.
2. Calculate the total duration by summing the date ranges of those roles:
   - Use start/end dates (e.g., "Jan 2021 - Dec 2023" = 3.0 years).
   - For "Present" or ongoing roles, use today's date (February 16, 2026).
   - Internships count at 50% weight.
   - Academic/coursework does NOT count.
   - Overlapping roles should not be double-counted.
3. Report your calculation.

RESUME:
{resume}

Respond in this exact JSON format:
{{
    "skill": "{skill}",
    "required_years": {required_years},
    "estimated_years": <float>,
    "meets_requirement": true/false,
    "calculation_breakdown": "<show your math step by step>"
}}"""

# ─── PYTHON CALCULATION ENGINE ───

def parse_month(ym_str: str, today: date) -> date:
    """Parse YYYY-MM string to date. 'current' → today."""
    if ym_str.lower() in ("current", "present", "ongoing", "now"):
        return today
    parts = ym_str.split("-")
    return date(int(parts[0]), int(parts[1]), 1)


def calculate_total_months(work_history: list, skill: str, today: date) -> dict:
    """Calculate total months of experience for a skill from structured work history."""
    total_months = 0.0
    breakdown = []
    intervals = []
    
    for entry in work_history:
        # Check if skill is used in this role
        entry_skills = [s.lower() for s in entry.get("skills", [])]
        skill_lower = skill.lower()
        skill_match = any(skill_lower in s or s in skill_lower for s in entry_skills)
        
        if not skill_match:
            breakdown.append(f"  SKIP: {entry['title']} @ {entry['company']} — skill '{skill}' not found in {entry.get('skills', [])}")
            continue
        
        try:
            start = parse_month(entry["start"], today)
            end = parse_month(entry["end"], today)
            months_raw = (end.year - start.year) * 12 + (end.month - start.month)
            
            # Weight factor
            entry_type = entry.get("type", "full_time")
            if entry_type == "internship":
                weight = 0.5
            elif entry_type == "academic":
                weight = 0.0
            else:
                weight = 1.0
            
            weighted_months = months_raw * weight
            total_months += weighted_months
            intervals.append((start, end))
            
            breakdown.append(
                f"  {entry['title']} @ {entry['company']}: {entry['start']}→{entry['end']} = "
                f"{months_raw}mo × {weight} = {weighted_months:.1f}mo ({entry_type})"
            )
        except Exception as e:
            breakdown.append(f"  ERROR: {entry['title']} @ {entry['company']} — {e}")
    
    return {
        "total_months": total_months,
        "total_years": round(total_months / 12, 2),
        "breakdown": breakdown,
        "intervals": intervals
    }


def run_test():
    today = date(2026, 2, 16)
    results = []
    
    print("=" * 100)
    print("HYBRID YEARS CALCULATION — PROOF OF CONCEPT")
    print(f"Today's date: {today}")
    print(f"Model: gpt-4o | Temperature: 0.1")
    print("=" * 100)
    
    for i, tc in enumerate(TEST_CASES, 1):
        print(f"\n{'─' * 80}")
        print(f"TEST {i}/{len(TEST_CASES)}: {tc['name']}")
        print(f"Description: {tc['description']}")
        print(f"Skill: {tc['skill']} | Required: {tc['required_years']}yr | Expected: {tc['expected_months']}mo")
        print(f"{'─' * 80}")
        
        # ── Layer 1: GPT Extraction ──
        extraction_ok = False
        python_years = None
        extraction_confidence = "none"
        try:
            resp1 = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a resume parser. Extract structured data accurately."},
                    {"role": "user", "content": EXTRACTION_PROMPT.format(resume=tc["resume"])}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=800
            )
            extraction = json.loads(resp1.choices[0].message.content)
            work_history = extraction.get("work_history", [])
            
            print(f"\n  [EXTRACTION] Found {len(work_history)} positions:")
            for entry in work_history:
                conf = entry.get("confidence", "?")
                print(f"    • {entry['title']} @ {entry['company']}: {entry['start']} → {entry['end']} ({entry.get('type', '?')}) [confidence: {conf}]")
                print(f"      Skills: {entry.get('skills', [])}")
            
            # Check confidence
            confidences = [e.get("confidence", "high") for e in work_history]
            if all(c == "high" for c in confidences):
                extraction_confidence = "high"
            elif any(c == "low" for c in confidences):
                extraction_confidence = "low"
            else:
                extraction_confidence = "medium"
            
            # ── Layer 2: Python Calculation ──
            calc = calculate_total_months(work_history, tc["skill"], today)
            python_years = calc["total_years"]
            python_months = calc["total_months"]
            
            print(f"\n  [PYTHON CALC] Total: {python_months:.1f}mo = {python_years:.2f}yr")
            for line in calc["breakdown"]:
                print(f"    {line}")
            
            extraction_ok = True
            
        except Exception as e:
            print(f"\n  [EXTRACTION FAILED] {e}")
        
        # ── Comparison: GPT Self-Calculation ──
        gpt_years = None
        try:
            resp2 = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a strict technical recruiter. Calculate experience precisely."},
                    {"role": "user", "content": CALCULATION_PROMPT.format(
                        resume=tc["resume"], skill=tc["skill"], required_years=tc["required_years"]
                    )}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=600
            )
            gpt_result = json.loads(resp2.choices[0].message.content)
            gpt_years = float(gpt_result.get("estimated_years", 0))
            gpt_breakdown = gpt_result.get("calculation_breakdown", "N/A")
            
            print(f"\n  [GPT SELF-CALC] Estimated: {gpt_years:.2f}yr")
            print(f"    Breakdown: {gpt_breakdown[:200]}")
            
        except Exception as e:
            print(f"\n  [GPT CALC FAILED] {e}")
        
        # ── Comparison ──
        expected_years = tc["expected_months"] / 12.0 if tc["expected_months"] > 0 else None
        
        print(f"\n  ╔══════════════════════════════════════════╗")
        print(f"  ║ COMPARISON                               ║")
        print(f"  ╠══════════════════════════════════════════╣")
        if expected_years:
            print(f"  ║ Ground truth:  {expected_years:.2f}yr ({tc['expected_months']}mo)         ║")
        else:
            print(f"  ║ Ground truth:  AMBIGUOUS                ║")
        if python_years is not None:
            py_err = abs(python_years - expected_years) / expected_years * 100 if expected_years else None
            print(f"  ║ Hybrid:        {python_years:.2f}yr (err: {py_err:.1f}%)         ║" if py_err is not None else f"  ║ Hybrid:        {python_years:.2f}yr                    ║")
        else:
            print(f"  ║ Hybrid:        FAILED                   ║")
        if gpt_years is not None:
            gpt_err = abs(gpt_years - expected_years) / expected_years * 100 if expected_years else None
            print(f"  ║ GPT-only:      {gpt_years:.2f}yr (err: {gpt_err:.1f}%)         ║" if gpt_err is not None else f"  ║ GPT-only:      {gpt_years:.2f}yr                    ║")
        else:
            print(f"  ║ GPT-only:      FAILED                   ║")
        print(f"  ║ Extraction:    {extraction_confidence} confidence     ║")
        print(f"  ╚══════════════════════════════════════════╝")
        
        results.append({
            "name": tc["name"],
            "expected_years": expected_years,
            "python_years": python_years,
            "gpt_years": gpt_years,
            "extraction_ok": extraction_ok,
            "extraction_confidence": extraction_confidence,
            "python_error_pct": abs(python_years - expected_years) / expected_years * 100 if (python_years and expected_years) else None,
            "gpt_error_pct": abs(gpt_years - expected_years) / expected_years * 100 if (gpt_years and expected_years) else None,
        })
    
    # ── SUMMARY ──
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    
    extraction_successes = sum(1 for r in results if r["extraction_ok"])
    high_confidence = sum(1 for r in results if r["extraction_confidence"] == "high")
    
    py_errors = [r["python_error_pct"] for r in results if r["python_error_pct"] is not None]
    gpt_errors = [r["gpt_error_pct"] for r in results if r["gpt_error_pct"] is not None]
    
    py_accurate = sum(1 for e in py_errors if e < 10)  # Within 10% of ground truth
    gpt_accurate = sum(1 for e in gpt_errors if e < 10)
    
    print(f"\nExtraction success rate: {extraction_successes}/{len(TEST_CASES)} ({extraction_successes/len(TEST_CASES)*100:.0f}%)")
    print(f"High confidence extractions: {high_confidence}/{len(TEST_CASES)}")
    print(f"\nAccuracy (within 10% of ground truth):")
    print(f"  Hybrid (GPT extract + Python calc): {py_accurate}/{len(py_errors)} ({py_accurate/len(py_errors)*100:.0f}%)")
    print(f"  GPT-only (current approach):         {gpt_accurate}/{len(gpt_errors)} ({gpt_accurate/len(gpt_errors)*100:.0f}%)")
    print(f"\nAverage error:")
    print(f"  Hybrid:   {sum(py_errors)/len(py_errors):.1f}%")
    print(f"  GPT-only: {sum(gpt_errors)/len(gpt_errors):.1f}%")
    
    print(f"\n{'─' * 60}")
    print("DETAILED RESULTS:")
    print(f"{'Test Name':<25} {'Expected':>8} {'Hybrid':>8} {'GPT':>8} {'H.Err':>8} {'G.Err':>8} {'Conf':>8}")
    print(f"{'─' * 25} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8}")
    for r in results:
        exp = f"{r['expected_years']:.2f}" if r['expected_years'] else "???"
        hyb = f"{r['python_years']:.2f}" if r['python_years'] is not None else "FAIL"
        gpt = f"{r['gpt_years']:.2f}" if r['gpt_years'] is not None else "FAIL"
        he = f"{r['python_error_pct']:.1f}%" if r['python_error_pct'] is not None else "N/A"
        ge = f"{r['gpt_error_pct']:.1f}%" if r['gpt_error_pct'] is not None else "N/A"
        print(f"{r['name']:<25} {exp:>8} {hyb:>8} {gpt:>8} {he:>8} {ge:>8} {r['extraction_confidence']:>8}")


if __name__ == "__main__":
    run_test()
