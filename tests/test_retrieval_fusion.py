import unittest

from app.kb.base import RetrievalHit
from app.kb.sources import GhostwriterSource, MitreSource, NVDSource, OwaspDocsSource, OwaspSource
from app.services.retrieval import (
    _pin_exact_matches,
    _preserve_source_coverage,
    _prioritize_ghostwriter,
    _weighted_rrf,
)
from app.kb.indexer import _chunk_header
from app.ingestion.embedder import chunk_text_with_header


def _hits(source, count, *, exact_index=None, scores=None):
    hits = []
    for index in range(count):
        payload = {"matched_by": "exact_id" if index == exact_index else "hybrid"}
        hits.append(
            RetrievalHit(
                source_key=source.key,
                source_label=source.display_name,
                doc_id=f"{source.key}-{index}",
                title=f"Document {index}",
                text="content",
                score=(scores[index] if scores else 0.0),
                payload=payload,
            )
        )
    return hits


class RetrievalFusionTests(unittest.TestCase):
    def test_hybrid_relevance_competes_across_sources(self):
        sources = [NVDSource(), MitreSource(), OwaspSource(), OwaspDocsSource()]
        fused = _weighted_rrf(
            [
                (sources[0], _hits(sources[0], 2, scores=[0.90, 0.30])),
                (sources[1], _hits(sources[1], 1, scores=[0.80])),
                (sources[2], _hits(sources[2], 1, scores=[0.20])),
                (sources[3], _hits(sources[3], 1, scores=[0.10])),
            ]
        )

        self.assertEqual([hit.source_key for hit in fused[:3]], ["nvd", "mitre", "nvd"])
        self.assertEqual([hit.score for hit in fused[:2]], [0.90, 0.80])

    def test_chunk_header_keeps_identity_and_severity_with_fragment(self):
        header = _chunk_header(
            "CVE-2025-1234",
            {"title": "Example vulnerability", "severity": "CRITICAL", "cvss_v3": 9.8},
        )
        self.assertIn("Document ID: CVE-2025-1234", header)
        self.assertIn("Title: Example vulnerability", header)
        self.assertIn("Severity: CRITICAL; CVSS: 9.8", header)

    def test_chunk_header_is_repeated_without_exceeding_configured_window(self):
        chunks = chunk_text_with_header("Document ID: CVE-1", "long evidence " * 2000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.startswith("Document ID: CVE-1\n\n") for chunk in chunks))

    def test_exact_identifier_is_pinned_globally(self):
        nvd = NVDSource()
        owasp_docs = OwaspDocsSource()
        fused = _weighted_rrf(
            [
                (owasp_docs, _hits(owasp_docs, 5)),
                (nvd, _hits(nvd, 5, exact_index=0)),
            ]
        )

        self.assertEqual(fused[0].source_key, "nvd")
        self.assertEqual(fused[0].payload["matched_by"], "exact_id")

    def test_ghostwriter_is_preserved_before_other_semantic_matches(self):
        ghostwriter = GhostwriterSource()
        mitre = MitreSource()
        hits = _prioritize_ghostwriter(
            _hits(mitre, 8) + _hits(ghostwriter, 1)
        )

        self.assertEqual(hits[0].source_key, "ghostwriter")
        self.assertEqual(len(hits), 9)

    def test_exact_identifier_stays_before_ghostwriter(self):
        ghostwriter = GhostwriterSource()
        nvd = NVDSource()
        exact = _hits(nvd, 1, exact_index=0)
        hits = _prioritize_ghostwriter(exact + _hits(ghostwriter, 1))

        self.assertEqual(hits[0].source_key, "nvd")
        self.assertEqual(hits[1].source_key, "ghostwriter")

    def test_reranker_cannot_remove_an_entire_source(self):
        nvd = NVDSource()
        mitre = MitreSource()
        original = _hits(nvd, 2) + _hits(mitre, 1)

        restored = _preserve_source_coverage(original[:2], original)

        self.assertEqual([hit.source_key for hit in restored], ["nvd", "mitre", "nvd"])

    def test_exact_identifier_is_re_pinned_after_reranking(self):
        nvd = NVDSource()
        exact = _hits(nvd, 1, exact_index=0)[0]
        semantic = _hits(nvd, 1)[0]
        restored = _pin_exact_matches([semantic], [exact, semantic])
        self.assertEqual(restored[0].payload["matched_by"], "exact_id")


if __name__ == "__main__":
    unittest.main()
