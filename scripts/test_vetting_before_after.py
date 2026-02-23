#!/usr/bin/env python3
"""
Before/After vetting simulation for the location-based vetting fix.

This script simulates the vetting decision for:
- Job: Embedded Software Developer, Ottawa, Ontario, Canada (On-site, 80% threshold)
- Custom override: "Candidates must have at least 5 years of professional working experience in Canada."
- Candidate: Leonel Akanji (Ottawa, ON address, but work history in Turkey + US only)

It calls the OpenAI API twice:
1. BEFORE prompt (old soft guidance)
2. AFTER prompt (new hard gate)

Usage: OPENAI_API_KEY=sk-... python3 scripts/test_vetting_before_after.py
"""

import os
import sys
import json

# Ensure we can import from the project
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai package not installed. Run: pip3 install openai")
    sys.exit(1)

api_key = os.environ.get('OPENAI_API_KEY')
if not api_key:
    print("ERROR: OPENAI_API_KEY environment variable not set.")
    sys.exit(1)

client = OpenAI(api_key=api_key)

# ─── Test data ───────────────────────────────────────────────────────────────

RESUME_TEXT = """
LEONEL AKANJI
Firmware Engineer
Ottawa, ON
(343) 322-6402
leonelakanji98@gmail.com

SUMMARY
Embedded/Firmware engineer with experience designing reliable firmware and mixed-signal systems
for low-power ARM-based platforms and environmental sensing instrumentation.

TECHNICAL SKILLS
Embedded Systems and Programming: C, C++, Python, FPGA, Verilog, Vivado, ARM Cortex-M (EFR32, STM32, ESP32, nRF52840), FreeRTOS, CMSIS-DSP, bootloaders, OTA updates, embedded Linux.
PCB Design and Hardware: Multi-layer PCB design (Altium, Proteus, Multism, EasyEDA)
Protocols: BLE, Wi-Fi, SPI, I2C, UART, RS-232/485, GPS, RFID, LoRa, NFC, ADC, TCP/IP

EXPERIENCE

Research Assistant - AI Embedded Systems
2023 - 2025
Clarkson University, New York, United States
- Designed ultra-low-power RF/mixed-signal biosensor PCBs
- Performed precision 0402 SMD soldering
- Designed multi-layer PCBs in Altium and EasyEDA
- Conducted RF-related BLE hardware testing

Embedded Systems - Flutter Mobile App Developer
2022 - 2023
Gokido - Kocaeli Teknopark, Kovalei, Turkey
- Designed firmware for the Gokido Smart Cane, integrating BLE, Wi-Fi modules, GPS, and NFC
- Developed and debugged wireless communication pipelines

Software Engineer Intern
May 2021 - Oct 2021
Yongatek Electronics, Istanbul, Turkey
- Developed C++/Qt apps for real-time visualization of embedded data streams
- Performed hardware-software debugging of wireless sensor modules

AI/Embedded Software Engineer Training
Mar 2025 - Sep 2025
Get Hire Technologies Inc. and Omdena, New Orleans, Louisiana
- Developed AI-powered mental health solutions using multi-agent systems

EDUCATION AND TRAINING
Master of Science in Electrical and Computer Engineering, 2024
Clarkson University, New York, United States

Bachelor of Science in Mechatronics Engineering, 2022
Kocaeli University, Kocaeli, Turkey
"""

JOB_TITLE = "Embedded Software Developer"
JOB_DESCRIPTION = "We are looking for an Embedded Software Developer to join our Ottawa team. Requirements: C/C++, embedded systems, ARM microcontrollers, RTOS, BLE/wireless protocols, PCB design experience."
JOB_LOCATION = "Ottawa, Ontario, Canada"
WORK_TYPE = "On-Site"
CUSTOM_REQUIREMENTS = "Candidates must have at least 5 years of professional working experience in Canada."
CANDIDATE_LOCATION = "Ottawa, Ontario, Canada"

# ─── Prompt templates ────────────────────────────────────────────────────────

SYSTEM_MESSAGE = """You are a strict, evidence-based technical recruiter analyzing candidate-job fit.

CRITICAL RULES:
1. You MUST only cite skills and experience that are EXPLICITLY written in the candidate's resume.
2. You MUST NOT infer or hallucinate skills that are not directly stated.
3. Be honest - a mismatched candidate should score LOW even if they have impressive but irrelevant skills.
4. LOCATION MATTERS: Check if the candidate's location is compatible with the job's work type.
   - Remote jobs: Candidate must be in the same COUNTRY for tax/legal compliance.
   - On-site/Hybrid jobs: Candidate should be in or near the job's city/metro area.
   - If candidate location doesn't match, this is a GAP that should reduce their score."""

LOCATION_INSTRUCTION = f"""
LOCATION REQUIREMENT ({WORK_TYPE} Position):
- Job Location: {JOB_LOCATION} (Work Type: {WORK_TYPE})
- Candidate Location: {CANDIDATE_LOCATION}
- For ON-SITE/HYBRID positions: Candidate should be in or near the job's city/metro area, or willing to relocate.
- Local candidates CAN work on-site by default."""

RESPONSE_FORMAT = """
Respond in JSON format with these exact fields:
{
    "match_score": <integer 0-100>,
    "match_summary": "<2-3 sentence summary>",
    "skills_match": "<skills from resume matching job>",
    "experience_match": "<relevant experience>",
    "gaps_identified": "<ALL mandatory requirements NOT met>",
    "key_requirements": "<top 3-5 mandatory requirements>"
}

SCORING GUIDELINES:
- 85-100: Meets nearly ALL mandatory requirements with evidence AND location matches
- 70-84: Meets MOST mandatory requirements, 1-2 minor gaps
- 50-69: Some requirements met but missing key qualifications or location issues
- 30-49: Tangential experience, significant gaps, or major location mismatch
- 0-29: Background does not align"""

# ─── BEFORE prompt (old soft guidance) ───────────────────────────────────────

BEFORE_REQUIREMENTS = f"""
IMPORTANT: Use these specific requirements for evaluation (manually specified):
{CUSTOM_REQUIREMENTS}

Focus ONLY on these requirements when scoring. Ignore nice-to-haves in the job description."""

# ─── AFTER prompt (new hard gate) ────────────────────────────────────────────

AFTER_REQUIREMENTS = f"""
CRITICAL - CUSTOM OVERRIDE REQUIREMENTS (manually specified by recruiter):
{CUSTOM_REQUIREMENTS}

These are HARD REQUIREMENTS set by the recruiter. Every custom requirement listed above is a MANDATORY pass/fail gate:
- If the candidate does NOT meet ANY ONE of these custom requirements, the match_score MUST be capped at 60 or below (Not Recommended).
- List each unmet custom requirement explicitly in gaps_identified, prefixed with "[CUSTOM UNMET]".
- For location/geography requirements (e.g., "X years of experience in [country]"), verify against the candidate's ACTUAL WORK HISTORY locations, not just their current address.
- A candidate who lives in Canada but has zero professional experience in Canada does NOT satisfy "5 years of professional experience in Canada."
- Do NOT let strong technical fit override an unmet custom requirement — these are non-negotiable."""


def build_prompt(requirements_instruction):
    return f"""Analyze how well this candidate's resume matches the MANDATORY job requirements.
Provide an objective assessment with a percentage match score (0-100).
{requirements_instruction}
{LOCATION_INSTRUCTION}

JOB DETAILS:
- Title: {JOB_TITLE}
- Location: {JOB_LOCATION} (Work Type: {WORK_TYPE})
- Description: {JOB_DESCRIPTION}

CANDIDATE INFORMATION:
- Known Location: {CANDIDATE_LOCATION}

CANDIDATE RESUME:
{RESUME_TEXT}

CRITICAL INSTRUCTIONS:
1. ONLY reference skills and experience EXPLICITLY STATED in the resume.
2. DO NOT infer or hallucinate skills.
3. If a MANDATORY requirement is NOT in the resume, list it in gaps_identified.
{RESPONSE_FORMAT}

BE HONEST. If the resume does not show required skills OR candidate location doesn't match, the candidate should NOT score high."""


def run_vetting(label, requirements_instruction):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    prompt = build_prompt(requirements_instruction)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1000
    )

    result = json.loads(response.choices[0].message.content)
    score = int(result.get('match_score', 0))

    # Apply post-scoring cap for AFTER scenario
    if label == "AFTER (Hard Gate)":
        gaps = result.get('gaps_identified', '')
        if '[CUSTOM UNMET]' in gaps and score > 60:
            print(f"  ⚠️  Code enforcement: AI scored {score}% but custom unmet → capping at 60%")
            score = 60
            result['match_score'] = 60

    threshold = 80
    recommended = "✅ RECOMMENDED" if score >= threshold else "❌ NOT RECOMMENDED"

    print(f"\n  Score:          {score}%")
    print(f"  Threshold:      {threshold}%")
    print(f"  Decision:       {recommended}")
    print(f"\n  Summary:        {result.get('match_summary', 'N/A')}")
    print(f"\n  Gaps Identified:")
    for line in result.get('gaps_identified', 'None').split('. '):
        if line.strip():
            print(f"    • {line.strip()}")
    print(f"\n  Skills Match:   {result.get('skills_match', 'N/A')[:120]}...")

    return result


if __name__ == '__main__':
    print("\n" + "╔" + "═"*68 + "╗")
    print("║  VETTING FIX: Before/After Comparison                              ║")
    print("║  Job: Embedded Software Developer, Ottawa, Canada (On-site, 80%)   ║")
    print("║  Custom: '5 years professional experience in Canada'               ║")
    print("║  Candidate: Leonel Akanji (Ottawa address, 0 years Canada work)    ║")
    print("╚" + "═"*68 + "╝")

    before = run_vetting("BEFORE (Soft Guidance)", BEFORE_REQUIREMENTS)
    after = run_vetting("AFTER (Hard Gate)", AFTER_REQUIREMENTS)

    print(f"\n{'='*70}")
    print(f"  COMPARISON")
    print(f"{'='*70}")
    print(f"  BEFORE score: {before.get('match_score')}%  →  AFTER score: {after.get('match_score')}%")
    print(f"  BEFORE decision: {'✅ Recommended' if before.get('match_score', 0) >= 80 else '❌ Not Recommended'}")
    print(f"  AFTER decision:  {'✅ Recommended' if after.get('match_score', 0) >= 80 else '❌ Not Recommended'}")
    print()
