"""Tests for garbled resume-text detection (broken PDF ToUnicode / OLE junk)."""

from utils.resume_text_quality import is_garbled_resume_text

# Representative of Gwena Thomas (4672488) Overview / description extract
GWENA_GARBLED = (
    "c U=Zc= m& m Kb X]`B@Nb BBZc B`]`Nb Bb KX=NU @\\Xm -NZSBAVZm \n\n"
    "5Bbf Wdb Aa Og B[m3a\\QB@dm.=[=LBamh Od Mm\\g Bam=m AB@=ABm\\Dm Bi^Ca OB[@Bm "
    "WB=AO[Lm=AYO[Obda=eg Bm\\^Ba=e\\[b m^a\\@fa BYB[d m=[Am G[=[@O=Wm Y=[=LBYB[dm "
    "Df[@d P\\[bm=@a\\bbm DBABa=Wm=[Am LW\\>=Wm B[d Ba^a Ob Bm B[g Oa\\[YB[db m6TOWWBAm\n\n"
    "Experience \n\n"
    "3a\\QB@dm.=Z=KBXBZdm lm 3a\\@fa CXCZdm m \\Zda=@dm AXOZObda=d P\\Zm lm "
    "AKBdm3W=ZZOZKm m8a=@TOZKm lm :BZA\\am.=Z=LBXBZdm lm 6d=TBM\\WABam#[K=KCXCZdm\n\n"
    "Skills \n\n"
    "f Ujm 4`Bb BZcm 'j?`NA\n.=[=LCm LW\\>=Wm^a\\@fa BYB[dm\\^Ca=d P\\[bmbf^^\\ad P[Lm\n\n"
    "Education \n\n"
    "\"B@m /=jm l .=[=LCAmd MBm Df WWm WODB@j@WCm\\Dm La=[dbm=AYO[Obda=d P\\[ m"
)


CLEAN_RESUME = """
GWENA THOMAS
Project Manager | Procurement & Administrative Operations
Atlanta, GA | (470) 969-2879 | gsmpreciseenterprises@gmail.com

PROFESSIONAL SUMMARY
Results-driven Project Manager with over a decade of experience leading
administrative operations, procurement, and financial management.

EXPERIENCE
Government Contract Financial Supervisor / Senior Client Financial Analyst
WPP — July 2023 – Present (Hybrid)
- Managed global procurement for a major federal client across 124 active projects
- Partnered with stakeholders to identify process gaps and lead category strategies

Grants Management Team Lead / Project Manager III
The Building People — Dec 2021 – July 2023
- Directed a remote team of 12 and improved grant application success rates

EDUCATION
Bachelor of Science in Psychology and Human Services
Clayton State University

SKILLS
Project Management, Procurement, Budget Planning, Microsoft Office 365, SharePoint
"""


class TestIsGarbledResumeText:
    def test_gwena_style_mojibake_is_garbled(self):
        assert is_garbled_resume_text(GWENA_GARBLED) is True

    def test_clean_english_resume_is_not_garbled(self):
        assert is_garbled_resume_text(CLEAN_RESUME) is False

    def test_ole_word_junk_is_garbled(self):
        junk = (
            "WW8Num1z OJQJ^J phOJQJ OJQJo more binary garbage from Word OLE "
            "WW8Num OJQJ ^J  still more padding text here to exceed min length"
        )
        assert is_garbled_resume_text(junk) is True

    def test_short_text_not_flagged(self):
        assert is_garbled_resume_text('short') is False
        assert is_garbled_resume_text('') is False
        assert is_garbled_resume_text(None) is False

    def test_html_wrapped_garbled_still_detected(self):
        html = f"<div><p>{GWENA_GARBLED}</p></div>"
        assert is_garbled_resume_text(html) is True
