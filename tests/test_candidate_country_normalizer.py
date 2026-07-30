"""Regression tests for resume-backed Candidate country normalization."""
from unittest.mock import MagicMock, patch

from email_inbound_service.resume_mixin import ResumeMixin
from email_inbound_service.ai_mixin import AIMixin
from services.candidate_country_normalizer import (
    _country_ids_for_environment,
    _recent_candidates,
    normalize_candidate_country,
    normalize_candidates,
)
from utils.candidate_country import (
    bullhorn_country_payload,
    country_definition,
    infer_country_from_resume,
)


POOJA_RESUME = """
<p><strong>Pooja Dubey</strong></p>
<p><strong>Toronto, ON</strong> | +1 416 908 8137 | pooja@example.com</p>
<p><strong>Status:</strong> Canadian Citizen (Valid Canadian Passport Holder)</p>
<h4>Professional Summary</h4>
<p>Agile Project Manager with extensive Canadian banking experience.</p>
"""


class _ResumeMapper(ResumeMixin):
    SOURCE_TO_BULLHORN = {"LinkedIn": "LinkedIn Job Board"}
    WORK_AUTH_TO_VISA_TYPE = {}
    PANDO_FEED_SOURCE = "Corporate Website"


class _EnrichmentBuilder(AIMixin):
    logger = MagicMock()


def _candidate(**overrides):
    value = {
        "id": 4673235,
        "firstName": "Pooja",
        "lastName": "Dubey",
        "description": POOJA_RESUME,
        "address": {
            "city": "Toronto",
            "state": "ON",
            "countryID": 1,
            "countryCode": "US",
            "countryName": "United States",
        },
    }
    value.update(overrides)
    return value


def _bullhorn(readback_country="Canada", readback_id=2216):
    bh = MagicMock()
    bh.update_candidate.return_value = 4673235
    bh.get_candidate.return_value = {
        "id": 4673235,
        "address": {
            "city": "Toronto",
            "state": "ON",
            "countryID": readback_id,
            "countryName": readback_country,
        },
    }
    return bh


class TestCountryCatalog:
    def test_canada_uses_verified_bullhorn_entity_id(self):
        assert bullhorn_country_payload("Canada") == {
            "countryID": 2216,
            "countryName": "Canada",
            "countryCode": "CA",
        }

    def test_country_lookup_accepts_name_code_and_id(self):
        assert country_definition("Canada").bullhorn_id == 2216
        assert country_definition("CA").name == "Canada"
        assert country_definition(2216).name == "Canada"

    def test_unknown_country_is_not_guessed(self):
        assert bullhorn_country_payload("Atlantis") == {}


class TestResumeEvidence:
    def test_toronto_on_resume_resolves_to_canada(self):
        result = infer_country_from_resume("Toronto", "ON", POOJA_RESUME)
        assert result is not None
        assert result.country.name == "Canada"
        assert result.country.bullhorn_id == 2216
        assert result.confidence == "high"
        assert "state='ON'" in result.evidence
        assert "@" not in result.evidence
        assert "416" not in result.evidence

    def test_bullhorn_location_not_present_in_resume_is_not_enough(self):
        result = infer_country_from_resume(
            "Toronto",
            "ON",
            "Jane Doe\nNew York, NY\nSoftware Engineer",
        )
        assert result is None

    def test_on_does_not_match_the_ordinary_word_on(self):
        result = infer_country_from_resume(
            "",
            "ON",
            "Jane Doe\nWorked on enterprise systems\nProject Manager",
        )
        assert result is None

    def test_citizenship_is_not_residency_evidence(self):
        result = infer_country_from_resume(
            "",
            "",
            "Jane Doe\nNew York, NY\nCanadian Citizen",
        )
        assert result is None

    def test_explicit_header_country_can_populate_blank_address_country(self):
        result = infer_country_from_resume(
            "",
            "",
            "Jane Doe\nLocation: Toronto, Ontario, Canada\njane@example.com",
        )
        assert result is not None
        assert result.country.name == "Canada"

    def test_education_country_does_not_populate_blank_address_country(self):
        result = infer_country_from_resume(
            "",
            "",
            "Jane Doe\nEducation: University of Toronto, Canada\nProject Manager",
        )
        assert result is None

    def test_ambiguous_wa_without_explicit_country_is_not_written(self):
        result = infer_country_from_resume(
            "Perth",
            "WA",
            "Jane Doe\nPerth, WA\nSoftware Engineer",
        )
        assert result is None

    def test_ambiguous_wa_with_explicit_country_is_safe(self):
        result = infer_country_from_resume(
            "Perth",
            "WA",
            "Jane Doe\nPerth, WA, Australia\nSoftware Engineer",
        )
        assert result is not None
        assert result.country.name == "Australia"

    def test_unrelated_country_in_work_history_does_not_override_location(self):
        result = infer_country_from_resume(
            "Austin",
            "TX",
            "Jane Doe\nAustin, TX\nPreviously worked for Canada Systems Inc.",
        )
        assert result is not None
        assert result.country.name == "United States"


class TestInboundMapping:
    def test_parsed_country_sends_country_id_not_name_only(self):
        mixin = _ResumeMapper()
        payload = mixin.map_to_bullhorn_fields(
            {"first_name": "Pooja", "last_name": "Dubey"},
            {"city": "Toronto", "state": "ON", "country": "Canada"},
            source="LinkedIn",
        )
        assert payload["address"]["countryID"] == 2216
        assert payload["address"]["countryCode"] == "CA"
        assert payload["address"]["countryName"] == "Canada"

    def test_location_evidence_overrides_incorrect_ai_country(self):
        mixin = _ResumeMapper()
        payload = mixin.map_to_bullhorn_fields(
            {"first_name": "Pooja", "last_name": "Dubey"},
            {
                "city": "Toronto",
                "state": "ON",
                "country": "United States",
                "raw_text": "Pooja Dubey\nToronto, ON\nProject Manager",
            },
            source="LinkedIn",
        )
        assert payload["address"]["countryID"] == 2216
        assert payload["address"]["countryName"] == "Canada"

    def test_unknown_parser_country_is_omitted_without_verified_id(self):
        mixin = _ResumeMapper()
        payload = mixin.map_to_bullhorn_fields(
            {"first_name": "Test", "last_name": "Person"},
            {"country": "Atlantis"},
            source="Other",
        )
        assert payload["address"] == {}

    def test_returning_applicant_wrong_default_country_is_corrected(self):
        builder = _EnrichmentBuilder()
        update = builder._build_enrichment_update(
            {
                "address": {
                    "city": "Toronto",
                    "state": "ON",
                    "countryID": 1,
                    "countryName": "United States",
                }
            },
            {
                "address": {
                    "city": "Toronto",
                    "state": "ON",
                    "countryID": 2216,
                    "countryName": "Canada",
                },
                "description": POOJA_RESUME,
            },
        )
        assert update["address"]["countryID"] == 2216

    def test_returning_applicant_country_is_not_overwritten_from_ai_alone(self):
        builder = _EnrichmentBuilder()
        update = builder._build_enrichment_update(
            {
                "address": {
                    "city": "New York",
                    "state": "NY",
                    "countryID": 1,
                    "countryName": "United States",
                }
            },
            {
                "address": {
                    "countryID": 2216,
                    "countryName": "Canada",
                },
                "description": "Jane Doe\nSoftware Engineer\nCanadian Citizen",
            },
        )
        assert "address" not in update


class TestNormalizerWrites:
    @patch("services.candidate_country_normalizer._audit")
    def test_corrects_wrong_us_default_and_verifies_readback(self, audit):
        bh = _bullhorn()
        result = normalize_candidate_country(_candidate(), bh)

        assert result["status"] == "corrected"
        bh.update_candidate.assert_called_once_with(
            4673235,
            {
                "address": {
                    "city": "Toronto",
                    "state": "ON",
                    "countryID": 2216,
                }
            },
        )
        bh.get_candidate.assert_called_once_with(4673235)
        assert audit.call_args.kwargs["status"] == "corrected"

    @patch("services.candidate_country_normalizer._audit")
    def test_correct_country_is_idempotent(self, audit):
        candidate = _candidate(
            address={
                "city": "Toronto",
                "state": "ON",
                "countryID": 2216,
                "countryName": "Canada",
            }
        )
        bh = _bullhorn()
        result = normalize_candidate_country(candidate, bh)

        assert result == {"status": "unchanged", "country": "Canada"}
        bh.update_candidate.assert_not_called()
        audit.assert_not_called()

    @patch("services.candidate_country_normalizer._audit")
    def test_correct_name_without_canonical_id_is_repaired(self, audit):
        candidate = _candidate(
            address={
                "city": "Toronto",
                "state": "ON",
                "countryName": "Canada",
            }
        )
        bh = _bullhorn()
        result = normalize_candidate_country(candidate, bh)

        assert result["status"] == "corrected"
        bh.update_candidate.assert_called_once()

    @patch("services.candidate_country_normalizer._audit")
    def test_contradictory_readback_name_does_not_mask_wrong_id(self, audit):
        bh = _bullhorn(readback_country="Canada", readback_id=1)
        result = normalize_candidate_country(_candidate(), bh)

        assert result["status"] == "failed"
        assert "expected ID 2216" in result["error"]

    @patch("services.candidate_country_normalizer._audit")
    def test_dry_run_never_writes(self, audit):
        bh = _bullhorn()
        result = normalize_candidate_country(_candidate(), bh, dry_run=True)

        assert result["status"] == "dry_run"
        bh.update_candidate.assert_not_called()
        bh.get_candidate.assert_not_called()
        audit.assert_not_called()

    @patch("services.candidate_country_normalizer._audit")
    def test_failed_readback_is_a_failed_correction(self, audit):
        bh = _bullhorn(readback_country="United States", readback_id=1)
        result = normalize_candidate_country(_candidate(), bh)

        assert result["status"] == "failed"
        assert "verification failed" in result["error"]
        assert audit.call_args.kwargs["status"] == "failed"

    @patch("services.candidate_country_normalizer._audit")
    def test_update_failure_is_audited(self, audit):
        bh = _bullhorn()
        bh.update_candidate.return_value = None
        result = normalize_candidate_country(_candidate(), bh)

        assert result["status"] == "failed"
        assert audit.call_args.kwargs["status"] == "failed"

    @patch(
        "services.candidate_country_normalizer._audit",
        return_value=False,
    )
    def test_successful_remote_write_with_failed_audit_is_not_reported_clean(self, audit):
        bh = _bullhorn()
        result = normalize_candidate_country(_candidate(), bh)

        assert result["status"] == "corrected_unlogged"
        assert "audit persistence failed" in result["error"]

    def test_batch_metrics_distinguish_changed_and_skipped(self):
        bh = _bullhorn()
        skipped = _candidate(
            id=9,
            description="Candidate\nBoston, MA",
            address={"city": "Toronto", "state": "ON", "countryName": "United States"},
        )
        with patch("services.candidate_country_normalizer._audit"):
            result = normalize_candidates([_candidate(), skipped], bh)
        assert result["scanned"] == 2
        assert result["corrected"] == 1
        assert result["skipped"] == 1


class TestRecentCandidateCollection:
    @patch("services.candidate_country_normalizer.requests.get")
    def test_search_is_bounded_and_excludes_archived_records(self, get):
        now_ms = 1_785_400_000_000
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [{"id": 1, "dateAdded": now_ms}],
            "total": 1,
        }
        get.return_value = response
        bh = MagicMock(base_url="https://rest.example/")
        bh.rest_token = "token"

        with patch(
            "services.candidate_country_normalizer._lookback_floor_ms",
            return_value=now_ms - 86_400_000,
        ):
            rows = _recent_candidates(bh, lookback_hours=48, limit=5000)

        assert rows == [{"id": 1, "dateAdded": now_ms}]
        params = get.call_args.kwargs["params"]
        assert params["count"] == 500
        assert "dateAdded:[" in params["query"]
        assert "-status:Archive" in params["query"]
        assert "isDeleted:0" in params["query"]
        assert params["sort"] == "-dateAdded"
        assert "description" in params["fields"]

    @patch("services.candidate_country_normalizer.requests.get")
    def test_search_reauthenticates_once_after_401(self, get):
        rejected = MagicMock(status_code=401)
        accepted = MagicMock(status_code=200)
        accepted.raise_for_status.return_value = None
        accepted.json.return_value = {"data": [], "total": 0}
        get.side_effect = [rejected, accepted]
        bh = MagicMock(base_url="https://rest.example/")
        bh.rest_token = "rejected-token"

        def authenticate():
            bh.rest_token = "fresh-token"
            return True

        bh.authenticate.side_effect = authenticate
        with patch(
            "services.candidate_country_normalizer._lookback_floor_ms",
            return_value=1_785_000_000_000,
        ):
            assert _recent_candidates(bh, lookback_hours=24, limit=10) == []

        assert get.call_count == 2
        bh.authenticate.assert_called_once()
        assert get.call_args.kwargs["headers"]["BhRestToken"] == "fresh-token"

    @patch("services.candidate_country_normalizer.requests.get")
    def test_search_pages_so_older_candidates_are_not_starved(self, get):
        floor_ms = 1_785_000_000_000
        first = MagicMock(status_code=200)
        first.raise_for_status.return_value = None
        first.json.return_value = {
            "total": 600,
            "data": [
                {"id": value, "dateAdded": floor_ms + value}
                for value in range(500)
            ],
        }
        second = MagicMock(status_code=200)
        second.raise_for_status.return_value = None
        second.json.return_value = {
            "total": 600,
            "data": [
                {"id": value, "dateAdded": floor_ms + value}
                for value in range(500, 600)
            ],
        }
        get.side_effect = [first, second]
        bh = MagicMock(base_url="https://rest.example/", rest_token="token")

        with patch(
            "services.candidate_country_normalizer._lookback_floor_ms",
            return_value=floor_ms,
        ):
            # In-window cursor forces ascending catch-up pagination.
            rows = _recent_candidates(
                bh,
                lookback_hours=48,
                limit=1000,
                since_ms=floor_ms + 10,
            )

        assert len(rows) == 600
        assert get.call_count == 2
        assert get.call_args_list[0].kwargs["params"]["sort"] == "dateAdded"
        assert get.call_args_list[0].kwargs["params"]["start"] == 0
        assert get.call_args_list[1].kwargs["params"]["start"] == 500

    @patch("services.candidate_country_normalizer.requests.get")
    def test_stale_pre_lookback_cursor_is_ignored(self, get):
        floor_ms = 1_785_000_000_000
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "total": 1,
            "data": [{"id": 42, "dateAdded": floor_ms + 5}],
        }
        get.return_value = response
        bh = MagicMock(base_url="https://rest.example/", rest_token="token")

        with patch(
            "services.candidate_country_normalizer._lookback_floor_ms",
            return_value=floor_ms,
        ):
            rows = _recent_candidates(
                bh,
                lookback_hours=48,
                limit=100,
                since_ms=950_162_400_000,  # year 2000 poison cursor
            )

        assert rows == [{"id": 42, "dateAdded": floor_ms + 5}]
        params = get.call_args.kwargs["params"]
        assert params["sort"] == "-dateAdded"
        assert f"dateAdded:[{floor_ms} TO *]" in params["query"]

    @patch("services.candidate_country_normalizer.requests.get")
    def test_out_of_window_rows_trigger_newest_first_fallback(self, get):
        floor_ms = 1_785_000_000_000
        ancient = MagicMock(status_code=200)
        ancient.raise_for_status.return_value = None
        ancient.json.return_value = {
            "total": 1000,
            "data": [
                {"id": 1, "dateAdded": 950_162_400_000},
                {"id": 2, "dateAdded": 950_162_401_000},
            ],
        }
        recent = MagicMock(status_code=200)
        recent.raise_for_status.return_value = None
        recent.json.return_value = {
            "total": 1,
            "data": [{"id": 99, "dateAdded": floor_ms + 100}],
        }
        get.side_effect = [ancient, recent]
        bh = MagicMock(base_url="https://rest.example/", rest_token="token")

        with patch(
            "services.candidate_country_normalizer._lookback_floor_ms",
            return_value=floor_ms,
        ):
            rows = _recent_candidates(bh, lookback_hours=48, limit=100)

        assert rows == [{"id": 99, "dateAdded": floor_ms + 100}]
        assert get.call_count == 2
        assert get.call_args_list[1].kwargs["params"]["sort"] == "-dateAdded"
        assert (
            f"dateAdded:[{floor_ms} TO *]"
            in get.call_args_list[1].kwargs["params"]["query"]
        )


class TestEnvironmentCountryIds:
    def test_environment_options_override_static_country_id(self):
        bh = MagicMock()
        bh.get_options.return_value = [
            {"label": "Canada", "value": 9999},
        ]
        country_ids = _country_ids_for_environment(bh)
        assert country_ids["Canada"] == 9999
        assert country_ids["United States"] == 1
