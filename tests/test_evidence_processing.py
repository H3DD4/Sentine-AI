import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException, UploadFile

from app.services.query_planner import _evenly_select
from app.services.retrieval import _exact_id_query_sync
from app.services.upload_processing import cleanup_staged_evidence, stage_evidence_uploads
from app.services.validation import _bounded_items, _representative_text


class EvidenceProcessingTests(unittest.TestCase):
    def test_prompt_excerpt_preserves_beginning_and_end(self):
        text = "BEGIN" + "x" * 200 + "END"

        excerpt, truncated = _representative_text(text, 80)

        self.assertTrue(truncated)
        self.assertTrue(excerpt.startswith("BEGIN"))
        self.assertTrue(excerpt.endswith("END"))
        self.assertIn("middle omitted", excerpt)

    def test_evidence_budget_is_shared_across_all_files(self):
        selected, truncated = _bounded_items(["a" * 100, "b" * 100, "c" * 100], 90)

        self.assertTrue(truncated)
        self.assertEqual(len(selected), 3)
        self.assertTrue(selected[0].startswith("a"))
        self.assertTrue(selected[-1].endswith("c"))

    def test_query_selection_reaches_later_files(self):
        self.assertEqual(_evenly_select(["first", "second", "third", "last"], 3), ["first", "third", "last"])

    def test_uploads_are_staged_and_manifest_reports_selection(self):
        upload = UploadFile(filename="../evidence.log", file=io.BytesIO(b"start\n" + b"x" * 200 + b"\nend"))
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.upload_processing.settings.UPLOAD_DIR", directory
        ), patch("app.services.upload_processing.EVIDENCE_CONTEXT_CHARS", 80):
            parsed, manifest = asyncio.run(stage_evidence_uploads([upload]))
            try:
                self.assertEqual(parsed[0]["filename"], "evidence.log")
                self.assertTrue(Path(parsed[0]["storage_path"]).exists())
                self.assertLess(manifest["files"][0]["selected_chars"], manifest["files"][0]["extracted_chars"])
                self.assertIn("complete artifact was retained", " ".join(manifest["notices"]))
            finally:
                cleanup_staged_evidence(parsed)

    def test_streaming_per_file_limit_removes_partial_artifact(self):
        upload = UploadFile(filename="large.log", file=io.BytesIO(b"x" * 20))
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.upload_processing.settings.UPLOAD_DIR", directory
        ), patch("app.services.upload_processing.settings.EVIDENCE_MAX_FILE_BYTES", 10), patch(
            "app.services.upload_processing.settings.EVIDENCE_UPLOAD_CHUNK_BYTES", 4
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(stage_evidence_uploads([upload]))

            self.assertEqual(raised.exception.status_code, 413)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_exact_id_lookup_keeps_hybrid_payload_filter(self):
        qdrant = Mock()
        qdrant.query_points.return_value.points = []
        source = Mock(collection="kb_mitre")
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        payload_filter = Filter(
            must=[FieldCondition(key="deprecated", match=MatchValue(value=False))]
        )

        _exact_id_query_sync(qdrant, source, ["T1059.001"], payload_filter)

        sent_filter = qdrant.query_points.call_args.kwargs["query_filter"]
        self.assertEqual(len(sent_filter.must), 2)
        self.assertEqual(sent_filter.must[1].key, "deprecated")


if __name__ == "__main__":
    unittest.main()
