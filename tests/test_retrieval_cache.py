import unittest
import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.services.retrieval_cache import (
    _deserialize,
    _serialize,
    clear,
    get_async,
    get,
    put_async,
    put,
    release_lock,
    retrieval_cache_key,
    workspace_update,
)


def _outcome() -> SearchOutcome:
    return SearchOutcome(
        query="ssrf",
        hits=[RetrievalHit(
            source_key="owasp_docs",
            source_label="OWASP",
            doc_id="doc-1",
            title="SSRF guidance",
            text="Server side request forgery",
            score=0.9,
            rank=1,
        )],
        reports=[SourceReport("owasp_docs", "OWASP", Availability.OK, hits=1)],
    )


class RetrievalCacheTests(unittest.TestCase):
    def setUp(self):
        clear()

    def tearDown(self):
        clear()

    def test_key_isolated_by_user_conversation_and_index_revision(self):
        common = dict(query="SSRF", index_revision="rev-1")
        first = retrieval_cache_key(user_id="u1", conversation_id="c1", **common)
        self.assertNotEqual(first, retrieval_cache_key(user_id="u2", conversation_id="c1", **common))
        self.assertNotEqual(first, retrieval_cache_key(user_id="u1", conversation_id="c2", **common))
        self.assertNotEqual(first, retrieval_cache_key(user_id="u1", conversation_id="c1", query="SSRF", index_revision="rev-2"))

    def test_cache_returns_deep_copy(self):
        key = retrieval_cache_key(
            user_id="u1", conversation_id="c1", query="ssrf", index_revision="rev"
        )
        with patch("app.services.retrieval_cache.settings.RETRIEVAL_CACHE_TTL_SECONDS", 60):
            put(key, _outcome())
        first = get(key)
        first.hits[0].title = "mutated"
        self.assertEqual(get(key).hits[0].title, "SSRF guidance")

    def test_expired_entry_is_not_reused(self):
        key = retrieval_cache_key(
            user_id="u1", conversation_id="c1", query="ssrf", index_revision="rev"
        )
        with patch("app.services.retrieval_cache.settings.RETRIEVAL_CACHE_TTL_SECONDS", 0):
            put(key, _outcome())
        self.assertIsNone(get(key))

    def test_workspace_is_compact_deduplicated_and_bounded(self):
        with patch("app.services.retrieval_cache.settings.RETRIEVAL_WORKSPACE_MAX_SEARCHES", 2), patch(
            "app.services.retrieval_cache.settings.RETRIEVAL_WORKSPACE_MAX_HITS", 1
        ):
            workspace = workspace_update(None, query="one", outcome=_outcome())
            workspace = workspace_update(workspace, query="two", outcome=_outcome())
            workspace = workspace_update(workspace, query="three", outcome=_outcome())
        self.assertEqual([item["query"] for item in workspace["searches"]], ["two", "three"])
        self.assertEqual(len(workspace["hits"]), 1)
        self.assertNotIn("text", workspace["hits"][0])

    def test_redis_serialization_round_trip_preserves_provenance(self):
        restored = _deserialize(_serialize(_outcome()))
        self.assertIsNotNone(restored)
        self.assertEqual(restored.query, "ssrf")
        self.assertEqual(restored.hits[0].source_key, "owasp_docs")
        self.assertEqual(restored.hits[0].text, "Server side request forgery")
        self.assertEqual(restored.reports[0].availability, Availability.OK)

    def test_redis_schema_mismatch_is_a_miss(self):
        raw = json.dumps({"schema_version": 999, "outcome": _outcome().to_dict()})
        self.assertIsNone(_deserialize(raw))

    def test_redis_unavailable_keeps_local_write_and_read(self):
        key = "retrieval:test:unavailable"
        with patch("app.services.retrieval_cache._redis_client", new=AsyncMock(return_value=None)):
            asyncio.run(put_async(key, _outcome()))
            restored = asyncio.run(get_async(key))
        self.assertEqual(restored.query, "ssrf")

    def test_redis_write_passes_positive_ttl(self):
        client = AsyncMock()
        with patch("app.services.retrieval_cache._redis_client", new=AsyncMock(return_value=client)), \
             patch("app.services.retrieval_cache._ttl_seconds", return_value=12.8):
            asyncio.run(put_async("retrieval:test:ttl", _outcome()))
        client.set.assert_awaited_once()
        self.assertEqual(client.set.await_args.kwargs["ex"], 12)

    def test_release_lock_only_deletes_owned_token(self):
        client = AsyncMock()
        client.get.return_value = "another-token"
        with patch("app.services.retrieval_cache._redis_client", new=AsyncMock(return_value=client)):
            asyncio.run(release_lock("retrieval:test:lock", "my-token"))
        client.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
