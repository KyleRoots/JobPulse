"""
Tests for the human-owner skip in `screening.detection.detect_new_applicants`.

Background: Bullhorn's search index can lag ~1 minute behind a freshly-added
recruiter note. During that window, the candidate's `dateLastModified`
advances and matches the screening detection query, but the dedup check
sees no new recruiter activity yet — so the 5-min screening cycle re-vets a
candidate that is already being actively worked.

Fix under test: once a candidate's `owner.id` is NOT in the configured
`api_user_ids` list, the candidate is skipped before the dedup check ever
runs. Gated by the `screening_skip_human_owned` VettingConfig kill switch
(default ON) so it can be disabled without a deploy if it ever misfires.
"""
from unittest.mock import MagicMock

from screening.detection import (
    CandidateDetectionMixin,
    _is_human_owned,
    _parse_api_user_ids_for_screening,
    _screening_skip_human_owned,
)


# ─── Test harness ────────────────────────────────────────────────────────────

class _Detector(CandidateDetectionMixin):
    """Concrete subclass for testing the mixin in isolation. Stubs the
    Bullhorn-service hook and the last-run timestamp source."""

    def __init__(self, bullhorn=None, last_run=None):
        self._bullhorn = bullhorn
        self._last_run = last_run
        self._skip_calls: list = []

    def _get_bullhorn_service(self):
        return self._bullhorn

    def _get_last_run_timestamp(self):
        return self._last_run

    def _should_skip_candidate(self, candidate_id, applied_job_id=None,
                                bullhorn=None):
        """Track calls and never short-circuit so we can verify the human-
        owner gate ran BEFORE this check."""
        self._skip_calls.append(candidate_id)
        return False


def _ensure_config(app, key, value):
    from app import db
    from models import VettingConfig
    with app.app_context():
        row = VettingConfig.query.filter_by(setting_key=key).first()
        if row:
            row.setting_value = value
        else:
            db.session.add(VettingConfig(setting_key=key, setting_value=value))
        db.session.commit()


def _delete_config(app, key):
    from app import db
    from models import VettingConfig
    with app.app_context():
        VettingConfig.query.filter_by(setting_key=key).delete()
        db.session.commit()


def _make_candidate(cid, owner_id=None, owner_name='API User',
                    first='Jane', last='Doe'):
    """Build a fake Bullhorn candidate dict with optional owner block."""
    cand = {
        'id': cid,
        'firstName': first,
        'lastName': last,
        'email': f'{first.lower()}@example.com',
        'phone': '555-0100',
        'status': 'Online Applicant',
        'dateAdded': 1700000000000,
        'dateLastModified': 1700000060000,
        'source': 'LinkedIn Job Board',
    }
    if owner_id is not None:
        cand['owner'] = {'id': owner_id, 'name': owner_name}
    return cand


def _make_bullhorn(candidates):
    """Build a fake authenticated Bullhorn whose .session.get returns the
    given candidate list."""
    bh = MagicMock()
    bh.base_url = 'https://rest45.example/'
    bh.rest_token = 'token-xyz'
    bh.authenticate.return_value = True

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'data': candidates}
    bh.session.get.return_value = resp
    return bh


# ─── Helper-function unit tests ──────────────────────────────────────────────

class TestParseApiUserIds:
    def test_returns_empty_when_missing(self, app):
        with app.app_context():
            _delete_config(app, 'api_user_ids')
            assert _parse_api_user_ids_for_screening() == []

    def test_parses_csv(self, app):
        with app.app_context():
            _ensure_config(app, 'api_user_ids', '4582015,4591841,4593767')
            assert _parse_api_user_ids_for_screening() == [
                4582015, 4591841, 4593767,
            ]

    def test_strips_non_numeric(self, app):
        with app.app_context():
            _ensure_config(app, 'api_user_ids', '111, abc, 222 , ,333')
            assert _parse_api_user_ids_for_screening() == [111, 222, 333]


class TestKillSwitchDefault:
    def test_default_is_true_when_missing(self, app):
        with app.app_context():
            _delete_config(app, 'screening_skip_human_owned')
            assert _screening_skip_human_owned() is True

    def test_false_when_set_false(self, app):
        with app.app_context():
            _ensure_config(app, 'screening_skip_human_owned', 'false')
            assert _screening_skip_human_owned() is False

    def test_true_for_truthy_variants(self, app):
        with app.app_context():
            for v in ('true', 'True', '1', 'yes', 'on'):
                _ensure_config(app, 'screening_skip_human_owned', v)
                assert _screening_skip_human_owned() is True, (
                    f"Expected truthy for {v!r}"
                )


class TestIsHumanOwned:
    def test_returns_false_when_no_owner(self):
        assert _is_human_owned({}, [9999]) is False
        assert _is_human_owned({'owner': None}, [9999]) is False

    def test_returns_false_when_owner_in_api_users(self):
        cand = {'owner': {'id': 9999, 'name': 'API Bot'}}
        assert _is_human_owned(cand, [9999, 8888]) is False

    def test_returns_true_when_owner_is_human(self):
        cand = {'owner': {'id': 777, 'name': 'Bob Smith'}}
        assert _is_human_owned(cand, [9999, 8888]) is True

    def test_handles_string_owner_id(self):
        cand = {'owner': {'id': '9999'}}
        assert _is_human_owned(cand, [9999]) is False


# ─── End-to-end detection tests ──────────────────────────────────────────────

class TestDetectNewApplicantsHumanOwnerSkip:

    def _setup(self, app):
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'screening_skip_human_owned', 'true')

    def test_human_owned_candidate_is_skipped(self, app):
        """Candidate owned by a recruiter (not in api_user_ids) must be
        excluded from the detection results — and `_should_skip_candidate`
        must NOT be called for them (proves the gate fires first)."""
        with app.app_context():
            self._setup(app)
            human_owned = _make_candidate(cid=1001, owner_id=777,
                                          owner_name='Bob Smith')
            api_owned = _make_candidate(cid=1002, owner_id=9999,
                                        owner_name='Pandologic API')

            bh = _make_bullhorn([human_owned, api_owned])
            det = _Detector(bullhorn=bh)
            results = det.detect_new_applicants(since_minutes=5)

            result_ids = [c['id'] for c in results]
            assert 1001 not in result_ids, (
                "Human-owned candidate 1001 must not be re-screened"
            )
            assert 1002 in result_ids, (
                "API-owned candidate 1002 must still be screened"
            )
            # The dedup check must only have fired for the API-owned
            # candidate, never for the human-owned one.
            assert det._skip_calls == [1002]

    def test_owner_in_api_user_ids_is_screened(self, app):
        """Sanity: a candidate whose owner IS an API user (legitimate
        Pandologic / Matador / Myticas inbound) is included normally."""
        with app.app_context():
            self._setup(app)
            cand = _make_candidate(cid=2001, owner_id=9999)
            bh = _make_bullhorn([cand])
            det = _Detector(bullhorn=bh)
            results = det.detect_new_applicants(since_minutes=5)

            assert [c['id'] for c in results] == [2001]
            assert det._skip_calls == [2001]

    def test_candidate_with_no_owner_is_screened(self, app):
        """A candidate with no `owner` block (legacy/unowned) must NOT be
        skipped — only an explicit human owner triggers the skip."""
        with app.app_context():
            self._setup(app)
            cand = _make_candidate(cid=3001, owner_id=None)
            assert 'owner' not in cand
            bh = _make_bullhorn([cand])
            det = _Detector(bullhorn=bh)
            results = det.detect_new_applicants(since_minutes=5)

            assert [c['id'] for c in results] == [3001]

    def test_kill_switch_off_screens_human_owned_candidates(self, app):
        """When the operator flips the kill switch off, the gate becomes a
        no-op and human-owned candidates fall through to the dedup check."""
        with app.app_context():
            _ensure_config(app, 'api_user_ids', '9999')
            _ensure_config(app, 'screening_skip_human_owned', 'false')

            cand = _make_candidate(cid=4001, owner_id=777)
            bh = _make_bullhorn([cand])
            det = _Detector(bullhorn=bh)
            results = det.detect_new_applicants(since_minutes=5)

            # Kill switch off → human-owned candidate is screened.
            assert [c['id'] for c in results] == [4001]
            assert det._skip_calls == [4001]

    def test_no_api_user_ids_disables_skip(self, app):
        """If the operator hasn't configured `api_user_ids` yet, the gate
        must be a no-op (refuse to guess which IDs are API accounts)."""
        with app.app_context():
            _ensure_config(app, 'api_user_ids', '')
            _ensure_config(app, 'screening_skip_human_owned', 'true')

            cand = _make_candidate(cid=5001, owner_id=777)
            bh = _make_bullhorn([cand])
            det = _Detector(bullhorn=bh)
            results = det.detect_new_applicants(since_minutes=5)

            # No api_user_ids configured → can't classify "human" vs
            # "API"; fall through to legacy behavior.
            assert [c['id'] for c in results] == [5001]

    def test_multiple_api_user_ids_supported(self, app):
        """Production has 5 API user IDs configured; the gate must
        recognize ALL of them as "API-owned" (not just the first)."""
        with app.app_context():
            _ensure_config(
                app, 'api_user_ids',
                '4582015,4591841,4593767,4582033,1147490',
            )
            _ensure_config(app, 'screening_skip_human_owned', 'true')

            api_one = _make_candidate(cid=6001, owner_id=4593767)
            api_two = _make_candidate(cid=6002, owner_id=1147490)
            human = _make_candidate(cid=6003, owner_id=777)

            bh = _make_bullhorn([api_one, api_two, human])
            det = _Detector(bullhorn=bh)
            results = det.detect_new_applicants(since_minutes=5)

            result_ids = sorted(c['id'] for c in results)
            assert result_ids == [6001, 6002], (
                "Both API-owned candidates must be screened; human-owned "
                f"candidate must be skipped. Got: {result_ids}"
            )
