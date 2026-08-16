import unittest
from unittest.mock import Mock, patch

from app.ingestion import embedder


class EmbeddingModelConfigTests(unittest.TestCase):
    def test_bge_m3_does_not_use_english_query_instruction(self):
        with patch.object(embedder.settings, "EMBEDDING_MODEL", "BAAI/bge-m3"):
            self.assertFalse(embedder._uses_bge_prefix())

    def test_legacy_bge_uses_query_instruction(self):
        with patch.object(embedder.settings, "EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5"):
            self.assertTrue(embedder._uses_bge_prefix())

    def test_chunk_config_respects_model_limit(self):
        model = Mock(max_seq_length=512)
        with (
            patch.object(embedder, "get_model", return_value=model),
            patch.object(embedder.settings, "EMBEDDING_CHUNK_TOKENS", 800),
            patch.object(embedder.settings, "EMBEDDING_CHUNK_OVERLAP_TOKENS", 100),
        ):
            self.assertEqual(embedder.chunk_config(), (496, 100))

    def test_chunk_config_uses_moderate_m3_window(self):
        model = Mock(max_seq_length=8192)
        with (
            patch.object(embedder, "get_model", return_value=model),
            patch.object(embedder.settings, "EMBEDDING_CHUNK_TOKENS", 800),
            patch.object(embedder.settings, "EMBEDDING_CHUNK_OVERLAP_TOKENS", 100),
        ):
            self.assertEqual(embedder.chunk_config(), (800, 100))


if __name__ == "__main__":
    unittest.main()
