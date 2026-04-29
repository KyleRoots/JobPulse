"""
Tests for PromptBuilderMixin._reverify_zero_score().

Covers:
- Correct function signature (3 required + 3 optional params)
- Return dict uses revised_score / revised_summary / revised_gaps /
  revision_reason / confidence_reason keys
- Prior-context block is included in the prompt when supplied
- Global requirements block is included when supplied
- Returns None gracefully when OpenAI raises
- revised_score is coerced to int
"""

import json
import types
import unittest
from unittest.mock import MagicMock, patch


def _make_mixin():
    """Return a minimal PromptBuilderMixin instance with a mocked openai_client."""
    from screening.prompt_builder import PromptBuilderMixin

    class _Svc(PromptBuilderMixin):
        def __init__(self):
            self.openai_client = MagicMock()

    return _Svc()


def _mock_openai_response(payload: dict):
    """Build a fake OpenAI chat completion response returning *payload* as JSON."""
    choice = MagicMock()
    choice.message.content = json.dumps(payload)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestReverifyZeroScoreSignature(unittest.TestCase):
    """Verify the function accepts the full call-site argument list."""

    def test_accepts_all_positional_args(self):
        svc = _make_mixin()
        svc.openai_client.chat.completions.create.return_value = _mock_openai_response({
            "revised_score": 45,
            "revised_summary": "Candidate has relevant Python experience.",
            "revised_gaps": "No cloud experience.",
            "revision_reason": "Initial screen missed Python skills.",
            "confidence_reason": "High confidence."
        })

        result = svc._reverify_zero_score(
            "Python developer with 5 years experience",
            {"title": "Software Engineer", "description": "Python required"},
            {"city": "Austin", "state": "TX"},
            "Candidate seems off-topic",
            "No Python mentioned",
            "Must have Python 3.x"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["revised_score"], 45)

    def test_accepts_minimal_args(self):
        svc = _make_mixin()
        svc.openai_client.chat.completions.create.return_value = _mock_openai_response({
            "revised_score": 0,
        })

        result = svc._reverify_zero_score(
            "Some resume text",
            {"title": "Data Analyst"}
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["revised_score"], 0)


class TestReverifyZeroScoreReturnKeys(unittest.TestCase):
    """Verify return dict contains all keys the call site reads."""

    REQUIRED_KEYS = {
        "revised_score",
        "revised_summary",
        "revised_gaps",
        "revision_reason",
        "confidence_reason",
    }

    def _call(self, payload):
        svc = _make_mixin()
        svc.openai_client.chat.completions.create.return_value = (
            _mock_openai_response(payload)
        )
        return svc._reverify_zero_score("resume", {"title": "Job"})

    def test_full_payload_all_keys_present(self):
        result = self._call({
            "revised_score": 30,
            "revised_summary": "Some match.",
            "revised_gaps": "Missing X.",
            "revision_reason": "Found relevant skills.",
            "confidence_reason": "Medium confidence."
        })
        for key in self.REQUIRED_KEYS:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_partial_payload_defaults_filled(self):
        result = self._call({"revised_score": 0})
        for key in self.REQUIRED_KEYS:
            self.assertIn(key, result, f"Missing key: {key}")
        self.assertEqual(result["revised_summary"], "")
        self.assertEqual(result["revised_gaps"], "")
        self.assertEqual(result["revision_reason"], "")
        self.assertEqual(result["confidence_reason"], "")

    def test_revised_score_coerced_to_int(self):
        result = self._call({"revised_score": "72"})
        self.assertIsInstance(result["revised_score"], int)
        self.assertEqual(result["revised_score"], 72)


class TestReverifyZeroScorePromptContent(unittest.TestCase):
    """Verify that optional context blocks appear in the prompt when supplied."""

    def _capture_prompt(self, **kwargs):
        svc = _make_mixin()
        svc.openai_client.chat.completions.create.return_value = _mock_openai_response(
            {"revised_score": 0}
        )
        svc._reverify_zero_score("resume text", {"title": "Job"}, **kwargs)
        call_args = svc.openai_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args.args[0]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        return user_msg

    def test_prior_context_included_when_supplied(self):
        prompt = self._capture_prompt(
            prior_summary="Candidate seems off-topic",
            prior_gaps="No relevant tools listed"
        )
        self.assertIn("Prior analysis", prompt)
        self.assertIn("Candidate seems off-topic", prompt)
        self.assertIn("No relevant tools listed", prompt)

    def test_prior_context_omitted_when_empty(self):
        prompt = self._capture_prompt(prior_summary="", prior_gaps="")
        self.assertNotIn("Prior analysis", prompt)

    def test_global_requirements_included_when_supplied(self):
        prompt = self._capture_prompt(global_requirements="Must have active Top Secret clearance")
        self.assertIn("Global screening requirements", prompt)
        self.assertIn("Must have active Top Secret clearance", prompt)

    def test_global_requirements_omitted_when_none(self):
        prompt = self._capture_prompt(global_requirements=None)
        self.assertNotIn("Global screening requirements", prompt)


class TestReverifyZeroScoreErrorHandling(unittest.TestCase):
    """Verify graceful None return on OpenAI failure."""

    def test_returns_none_on_openai_exception(self):
        svc = _make_mixin()
        svc.openai_client.chat.completions.create.side_effect = RuntimeError("API down")
        result = svc._reverify_zero_score("resume", {"title": "Job"})
        self.assertIsNone(result)

    def test_returns_none_on_json_decode_error(self):
        svc = _make_mixin()
        bad_resp = MagicMock()
        bad_resp.choices[0].message.content = "not valid json {{{"
        svc.openai_client.chat.completions.create.return_value = bad_resp
        result = svc._reverify_zero_score("resume", {"title": "Job"})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
