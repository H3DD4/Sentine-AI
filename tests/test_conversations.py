import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException

from app.models import AnalysisConversation, User
from app.routers.conversations import get_conversation, update_conversation
from app.schemas import ConversationState


class ConversationTests(unittest.TestCase):
    def test_other_users_conversation_is_not_visible(self):
        session = AsyncMock()
        session.scalar.return_value = None
        user = User(id="user-a", email="a@example.com")

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(get_conversation("conversation-b", session=session, user=user))

        self.assertEqual(raised.exception.status_code, 404)

    def test_update_preserves_owner_and_changes_snapshot(self):
        conversation = AnalysisConversation(
            id="conversation-a",
            user_id="user-a",
            title="Original",
            messages=[],
        )
        session = AsyncMock()
        session.scalar.return_value = conversation
        session.refresh = AsyncMock()
        user = User(id="user-a", email="a@example.com")
        state = ConversationState(
            title="Updated",
            messages=[{"id": "message-1", "from": "user", "text": "Evidence"}],
            finding_id="finding-1",
        )

        result = asyncio.run(
            update_conversation("conversation-a", state, session=session, user=user)
        )

        self.assertEqual(result.user_id, "user-a")
        self.assertEqual(result.title, "Updated")
        self.assertEqual(result.finding_id, "finding-1")
        session.commit.assert_awaited_once()

    def test_client_cannot_overwrite_retrieval_workspace(self):
        self.assertNotIn("retrieval_workspace", ConversationState.model_fields)


if __name__ == "__main__":
    unittest.main()
