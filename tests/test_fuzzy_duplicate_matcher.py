"""Tests for the AI Fuzzy Duplicate Matcher (Task #57).

Covers the embedding-based pre-filter and the GPT-5.4 final scoring layer
that catches duplicate candidates whose email AND phone both changed.
The actual OpenAI calls are mocked so the suite stays offline and fast.
"""

import json
from unittest.mock import MagicMock

import pytest

from fuzzy_duplicate_matcher import (
    AI_CONFIDENCE_THRESHOLD,
    PRE_FILTER_COSINE_THRESHOLD,
    FuzzyDuplicateMatcher,
)


# ── Profile text builder ────────────────────────────────────────────────────

def _make_matcher(**overrides):
    """Build a matcher with a stub bullhorn + injectable embedding/openai."""
    bh = MagicMock()
    bh.base_url = 'https://example.invalid/'
    bh.rest_token = 'test-token'

    embedding_service = overrides.pop('embedding_service', MagicMock())
    embedding_service.embedding_model = 'text-embedding-3-large'

    openai_client = overrides.pop('openai_client', MagicMock())

    return FuzzyDuplicateMatcher(
        bullhorn_service=bh,
        embedding_service=embedding_service,
        openai_client=openai_client,
        **overrides,
    )


def test_build_profile_text_includes_all_required_sections():
    """The profile text must include name + work history + skills + location +
    education per the Task #57 spec."""
    matcher = _make_matcher()

    candidate = {
        'firstName': 'Jane',
        'lastName': 'Doe',
        'occupation': 'Senior Engineer',
        'companyName': 'Acme Corp',
        'skillSet': 'Python, Kubernetes, AWS',
        'address': {'city': 'Austin', 'state': 'TX', 'countryName': 'United States'},
    }
    work_history = [
        {'companyName': 'Acme', 'title': 'Senior Engineer',
         'startDate': 1577836800000, 'endDate': None, 'isLastJob': True},
        {'companyName': 'Beta Inc', 'title': 'Engineer',
         'startDate': 1483228800000, 'endDate': 1577836800000},
    ]
    education = [
        {'school': 'UT Austin', 'degree': 'BS', 'major': 'CS',
         'graduationDate': 1325376000000},
    ]

    text = matcher.build_profile_text(candidate, work_history, education)

    assert 'NAME: Jane Doe' in text
    assert 'LOCATION: Austin, TX, United States' in text
    assert 'CURRENT ROLE: Senior Engineer @ Acme Corp' in text
    assert 'SKILLS: Python, Kubernetes, AWS' in text
    assert 'WORK HISTORY:' in text
    assert 'Acme' in text and 'Beta Inc' in text
    assert 'EDUCATION:' in text
    assert 'UT Austin' in text


def test_profile_hash_is_deterministic_across_whitespace():
    """The hash must be stable across irrelevant whitespace differences so
    cache hits are not invalidated by formatting changes."""
    matcher = _make_matcher()
    a = matcher.compute_profile_hash("NAME: Jane Doe\nSKILLS: Python")
    b = matcher.compute_profile_hash("NAME:  Jane   Doe\n\nSKILLS:   Python")
    assert a == b


def test_profile_hash_changes_when_content_changes():
    matcher = _make_matcher()
    a = matcher.compute_profile_hash("NAME: Jane Doe\nSKILLS: Python")
    b = matcher.compute_profile_hash("NAME: Jane Doe\nSKILLS: Java")
    assert a != b


# ── Cosine pre-filter (Layer A) ─────────────────────────────────────────────

class _FakeRow:
    def __init__(self, candidate_id, vector, name='', snippet=''):
        self.bullhorn_candidate_id = candidate_id
        self.embedding_vector = json.dumps(vector)
        self.candidate_name = name
        self.profile_text_snippet = snippet


def _patch_embedding_table(monkeypatch, rows):
    """Patch ``models.CandidateProfileEmbedding`` so the matcher's in-function
    ``from models import CandidateProfileEmbedding`` returns a stub whose
    ``query.order_by(...).limit(...).all()`` chain yields ``rows``."""
    import models as models_module

    fake_query = MagicMock()
    fake_query.order_by.return_value.limit.return_value.all.return_value = rows
    fake_model = MagicMock()
    fake_model.query = fake_query
    fake_model.updated_at = MagicMock()
    monkeypatch.setattr(models_module, 'CandidateProfileEmbedding', fake_model)


def test_cosine_prefilter_returns_only_top_n_above_threshold(monkeypatch):
    matcher = _make_matcher(pre_filter_top_n=2)

    target = [1.0, 0.0, 0.0]
    rows = [
        _FakeRow(101, [1.0, 0.0, 0.0], 'Exact match'),       # sim=1.0
        _FakeRow(102, [0.95, 0.05, 0.0], 'Very close'),     # sim≈1.0
        _FakeRow(103, [0.6, 0.8, 0.0], 'Below threshold'),  # sim≈0.6 < 0.75
        _FakeRow(104, [0.85, 0.5, 0.0], 'Just above'),       # sim≈0.86
    ]
    _patch_embedding_table(monkeypatch, rows)

    results = matcher.find_top_candidates_by_cosine(target, exclude_ids=[])

    # Only the top 2 hits >= 0.75 should be returned, sorted desc.
    # Similarities: 101→1.000, 102→0.998, 104→0.862, 103→0.600 (filtered).
    assert len(results) == 2
    assert results[0][0] == 101
    assert results[1][0] == 102
    # Below-threshold row never appears
    assert all(r[0] != 103 for r in results)
    # Below-top-N row never appears
    assert all(r[0] != 104 for r in results)
    # Sorted descending by similarity
    assert results[0][1] >= results[1][1]


def test_cosine_prefilter_excludes_target_id(monkeypatch):
    matcher = _make_matcher()
    target = [1.0, 0.0, 0.0]
    rows = [
        _FakeRow(100, [1.0, 0.0, 0.0]),  # would match perfectly but excluded
        _FakeRow(200, [0.99, 0.01, 0.0]),
    ]
    _patch_embedding_table(monkeypatch, rows)

    results = matcher.find_top_candidates_by_cosine(target, exclude_ids=[100])
    ids = [r[0] for r in results]
    assert 100 not in ids
    assert 200 in ids


# ── GPT scoring (Layer B) ───────────────────────────────────────────────────

def _stub_openai_response(content):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def test_score_pair_with_ai_parses_json_response():
    client = _stub_openai_response(
        '{"confidence": 0.92, "reasoning": "Same employer + dates + city"}'
    )
    matcher = _make_matcher(openai_client=client)
    confidence, reasoning = matcher.score_pair_with_ai("profile A text", "profile B text")
    assert confidence == 0.92
    assert "Same employer" in reasoning


def test_score_pair_with_ai_strips_markdown_fences():
    client = _stub_openai_response(
        '```json\n{"confidence": 0.88, "reasoning": "Strong match"}\n```'
    )
    matcher = _make_matcher(openai_client=client)
    confidence, _ = matcher.score_pair_with_ai("a", "b")
    assert confidence == 0.88


def test_score_pair_with_ai_returns_zero_on_invalid_json():
    client = _stub_openai_response('not valid json at all')
    matcher = _make_matcher(openai_client=client)
    confidence, reasoning = matcher.score_pair_with_ai("a", "b")
    assert confidence == 0.0
    assert reasoning  # should carry an error message


def test_score_pair_with_ai_clamps_to_unit_range():
    client = _stub_openai_response('{"confidence": 1.5, "reasoning": "x"}')
    matcher = _make_matcher(openai_client=client)
    confidence, _ = matcher.score_pair_with_ai("a", "b")
    assert 0.0 <= confidence <= 1.0


def test_score_pair_with_ai_returns_zero_for_empty_profiles():
    matcher = _make_matcher()
    confidence, _ = matcher.score_pair_with_ai("", "non-empty")
    assert confidence == 0.0


# ── End-to-end find_fuzzy_duplicates ────────────────────────────────────────

def test_find_fuzzy_duplicates_only_returns_pairs_above_threshold(monkeypatch):
    """Cosine prefilter returns 2 candidates; AI scores one above and one
    below the AI confidence threshold. Only the high-scoring one is kept."""
    matcher = _make_matcher(ai_confidence_threshold=0.90)

    target_candidate = {'id': 999, 'firstName': 'Jane', 'lastName': 'Doe'}

    # Force get_or_create_profile_embedding to return a usable target vector
    monkeypatch.setattr(
        matcher,
        'get_or_create_profile_embedding',
        lambda c: ([1.0, 0.0, 0.0], 'NAME: Jane Doe\nSKILLS: Python'),
    )

    # Force the cosine prefilter to return two candidate ids
    monkeypatch.setattr(
        matcher,
        'find_top_candidates_by_cosine',
        lambda vec, exclude_ids=None: [
            (101, 0.95, 'Jane D', 'snippet'),
            (102, 0.85, 'John D', 'snippet'),
        ],
    )

    # Each fetched "other" candidate is a stub
    monkeypatch.setattr(
        matcher,
        '_fetch_full_candidate',
        lambda cid: {'id': cid, 'firstName': 'X', 'lastName': 'Y'},
    )
    monkeypatch.setattr(matcher, '_fetch_work_history', lambda cid: [])
    monkeypatch.setattr(matcher, '_fetch_education', lambda cid: [])
    monkeypatch.setattr(
        matcher, 'build_profile_text',
        lambda c, wh, edu: f"NAME: candidate-{c.get('id')}",
    )

    # AI returns 0.95 for id 101, 0.70 for id 102
    scores = {101: 0.95, 102: 0.70}

    def fake_score(profile_a, profile_b):
        # parse the candidate id out of "NAME: candidate-XXX"
        for cid in scores:
            if str(cid) in profile_b:
                return scores[cid], 'mocked'
        return 0.0, 'no match'

    monkeypatch.setattr(matcher, 'score_pair_with_ai', fake_score)

    hits = matcher.find_fuzzy_duplicates(target_candidate)
    ids = [h['candidate_id'] for h in hits]
    confidences = {h['candidate_id']: h['confidence'] for h in hits}

    assert ids == [101], f"only the >={AI_CONFIDENCE_THRESHOLD} pair should be returned, got {ids}"
    assert confidences[101] == 0.95


def test_find_fuzzy_duplicates_returns_empty_when_no_target_embedding(monkeypatch):
    matcher = _make_matcher()
    monkeypatch.setattr(
        matcher, 'get_or_create_profile_embedding', lambda c: (None, None)
    )
    assert matcher.find_fuzzy_duplicates({'id': 1}) == []


def test_find_fuzzy_duplicates_returns_empty_when_no_cosine_hits(monkeypatch):
    matcher = _make_matcher()
    monkeypatch.setattr(
        matcher, 'get_or_create_profile_embedding',
        lambda c: ([1.0, 0.0, 0.0], 'profile'),
    )
    monkeypatch.setattr(matcher, 'find_top_candidates_by_cosine', lambda v, exclude_ids=None: [])
    assert matcher.find_fuzzy_duplicates({'id': 1}) == []


def test_find_fuzzy_duplicates_skips_archived_candidates(monkeypatch):
    """An archived Bullhorn candidate must never be returned as a fuzzy hit
    even if its embedding is still in the cache (mirrors the exact-match
    path's ``-status:Archive`` filter)."""
    matcher = _make_matcher()
    monkeypatch.setattr(
        matcher, 'get_or_create_profile_embedding',
        lambda c: ([1.0, 0.0, 0.0], 'target profile'),
    )
    monkeypatch.setattr(
        matcher, 'find_top_candidates_by_cosine',
        lambda v, exclude_ids=None: [(50, 0.95, 'Old Name', 'snippet')],
    )

    monkeypatch.setattr(
        matcher, '_fetch_full_candidate',
        lambda cid: {'id': cid, 'firstName': 'Stale', 'lastName': 'Person',
                     'status': 'Archive'},
    )

    # Embed/score should never be invoked for archived candidates.
    monkeypatch.setattr(
        matcher, '_fetch_work_history',
        lambda cid: (_ for _ in ()).throw(AssertionError("must not fetch wh for archived")),
    )
    monkeypatch.setattr(
        matcher, 'score_pair_with_ai',
        lambda a, b: (_ for _ in ()).throw(AssertionError("must not score archived pair")),
    )

    accepted = matcher.find_fuzzy_duplicates({'id': 99})
    assert accepted == []


def test_find_fuzzy_duplicates_excludes_target_and_already_merged(monkeypatch):
    """The exclude_ids passed in (e.g. already_merged) AND the target id
    must both be filtered out before the cosine pre-filter."""
    matcher = _make_matcher()
    monkeypatch.setattr(
        matcher, 'get_or_create_profile_embedding',
        lambda c: ([1.0, 0.0, 0.0], 'profile'),
    )

    captured = {}

    def fake_top(vec, exclude_ids=None):
        captured['exclude_ids'] = set(exclude_ids or [])
        return []

    monkeypatch.setattr(matcher, 'find_top_candidates_by_cosine', fake_top)
    matcher.find_fuzzy_duplicates({'id': 999}, exclude_ids={1, 2, 3})

    assert captured['exclude_ids'] == {1, 2, 3, 999}, \
        "Both already-merged ids and the target id must be excluded"


# ── Constants sanity checks ─────────────────────────────────────────────────

def test_ai_threshold_is_higher_than_exact_path():
    """Per task spec: AI threshold ~0.90+ vs exact path's 0.80."""
    from duplicate_merge_service import CONFIDENCE_THRESHOLD
    assert AI_CONFIDENCE_THRESHOLD >= 0.90
    assert AI_CONFIDENCE_THRESHOLD > CONFIDENCE_THRESHOLD


def test_pre_filter_threshold_is_below_ai_threshold():
    """Cosine pre-filter must be lenient enough to feed the AI layer."""
    assert PRE_FILTER_COSINE_THRESHOLD < AI_CONFIDENCE_THRESHOLD


# ── Cold-start backfill (historical coverage) ───────────────────────────────

def _patch_cached_ids(monkeypatch, ids):
    """Stub out the ``CandidateProfileEmbedding.query`` chain used to read
    already-cached candidate IDs.

    The backfill uses a per-page existence check
    (``.with_entities(col).filter(col.in_(page_ids)).all()``). We don't
    bother decoding the SQLAlchemy IN clause in the stub — returning the
    full cached set is safe because the backfill only uses it for a
    ``cid in cached_ids`` membership test.
    """
    cached_rows = [(i,) for i in ids]
    fake_filter = MagicMock()
    fake_filter.all.return_value = cached_rows
    fake_with_entities = MagicMock()
    fake_with_entities.filter.return_value = fake_filter
    fake_with_entities.all.return_value = cached_rows
    fake_query = MagicMock()
    fake_query.with_entities.return_value = fake_with_entities
    fake_model = MagicMock()
    fake_model.query = fake_query
    fake_model.bullhorn_candidate_id = MagicMock()
    import models as models_module
    monkeypatch.setattr(models_module, 'CandidateProfileEmbedding', fake_model)


def _patch_cursor_store(monkeypatch, initial=0):
    """Stub the GlobalSettings cursor store with a simple in-test dict so we
    can verify the cursor advances across cycles."""
    state = {'value': initial}
    fake_gs = MagicMock()
    fake_gs.get_value = lambda key, default=None: str(state['value'])
    def _set(key, value, description=None, category=None):
        state['value'] = int(value)
    fake_gs.set_value = _set
    import models as models_module
    monkeypatch.setattr(models_module, 'GlobalSettings', fake_gs)
    return state


def test_backfill_skips_already_cached_ids_and_caps_at_limit(monkeypatch):
    """The rolling backfill must (a) skip candidates already in the cache,
    (b) embed up to ``limit`` brand-new ones, (c) stop early once the
    per-cycle cap is reached, and (d) advance the persistent cursor so
    the next cycle continues from where this one left off."""
    matcher = _make_matcher(
        backfill_per_cycle=3,
        backfill_page_size=10,
        backfill_max_pages=2,
    )

    _patch_cached_ids(monkeypatch, [1, 2])
    cursor_state = _patch_cursor_store(monkeypatch, initial=0)

    # Single page of 5 candidates; cap of 3 should be hit before exhausting it.
    pages_seen = []

    def fake_fetch(after_id, count):
        pages_seen.append(after_id)
        if after_id == 0:
            return [{'id': i} for i in [1, 2, 3, 4, 5]]
        return []

    monkeypatch.setattr(matcher, '_fetch_candidate_page_after', fake_fetch)

    embedded_ids = []

    def fake_embed(stub):
        embedded_ids.append(stub['id'])
        return ([0.1, 0.2, 0.3], 'profile-text')

    monkeypatch.setattr(matcher, 'get_or_create_profile_embedding', fake_embed)

    n = matcher.backfill_uncached_candidates()

    assert n == 3, "Should have embedded exactly the per-cycle cap"
    # 1 and 2 were cached, so backfill must have skipped them and embedded 3,4,5
    assert embedded_ids == [3, 4, 5]
    # Cursor should now point at the highest id seen on the page (5)
    assert cursor_state['value'] == 5
    # Only one page request — cap was reached without needing a second page
    assert pages_seen == [0]


def test_backfill_resumes_from_persistent_cursor_across_cycles(monkeypatch):
    """Multi-cycle progression: each cycle must resume from the cursor the
    previous cycle persisted — otherwise we'd re-scan the same early IDs
    forever and never reach the long tail. This is the core fix for the
    cold-start coverage gap."""
    matcher = _make_matcher(
        backfill_per_cycle=2,
        backfill_page_size=2,
        backfill_max_pages=1,
    )

    _patch_cached_ids(monkeypatch, [])
    cursor_state = _patch_cursor_store(monkeypatch, initial=0)

    # Simulate a 6-candidate corpus split across 3 logical "pages" of 2.
    def fake_fetch(after_id, count):
        all_ids = [10, 20, 30, 40, 50, 60]
        upcoming = [i for i in all_ids if i > after_id]
        return [{'id': i} for i in upcoming[:count]]

    monkeypatch.setattr(matcher, '_fetch_candidate_page_after', fake_fetch)

    embedded_per_cycle = []

    def fake_embed(stub):
        embedded_per_cycle[-1].append(stub['id'])
        return ([0.0, 0.0, 1.0], 'p')

    monkeypatch.setattr(matcher, 'get_or_create_profile_embedding', fake_embed)

    # Cycle 1: should embed 10, 20 and cursor -> 20
    embedded_per_cycle.append([])
    n1 = matcher.backfill_uncached_candidates()
    assert n1 == 2 and embedded_per_cycle[-1] == [10, 20]
    assert cursor_state['value'] == 20

    # Cycle 2: must resume from cursor=20, embed 30, 40, cursor -> 40
    embedded_per_cycle.append([])
    n2 = matcher.backfill_uncached_candidates()
    assert n2 == 2 and embedded_per_cycle[-1] == [30, 40]
    assert cursor_state['value'] == 40

    # Cycle 3: continues, embeds 50, 60, cursor -> 60
    embedded_per_cycle.append([])
    n3 = matcher.backfill_uncached_candidates()
    assert n3 == 2 and embedded_per_cycle[-1] == [50, 60]
    assert cursor_state['value'] == 60


def test_backfill_wraps_cursor_when_corpus_exhausted(monkeypatch):
    """When we walk past the end of the corpus, the cursor must wrap back
    to 0 so we eventually re-scan early IDs (catches new low-id candidates
    or rows that previously failed to embed)."""
    matcher = _make_matcher(
        backfill_per_cycle=1,
        backfill_page_size=5,
        backfill_max_pages=3,
    )

    # All 3 corpus IDs already cached so nothing actually gets embedded —
    # we just want to prove the cursor wraps when end-of-corpus is reached.
    _patch_cached_ids(monkeypatch, [10, 20, 30])
    cursor_state = _patch_cursor_store(monkeypatch, initial=25)

    fetch_calls = []

    def fake_fetch(after_id, count):
        fetch_calls.append(after_id)
        all_ids = [10, 20, 30]
        upcoming = [i for i in all_ids if i > after_id]
        return [{'id': i} for i in upcoming[:count]]

    monkeypatch.setattr(matcher, '_fetch_candidate_page_after', fake_fetch)
    monkeypatch.setattr(matcher, 'get_or_create_profile_embedding',
                        lambda stub: (_ for _ in ()).throw(AssertionError("nothing should be embedded")))

    matcher.backfill_uncached_candidates()

    # First fetch from cursor=25 finds [30], cursor advances to 30, no embed
    # (already cached). Next fetch from cursor=30 returns [] -> wrap to 0.
    # After wrap, fetch from cursor=0 returns [10,20,30] (all cached, no embed).
    assert fetch_calls[0] == 25
    assert 0 in fetch_calls, "Cursor should have wrapped back to 0 on exhaustion"


def test_backfill_advances_cursor_even_when_all_rows_cached(monkeypatch):
    """If a page returns rows that are all already cached, the cursor must
    still move forward to the page's max id — otherwise a cluster of
    cached rows would stall the cursor in a permanent loop."""
    matcher = _make_matcher(
        backfill_per_cycle=5,
        backfill_page_size=3,
        backfill_max_pages=1,
    )

    _patch_cached_ids(monkeypatch, [100, 101, 102])
    cursor_state = _patch_cursor_store(monkeypatch, initial=99)

    def fake_fetch(after_id, count):
        if after_id == 99:
            return [{'id': 100}, {'id': 101}, {'id': 102}]
        return []

    monkeypatch.setattr(matcher, '_fetch_candidate_page_after', fake_fetch)
    monkeypatch.setattr(matcher, 'get_or_create_profile_embedding',
                        lambda stub: (_ for _ in ()).throw(AssertionError("none should embed")))

    n = matcher.backfill_uncached_candidates()
    assert n == 0
    # Critical: cursor moved from 99 -> 102 even though nothing was embedded
    assert cursor_state['value'] == 102


def test_backfill_returns_zero_when_limit_is_zero(monkeypatch):
    matcher = _make_matcher(backfill_per_cycle=0)
    monkeypatch.setattr(matcher, '_fetch_candidate_page_after',
                        lambda a, c: (_ for _ in ()).throw(AssertionError("should not page")))
    assert matcher.backfill_uncached_candidates() == 0


def test_backfill_handles_db_failure_gracefully(monkeypatch):
    """If the cached-ids query blows up, backfill must return 0 cleanly
    rather than crashing the whole scheduled job."""
    matcher = _make_matcher()

    fake_model = MagicMock()
    fake_model.query.with_entities.return_value.all.side_effect = RuntimeError("db down")
    import models as models_module
    monkeypatch.setattr(models_module, 'CandidateProfileEmbedding', fake_model)

    n = matcher.backfill_uncached_candidates()
    assert n == 0


# ── Single-merge-per-source guarantee ───────────────────────────────────────

def test_fuzzy_pass_makes_only_one_merge_decision_per_source_candidate(monkeypatch):
    """Once a source candidate has been merged in this cycle, the inner
    hits loop must stop — preventing the same source from being merged
    into multiple targets in a single pass (data-integrity guarantee)."""
    from duplicate_merge_service import DuplicateMergeService

    svc = DuplicateMergeService()

    # Stub the fuzzy matcher to return TWO high-confidence hits for the
    # single source candidate. Only the first should result in a merge.
    fake_matcher = MagicMock()
    fake_matcher.backfill_uncached_candidates.return_value = 0
    fake_matcher.find_fuzzy_duplicates.return_value = [
        {
            'candidate_id': 200,
            'candidate_name': 'High Conf',
            'confidence': 0.97,
            'reasoning': 'top hit',
            'similarity': 0.96,
            'candidate': {'id': 200, 'firstName': 'A', 'lastName': 'B'},
        },
        {
            'candidate_id': 300,
            'candidate_name': 'Also Conf',
            'confidence': 0.94,
            'reasoning': 'second hit',
            'similarity': 0.92,
            'candidate': {'id': 300, 'firstName': 'C', 'lastName': 'D'},
        },
    ]

    import duplicate_merge_service as dms
    import fuzzy_duplicate_matcher as fdm_mod
    monkeypatch.setattr(fdm_mod, "FuzzyDuplicateMatcher",
                        lambda *a, **k: fake_matcher)

    # determine_primary picks the source as primary, target as duplicate.
    monkeypatch.setattr(svc, 'determine_primary',
                        lambda a, b: (a, b, 'newer placement'))

    merge_calls = []

    def fake_merge(primary, duplicate, confidence, match_field,
                   merge_type='scheduled', match_type='exact'):
        merge_calls.append((primary['id'], duplicate['id'], match_type))

    monkeypatch.setattr(svc, 'merge_candidates', fake_merge)
    monkeypatch.setattr(dms.time, 'sleep', lambda s: None)

    source = {'id': 100, 'firstName': 'Source', 'lastName': 'Person'}
    result = svc._run_fuzzy_matcher_pass(
        recent_candidates=[source],
        already_merged=set(),
        exact_matched=set(),
    )

    assert result['checked'] == 1
    assert result['merged'] == 1, "Must merge exactly once per source candidate"
    # Only the highest-confidence hit (200) should have been merged
    assert merge_calls == [(100, 200, 'ai_fuzzy')]


def test_fuzzy_pass_skips_source_already_merged_by_exact_pass(monkeypatch):
    """Candidates handled by Pass 1 (exact) must not be re-checked by
    Pass 2 (fuzzy) — it would waste an AI call and risk double-logging."""
    from duplicate_merge_service import DuplicateMergeService

    svc = DuplicateMergeService()

    fake_matcher = MagicMock()
    fake_matcher.backfill_uncached_candidates.return_value = 0
    fake_matcher.find_fuzzy_duplicates.return_value = []
    import duplicate_merge_service as dms
    import fuzzy_duplicate_matcher as fdm_mod
    monkeypatch.setattr(fdm_mod, "FuzzyDuplicateMatcher",
                        lambda *a, **k: fake_matcher)
    monkeypatch.setattr(dms.time, 'sleep', lambda s: None)

    candidates = [{'id': 11}, {'id': 22}, {'id': 33}]
    # 11 was already merged by exact; 22 was matched by exact (same effect)
    result = svc._run_fuzzy_matcher_pass(
        recent_candidates=candidates,
        already_merged={11},
        exact_matched={22},
    )

    # Only candidate 33 should reach the matcher
    assert fake_matcher.find_fuzzy_duplicates.call_count == 1
    assert fake_matcher.find_fuzzy_duplicates.call_args[0][0]['id'] == 33
    assert result['checked'] == 1


def test_fuzzy_pass_overflow_is_persisted_to_queue_and_drained_next_cycle(monkeypatch):
    """Sustained-load guarantee: candidates that exceed
    ``FUZZY_MAX_CANDIDATES_PER_CYCLE`` must be written to
    ``fuzzy_evaluation_queue`` so the next cycle drains them BEFORE
    looking at fresh recent candidates. Without this, a burst of recent
    activity could push older candidates out of the recent window before
    they're ever evaluated."""
    from duplicate_merge_service import DuplicateMergeService
    import duplicate_merge_service as dms
    import fuzzy_duplicate_matcher as fdm_mod
    from app import app, db
    from models import FuzzyEvaluationQueue

    with app.app_context():
        # Clean slate so prior tests don't pollute the queue.
        FuzzyEvaluationQueue.query.delete()
        db.session.commit()

        # Force the cycle cap down to 2 so a small recent_candidates list
        # is enough to trigger overflow without bloating the test.
        monkeypatch.setattr(dms, 'FUZZY_MAX_CANDIDATES_PER_CYCLE', 2)

        svc = DuplicateMergeService()

        fake_matcher = MagicMock()
        fake_matcher.backfill_uncached_candidates.return_value = 0
        fake_matcher.find_fuzzy_duplicates.return_value = []
        # Queue drain refetches by id — return the full record we'd see in BH.
        fake_matcher._fetch_full_candidate.side_effect = (
            lambda cid: {'id': cid, 'firstName': f'C{cid}', 'lastName': 'X'}
        )
        monkeypatch.setattr(fdm_mod, 'FuzzyDuplicateMatcher',
                            lambda *a, **k: fake_matcher)
        monkeypatch.setattr(dms.time, 'sleep', lambda s: None)

        # Cycle 1: 5 fresh recent candidates, cap=2 → 2 evaluated, 3 queued.
        recent = [{'id': i} for i in [101, 102, 103, 104, 105]]
        result1 = svc._run_fuzzy_matcher_pass(
            recent_candidates=recent,
            already_merged=set(),
            exact_matched=set(),
        )
        assert result1['checked'] == 2, "Cap=2 → exactly 2 candidates evaluated"
        assert result1['queued'] == 3, "Remaining 3 must be enqueued for next cycle"
        assert result1['drained'] == 0, "Nothing was queued before this cycle"

        queued_ids = {
            r.bullhorn_candidate_id for r in FuzzyEvaluationQueue.query.all()
        }
        assert queued_ids == {103, 104, 105}, (
            "Overflow must be written to fuzzy_evaluation_queue "
            f"(got {queued_ids})"
        )

        # Cycle 2: simulate a sustained burst — recent_candidates is again
        # full, but the queue must be drained FIRST so candidates 103/104
        # don't starve. Cap is still 2 → both queued items eat the cap and
        # NO fresh candidate is evaluated this cycle. (105 is queued but
        # we cap drain to the per-cycle cap so it rides one more cycle.)
        new_recent = [{'id': i} for i in [201, 202, 203]]
        evaluated_ids = []
        fake_matcher.find_fuzzy_duplicates.side_effect = lambda c, exclude_ids=None: (
            evaluated_ids.append(c['id']) or []
        )

        result2 = svc._run_fuzzy_matcher_pass(
            recent_candidates=new_recent,
            already_merged=set(),
            exact_matched=set(),
        )
        # Queue drain takes priority — older queued IDs must be evaluated
        # before any fresh recent candidate.
        assert evaluated_ids[:2] == [103, 104], (
            f"Queue must be drained in FIFO order before fresh candidates; "
            f"evaluated_ids={evaluated_ids}"
        )
        assert result2['drained'] == 2, "Both queued items processed should drain"
        # Fresh recent overflow must also be queued (cap consumed by drain).
        remaining_queue = {
            r.bullhorn_candidate_id for r in FuzzyEvaluationQueue.query.all()
        }
        # 103/104 drained; 105 still queued; 201/202/203 newly enqueued
        # because the cycle cap was eaten by the queue drain.
        assert 103 not in remaining_queue
        assert 104 not in remaining_queue
        assert 105 in remaining_queue, "Item past cap must remain queued"
        assert {201, 202, 203}.issubset(remaining_queue), (
            "Fresh overflow must also be queued — sustained-load guarantee"
        )

        # Cleanup so subsequent tests start fresh.
        FuzzyEvaluationQueue.query.delete()
        db.session.commit()


def test_run_scheduled_check_drains_queue_on_quiet_hour(monkeypatch):
    """A scheduler cycle with ZERO fresh recent candidates must still
    drain the persistent fuzzy queue. Otherwise overflow work from a
    prior burst would sit untouched during quiet hours and could age
    out of the 2-hour recent window before being evaluated.

    This is a regression guard for the bug where ``run_scheduled_check``
    used to early-return on ``not recent_candidates``."""
    from duplicate_merge_service import DuplicateMergeService
    import duplicate_merge_service as dms
    import fuzzy_duplicate_matcher as fdm_mod
    from app import app, db
    from models import FuzzyEvaluationQueue

    with app.app_context():
        FuzzyEvaluationQueue.query.delete()
        db.session.commit()

        # Pre-seed the queue as if a previous burst left two items behind.
        db.session.add(FuzzyEvaluationQueue(bullhorn_candidate_id=701))
        db.session.add(FuzzyEvaluationQueue(bullhorn_candidate_id=702))
        db.session.commit()

        svc = DuplicateMergeService()
        # Simulate "no fresh candidates this hour".
        monkeypatch.setattr(svc, '_search_recent_candidates', lambda: [])

        # Tracking which candidates Pass 2 actually evaluates.
        evaluated_ids = []
        fake_matcher = MagicMock()
        fake_matcher.backfill_uncached_candidates.return_value = 0
        fake_matcher._fetch_full_candidate.side_effect = (
            lambda cid: {'id': cid, 'firstName': f'Q{cid}', 'lastName': 'X'}
        )
        fake_matcher.find_fuzzy_duplicates.side_effect = (
            lambda c, exclude_ids=None: (evaluated_ids.append(c['id']) or [])
        )
        monkeypatch.setattr(fdm_mod, 'FuzzyDuplicateMatcher',
                            lambda *a, **k: fake_matcher)
        monkeypatch.setattr(dms.time, 'sleep', lambda s: None)

        # Avoid hitting the merge-log lookup against the Bullhorn-shaped DB.
        from models import CandidateMergeLog
        monkeypatch.setattr(
            CandidateMergeLog, 'query',
            MagicMock(filter=lambda *a, **k: MagicMock(all=lambda: [])),
        )

        stats = svc.run_scheduled_check()

        assert sorted(evaluated_ids) == [701, 702], (
            "Queue must be drained even when recent_candidates is empty; "
            f"evaluated_ids={evaluated_ids}"
        )
        assert stats['fuzzy_drained'] == 2
        # Both queue rows must be gone (terminal state after evaluation).
        remaining = FuzzyEvaluationQueue.query.all()
        assert remaining == [], "Drained queue rows must be removed"

        # Cleanup
        FuzzyEvaluationQueue.query.delete()
        db.session.commit()


def test_fuzzy_pass_queue_entry_with_unfetchable_candidate_eventually_drops(monkeypatch):
    """A queue entry whose candidate cannot be refetched from Bullhorn must
    not block the queue forever — attempts increment per cycle and the row
    is dropped after FUZZY_QUEUE_MAX_ATTEMPTS so the tail keeps moving."""
    from duplicate_merge_service import DuplicateMergeService
    import duplicate_merge_service as dms
    import fuzzy_duplicate_matcher as fdm_mod
    from app import app, db
    from models import FuzzyEvaluationQueue

    with app.app_context():
        FuzzyEvaluationQueue.query.delete()
        db.session.commit()

        # Pre-seed the queue with one candidate whose Bullhorn fetch always
        # fails. Set attempts to one below the ceiling so a single cycle
        # pushes it over and drops the row.
        ceiling = DuplicateMergeService.FUZZY_QUEUE_MAX_ATTEMPTS
        db.session.add(FuzzyEvaluationQueue(
            bullhorn_candidate_id=999,
            attempts=ceiling - 1,
        ))
        db.session.commit()

        svc = DuplicateMergeService()
        fake_matcher = MagicMock()
        fake_matcher.backfill_uncached_candidates.return_value = 0
        fake_matcher.find_fuzzy_duplicates.return_value = []
        fake_matcher._fetch_full_candidate.return_value = None  # always fails
        monkeypatch.setattr(fdm_mod, 'FuzzyDuplicateMatcher',
                            lambda *a, **k: fake_matcher)
        monkeypatch.setattr(dms.time, 'sleep', lambda s: None)

        svc._run_fuzzy_matcher_pass(
            recent_candidates=[],
            already_merged=set(),
            exact_matched=set(),
        )

        remaining = FuzzyEvaluationQueue.query.filter_by(
            bullhorn_candidate_id=999
        ).first()
        assert remaining is None, (
            "Queue entry must be dropped after FUZZY_QUEUE_MAX_ATTEMPTS so a "
            "single broken record cannot starve the queue tail"
        )


def test_fuzzy_pass_runs_backfill_once_per_cycle(monkeypatch):
    """The cold-start backfill must be invoked exactly once per scheduled
    cycle, even when there are multiple candidates to check or none."""
    from duplicate_merge_service import DuplicateMergeService

    svc = DuplicateMergeService()
    fake_matcher = MagicMock()
    fake_matcher.backfill_uncached_candidates.return_value = 7
    fake_matcher.find_fuzzy_duplicates.return_value = []
    import duplicate_merge_service as dms
    import fuzzy_duplicate_matcher as fdm_mod
    monkeypatch.setattr(fdm_mod, "FuzzyDuplicateMatcher",
                        lambda *a, **k: fake_matcher)
    monkeypatch.setattr(dms.time, 'sleep', lambda s: None)

    result = svc._run_fuzzy_matcher_pass(
        recent_candidates=[{'id': 1}, {'id': 2}],
        already_merged=set(),
        exact_matched=set(),
    )

    assert fake_matcher.backfill_uncached_candidates.call_count == 1
    assert result['backfilled'] == 7

    # Empty-candidates case: backfill still runs (it's cold-start fuel)
    fake_matcher.backfill_uncached_candidates.reset_mock()
    fake_matcher.backfill_uncached_candidates.return_value = 3
    result2 = svc._run_fuzzy_matcher_pass(
        recent_candidates=[],
        already_merged=set(),
        exact_matched=set(),
    )
    assert fake_matcher.backfill_uncached_candidates.call_count == 1
    assert result2['backfilled'] == 3
