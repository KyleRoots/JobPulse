"""Tests for native Indeed inbound field remapping.

Covers status/source/owner remap, human-owner preservation, Idempotent
skip of already-remapped sources, and exclusion of Indeed Resume Search.
Documents interaction with owner_reassignment: Unassigned → Myticas API
User places the candidate into the same API-owner pool that activity-based
reassignment already monitors.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tasks.indeed_inbound_remap import (
    MYTICAS_API_USER_ID,
    TARGET_SOURCE,
    TARGET_STATUS,
    UNASSIGNED_OWNER_ID,
    build_indeed_inbound_remap_payload,
    is_native_indeed_source,
    is_unassigned_owner,
    remap_indeed_inbound_fields,
)


def _cand(
    *,
    cid=9001,
    status='New Lead',
    source='Indeed',
    owner_id=UNASSIGNED_OWNER_ID,
    owner_name='Unassigned User',
):
    owner = None
    if owner_id is not None or owner_name is not None:
        owner = {'id': owner_id, 'name': owner_name}
    return {
        'id': cid,
        'firstName': 'Indeed',
        'lastName': 'Applicant',
        'status': status,
        'source': source,
        'owner': owner,
        'dateLastModified': 1700000000000,
    }


class TestHelpers:
    def test_unassigned_by_id(self):
        assert is_unassigned_owner({'id': 1, 'name': 'Unassigned User'})
        assert is_unassigned_owner({'id': '1', 'name': 'Unassigned User'})

    def test_unassigned_by_name(self):
        assert is_unassigned_owner({'id': 999, 'name': 'Unassigned User'})

    def test_missing_owner_is_unassigned(self):
        assert is_unassigned_owner(None)
        assert is_unassigned_owner({})
        assert is_unassigned_owner({'id': None})

    def test_human_owner_not_unassigned(self):
        assert not is_unassigned_owner({'id': 555, 'name': 'Kyle Roots'})

    def test_myticas_api_user_not_unassigned(self):
        assert not is_unassigned_owner(
            {'id': MYTICAS_API_USER_ID, 'name': 'Myticas API User'}
        )

    def test_source_exact_indeed_only(self):
        assert is_native_indeed_source('Indeed')
        assert not is_native_indeed_source('Indeed Job Board')
        assert not is_native_indeed_source('Indeed Resume Search')
        assert not is_native_indeed_source('LinkedIn Job Board')
        assert not is_native_indeed_source('')
        assert not is_native_indeed_source(None)


class TestBuildPayload:
    def test_remaps_all_three_fields(self):
        payload = build_indeed_inbound_remap_payload(_cand())
        assert payload == {
            'status': TARGET_STATUS,
            'source': TARGET_SOURCE,
            'owner': {'id': MYTICAS_API_USER_ID},
        }
        assert payload['status'] == 'Online Applicant'
        assert payload['source'] == 'Indeed Job Board'
        assert payload['owner']['id'] == 1147490

    def test_skips_human_owner(self):
        payload = build_indeed_inbound_remap_payload(
            _cand(owner_id=777, owner_name='Kyle Roots')
        )
        assert payload.get('status') == TARGET_STATUS
        assert payload.get('source') == TARGET_SOURCE
        assert 'owner' not in payload

    def test_skips_already_indeed_job_board(self):
        payload = build_indeed_inbound_remap_payload(
            _cand(source='Indeed Job Board')
        )
        assert payload == {}

    def test_skips_indeed_resume_search(self):
        payload = build_indeed_inbound_remap_payload(
            _cand(source='Indeed Resume Search')
        )
        assert payload == {}

    def test_does_not_stomp_non_new_lead_status(self):
        payload = build_indeed_inbound_remap_payload(
            _cand(status='Active')
        )
        assert payload.get('source') == TARGET_SOURCE
        assert 'status' not in payload
        assert payload.get('owner') == {'id': MYTICAS_API_USER_ID}

    def test_null_owner_defaults_to_myticas(self):
        payload = build_indeed_inbound_remap_payload(
            _cand(owner_id=None, owner_name=None)
        )
        assert payload['owner'] == {'id': MYTICAS_API_USER_ID}

    def test_already_myticas_owner_not_rewritten(self):
        """Owner already Myticas API User: still remap status/source; leave owner."""
        payload = build_indeed_inbound_remap_payload(
            _cand(
                owner_id=MYTICAS_API_USER_ID,
                owner_name='Myticas API User',
            )
        )
        assert payload.get('status') == TARGET_STATUS
        assert payload.get('source') == TARGET_SOURCE
        assert 'owner' not in payload


class TestOwnershipActivitySemantics:
    """Document: after Unassigned → Myticas API User, owner_reassignment
    can take over when a human leaves activity — same as LinkedIn inbound.

    owner_reassignment queries owner.id IN api_user_ids (includes 1147490).
    Remapping Unassigned → 1147490 is what makes native Indeed eligible;
    overwriting a human owner would fight that automation.
    """

    def test_unassigned_becomes_api_user_eligible_for_reassignment(self):
        payload = build_indeed_inbound_remap_payload(_cand())
        assert payload['owner']['id'] == MYTICAS_API_USER_ID
        # Activity-based reassignment only scans api_user_ids owners —
        # Unassigned (1) is NOT in that set; Myticas API User is.
        assert payload['owner']['id'] != UNASSIGNED_OWNER_ID

    def test_human_owner_preserved_so_activity_claim_sticks(self):
        payload = build_indeed_inbound_remap_payload(
            _cand(owner_id=4242, owner_name='Recruiter Jane')
        )
        assert 'owner' not in payload


class TestRemapCycle:
    def _mock_bh(self):
        bh = MagicMock()
        bh.authenticate.return_value = True
        bh.base_url = 'https://rest.bullhorn.test/'
        bh.rest_token = 'tok'
        return bh

    def test_feature_flag_off_skips(self, monkeypatch):
        monkeypatch.setenv('INDEED_INBOUND_REMAP_ENABLED', 'false')
        result = remap_indeed_inbound_fields()
        assert result['enabled'] is False
        assert result['updated'] == 0

    def test_cycle_updates_eligible_and_skips_others(self, monkeypatch):
        monkeypatch.setenv('INDEED_INBOUND_REMAP_ENABLED', 'true')
        bh = self._mock_bh()

        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {
            'data': [
                _cand(cid=1),  # full remap
                _cand(cid=2, owner_id=777, owner_name='Human'),  # no owner
                _cand(cid=3, source='Indeed Resume Search'),  # skip
                _cand(cid=4, source='Indeed Job Board'),  # skip
            ]
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {
            'changeType': 'UPDATE',
            'changedEntityId': 1,
        }

        with patch('bullhorn_service.BullhornService', return_value=bh), \
             patch('tasks.indeed_inbound_remap._requests.get', return_value=search_resp), \
             patch('tasks.indeed_inbound_remap._requests.post', return_value=update_resp) as mock_post:
            result = remap_indeed_inbound_fields(lookback_hours=24)

        assert result['enabled'] is True
        assert result['found'] == 4
        assert result['skipped_wrong_source'] == 2
        assert result['eligible'] == 2
        assert result['updated'] == 2
        assert result['skipped_human_owner_only'] == 1
        assert mock_post.call_count == 2

        bodies = [call.kwargs['json'] for call in mock_post.call_args_list]
        assert {
            'status': 'Online Applicant',
            'source': 'Indeed Job Board',
            'owner': {'id': 1147490},
        } in bodies
        assert {
            'status': 'Online Applicant',
            'source': 'Indeed Job Board',
        } in bodies
        assert sum(1 for b in bodies if 'owner' in b) == 1
