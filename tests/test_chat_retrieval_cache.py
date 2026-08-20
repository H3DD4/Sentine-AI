import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.kb.base import SearchOutcome
from app.models import AnalysisConversation
from app.routers.chat import _conversation_outcome, _owned_conversation
from app.services.retrieval_cache import clear


class ChatRetrievalCacheTests(unittest.TestCase):
    def setUp(self):
        clear()

    def tearDown(self):
        clear()

    def test_owned_conversation_rejects_unknown_or_other_owner(self):
        session = AsyncMock()
        session.scalar.return_value = None
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(_owned_conversation("other", "user-a", session))
        self.assertEqual(raised.exception.status_code, 404)

    def test_second_identical_turn_reuses_cache_and_does_not_rewrite_workspace(self):
        session = AsyncMock()
        result = unittest.mock.Mock()
        result.all.return_value = []
        session.execute.return_value = result
        conversation = AnalysisConversation(
            id="conversation-a", user_id="user-a", messages=[], retrieval_workspace=None
        )
        search = AsyncMock(return_value=SearchOutcome(query="analyze ssrf"))
        with patch("app.routers.chat.federated_search", search), patch(
            "app.services.retrieval_cache.settings.RETRIEVAL_CACHE_BACKEND", "l1"
        ):
            first = asyncio.run(_conversation_outcome(
                "analyze ssrf", [], session,
                user_id="user-a", conversation=conversation,
            ))
            second = asyncio.run(_conversation_outcome(
                "analyze ssrf", [], session,
                user_id="user-a", conversation=conversation,
            ))
        self.assertEqual(search.await_count, 1)
        self.assertIn("session retrieval cache hit", second.notes)
        self.assertEqual(session.commit.await_count, 2)
        self.assertEqual(len(conversation.retrieval_workspace["searches"]), 2)


if __name__ == "__main__":
    unittest.main()
