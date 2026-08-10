import asyncio
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException
from jose import jwt

from app.auth import create_access_token, create_refresh_token, get_current_user
from app.config import settings
from app.schemas import UserCreate


class AuthenticationTests(unittest.TestCase):
    def test_registration_input_is_normalized(self):
        user = UserCreate(
            username="  Analyst.One  ",
            email="  Analyst.One@Example.COM ",
            password="ValidPass123!",
        )

        self.assertEqual(user.username, "analyst.one")
        self.assertEqual(user.email, "analyst.one@example.com")

    def test_registration_rejects_invalid_fields(self):
        with self.assertRaises(ValueError):
            UserCreate(username="a b", email="not-an-email", password="short")

    def test_access_and_refresh_tokens_have_separate_types(self):
        access = jwt.decode(
            create_access_token({"sub": "user-id"}),
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        refresh = jwt.decode(
            create_refresh_token({"sub": "user-id"}),
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        self.assertEqual(access["type"], "access")
        self.assertEqual(refresh["type"], "refresh")

    def test_refresh_token_cannot_authenticate_an_api_request(self):
        session = AsyncMock()
        token = create_refresh_token({"sub": "user-id"})

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(get_current_user(token=token, session=session))

        self.assertEqual(raised.exception.status_code, 401)
        session.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
