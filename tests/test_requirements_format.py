"""Tests for screening-requirements bullet normalization."""
from utils.requirements_format import normalize_requirements_to_bullets


def test_already_bulleted_is_normalized():
    raw = "* Heavy travel\n- PLC experience\n• 50-60 hour weeks"
    out = normalize_requirements_to_bullets(raw)
    assert out == (
        "- Heavy travel\n"
        "- PLC experience\n"
        "- 50-60 hour weeks"
    )


def test_paragraph_split_into_bullets():
    raw = (
        "Heavy USA travel: 3 weeks on the road / 1 week home. "
        "Field experience in industrial environments. "
        "Control systems experience including DCS, PLC, Allen Bradley."
    )
    out = normalize_requirements_to_bullets(raw)
    lines = out.split('\n')
    assert len(lines) == 3
    assert all(line.startswith('- ') for line in lines)
    assert 'Heavy USA travel' in lines[0]
    assert 'Field experience' in lines[1]
    assert 'Control systems' in lines[2]


def test_numbered_and_pipe_lists():
    assert normalize_requirements_to_bullets('1. Kubernetes\n2) Docker') == (
        '- Kubernetes\n- Docker'
    )
    assert normalize_requirements_to_bullets('Kubernetes | Docker | Jenkins') == (
        '- Kubernetes\n- Docker\n- Jenkins'
    )


def test_empty_and_dedupe():
    assert normalize_requirements_to_bullets('') == ''
    assert normalize_requirements_to_bullets(None) == ''
    assert normalize_requirements_to_bullets('- Same\n- same\n- Other') == (
        '- Same\n- Other'
    )


def test_legacy_double_dot_separator():
    raw = 'Req one.. Req two.. Req three'
    out = normalize_requirements_to_bullets(raw)
    assert out.split('\n') == ['- Req one', '- Req two', '- Req three']
