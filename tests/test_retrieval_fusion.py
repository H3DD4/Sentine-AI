import unittest

from app.kb.base import RetrievalHit
from app.kb.sources import GhostwriterSource, MitreSource, NVDSource, OwaspDocsSource, OwaspSource
from app.services.retrieval import (
    _pin_exact_matches,
    _preserve_source_coverage,
    _prioritize_ghostwriter,
    _weighted_rrf,
)


def _hits(source, count, *, exact_index=None):
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
                score=0.0,
                payload=payload,
            )
        )
    return hits


class RetrievalFusionTests(unittest.TestCase):
    def test_fallback_does_not_exclude_lower_weight_sources(self):
        sources = [NVDSource(), MitreSource(), OwaspSource(), OwaspDocsSource()]
        fused = _weighted_rrf([(source, _hits(source, 15)) for source in sources])

        first_eight = [hit.source_key for hit in fused[:8]]

        self.assertEqual(first_eight.count("nvd"), 2)
        self.assertEqual(first_eight.count("mitre"), 2)
        self.assertEqual(first_eight.count("owasp"), 2)
        self.assertEqual(first_eight.count("owasp_docs"), 2)

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
