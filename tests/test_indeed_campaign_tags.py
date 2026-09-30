"""Qualified Indeed campaign-tag mapping."""

from indeed_publish.campaign_tags import (
    MYTICAS_CAMPAIGN_TAG,
    apply_campaign_tag,
    campaign_tag_for_job,
    department_owns_campaign_tag,
    resolve_qualified_campaign_tag,
    strip_campaign_tags,
)


def test_resolve_core_departments():
    assert resolve_qualified_campaign_tag('Calhoun') == '#INDCal'
    assert resolve_qualified_campaign_tag('Appleton') is None
    assert resolve_qualified_campaign_tag('Appleton (WI)') is None
    assert department_owns_campaign_tag('Appleton') is True
    assert department_owns_campaign_tag('  Appleton (WI) ') is True
    assert department_owns_campaign_tag('Calhoun') is False
    assert resolve_qualified_campaign_tag('Internal') == '#INDWin'
    assert resolve_qualified_campaign_tag('Internal Reqs') == '#INDWin'
    assert resolve_qualified_campaign_tag('Southfield') == '#INDLiv'
    assert resolve_qualified_campaign_tag('Flint (Brighton)') == '#INDFli'
    assert resolve_qualified_campaign_tag('Columbus') == '#INDCol'
    assert resolve_qualified_campaign_tag('Columbus-KIT') == '#INDCol'
    assert resolve_qualified_campaign_tag('Columbus KIT') == '#INDCol'
    assert resolve_qualified_campaign_tag('Columbus-Key') == '#INDCol'
    assert resolve_qualified_campaign_tag('Columbus Key') == '#INDCol'
    assert resolve_qualified_campaign_tag('Port Huron') == '#INDMary'
    assert resolve_qualified_campaign_tag('Chattanooga') == '#INDChat'
    assert resolve_qualified_campaign_tag('Grand Rapids') == '#INDGrand'
    assert resolve_qualified_campaign_tag('Katy') == '#INDKat'
    assert resolve_qualified_campaign_tag('QPT') == '#INDQT'
    assert resolve_qualified_campaign_tag('Technical') == '#INDQT'
    assert resolve_qualified_campaign_tag('Unknown Town') is None
    assert resolve_qualified_campaign_tag('') is None


def test_apply_strips_indshow_and_replaces():
    html = '<p>hi</p>   #INDShow'
    out = apply_campaign_tag(html, '#INDHar')
    assert out.endswith('   #INDHar')
    assert '#INDShow' not in out


def test_apply_strips_unhashed_legacy():
    html = '<div>INDCon</div>'
    out = apply_campaign_tag(html, '#INDCon')
    assert out.rstrip().endswith('#INDCon')
    assert out.count('INDCon') == 1


def test_strip_only():
    assert strip_campaign_tags('body   #INDShow') == 'body'
    assert MYTICAS_CAMPAIGN_TAG not in strip_campaign_tags('x   #INDShow')


def test_campaign_tag_for_job_myticas(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    tag, reason = campaign_tag_for_job({'correlatedCustomText1': 'Calhoun'}, qualified=False)
    assert tag == '#INDShow'
    assert reason == 'myticas'


def test_other_department_still_replaces_appleton_leftover():
    out = apply_campaign_tag('<p>body</p>   #IND-WH', '#INDCal')
    assert out.endswith('   #INDCal')
    assert '#IND-WH' not in out
    out_w = apply_campaign_tag('<p>body</p>   #IND-W', '#INDCal')
    assert '#IND-W' not in out_w
    assert out_w.endswith('   #INDCal')


def test_appleton_is_manual_and_myticas_is_not():
    tag, reason = campaign_tag_for_job(
        {'correlatedCustomText1': 'Appleton'}, qualified=True
    )
    assert tag == ''
    assert reason == 'manual-department:Appleton'
    wi_tag, wi_reason = campaign_tag_for_job(
        {'correlatedCustomText1': 'Appleton (WI)'}, qualified=True
    )
    assert wi_tag == ''
    assert wi_reason.startswith('manual-department:')
    myticas_tag, myticas_reason = campaign_tag_for_job(
        {'correlatedCustomText1': 'Appleton'}, qualified=False
    )
    assert myticas_tag == '#INDShow'
    assert myticas_reason == 'myticas'


def test_campaign_tag_for_job_qualified():
    tag, reason = campaign_tag_for_job(
        {'correlatedCustomText1': 'Harrisonburg'}, qualified=True
    )
    assert tag == '#INDHar'
    assert 'Harrisonburg' in reason
