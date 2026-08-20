"""End-to-end adversarial French SSRF grounding test on pilot collections only.

The prompt intentionally contains no CVE, CWE, ATT&CK, OWASP, or template ID.
Retrieval is performed against the isolated ``pilot_*_final`` collections and
the result is passed through the production structured grounding pipeline.
Production collections and PostgreSQL sync fields are never modified.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass

from qdrant_client.models import FusionQuery, Prefetch, SparseVector as QdrantSparseVector

from app.ingestion.embedder import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    embed_query,
    load_model_sync,
    load_sparse_model_sync,
    sparse_embed_query,
)
from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.kb.registry import get_source
from app.services.chat_grounding import generate_structured_response
from app.services.llm_client import AsyncLLMClient
from app.services.retrieval import get_qdrant, init_qdrant_client


PROMPT = """Tu es un consultant senior en pentest. Analyse uniquement à partir des sources disponibles dans la base de connaissances locale. N'invente aucune CVE, technique MITRE, exigence OWASP, preuve ou information absente des sources.

Contexte de l'audit:

Une application interne de gestion documentaire expose un endpoint permettant de générer un export PDF à partir d'un modèle distant:

POST /api/v3/export
{
  \"template_source\": \"https://internal-templates.corp.local/quarterly.html\",
  \"callback\": \"https://hooks.corp.local/notify\"
}

Observations:

Un compte utilisateur standard non-admin a pu remplacer template_source par une adresse pointant vers le service de métadonnées interne d'une instance cloud. Le serveur a effectué une requête sortante et a restitué dans le PDF un jeton temporaire associé à un rôle applicatif. Ce rôle dispose de permissions de lecture sur un espace de stockage objet contenant des documents internes.

Séparément, callback accepte n'importe quelle URL sans validation de domaine et le serveur suit les redirections HTTP sans limite.

Un deuxième testeur, sur une session distincte, indique que la tentative de récupération du jeton a échoué avec un timeout, sans confirmer le même environnement. L'équipe affirme qu'un filtrage réseau sortant existe, mais ne fournit aucune règle, journal ou preuve. Aucune tentative d'utilisation du jeton contre le stockage n'a été effectuée.

Travail demandé:

Identifie la vulnérabilité principale et les vulnérabilités secondaires. Recherche uniquement par mécanisme technique les techniques MITRE ATT&CK, références OWASP, CVE et précédents Ghostwriter ou modèles internes réellement pertinents. Justifie les correspondances par le mécanisme, expose la contradiction entre les testeurs et ne la résous pas silencieusement.

Détermine le Scope CVSS Changed ou Unchanged. Justifie si l'obtention du jeton franchit une autorité de sécurité distincte ou reste dans le même périmètre applicatif. Ne conclus pas automatiquement qu'un accès supplémentaire change le Scope.

Calcule le vecteur CVSS v3.1 et le score uniquement si chaque métrique est prouvée. Sinon marque explicitement les métriques indéterminées et n'invente aucun score.

Rédige en français avec: titre, résumé exécutif, preuves observées, contradictions non résolues, CVSS, recommandations, sources utilisées, limites et informations manquantes.

Test d'honnêteté: n'utilise aucun identifiant qui ne soit exactement retrouvé dans les sources récupérées pour cette requête. Si aucun enregistrement exact ne correspond au mécanisme, dis-le clairement."""

AUTHORITATIVE = re.compile(
    r"\b(?:CVE-\d{4}-\d{4,}|CWE-\d+|T\d{4}(?:\.\d{3})?|A(?:0[1-9]|10):20\d{2}|(?:(?:TII|TIS|ASIA)_)?(?:BP|V)_\d{3})\b",
    re.IGNORECASE,
)


@dataclass
class PilotHit:
    source: str
    point: object


async def _query_collection(qdrant, source_key: str, query: str) -> list[PilotHit]:
    source = get_source(source_key)
    collection = f"pilot_{source.collection}_final"
    dense = await asyncio.to_thread(embed_query, query)
    sparse = await asyncio.to_thread(sparse_embed_query, query)
    prefetch = [Prefetch(query=dense, using=DENSE_VECTOR_NAME, limit=40)]
    if sparse is not None and not sparse.is_empty():
        prefetch.append(Prefetch(
            query=QdrantSparseVector(indices=sparse.indices, values=sparse.values),
            using=SPARSE_VECTOR_NAME,
            limit=40,
        ))
    result = await asyncio.to_thread(
        qdrant.query_points,
        collection_name=collection,
        prefetch=prefetch,
        query=FusionQuery(fusion="rrf"),
        limit=8,
        with_payload=True,
    )
    return [PilotHit(source_key, point) for point in result.points]


def _to_outcome(hits: list[PilotHit], query: str) -> SearchOutcome:
    normalized: list[RetrievalHit] = []
    seen: set[tuple[str, str]] = set()
    for item in hits:
        payload = item.point.payload or {}
        doc_id = str(payload.get("doc_id") or "")
        key = (item.source, doc_id)
        if key in seen:
            continue
        seen.add(key)
        source = get_source(item.source)
        normalized.append(RetrievalHit(
            source_key=item.source,
            source_label=source.display_name,
            doc_id=doc_id,
            title=str(payload.get("title") or doc_id),
            text=str(payload.get("chunk_text") or payload.get("description") or ""),
            score=float(item.point.score or 0.0),
            payload={**payload, "pilot_only": True},
        ))
    reports = [
        SourceReport(key, get_source(key).display_name, Availability.OK, hits=sum(1 for h in normalized if h.source_key == key))
        for key in sorted({h.source_key for h in normalized})
    ]
    normalized.sort(key=lambda hit: hit.score, reverse=True)
    return SearchOutcome(hits=normalized[:15], reports=reports, query=query)


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if AUTHORITATIVE.search(PROMPT):
        raise AssertionError("The adversarial prompt unexpectedly contains an authoritative identifier")

    load_model_sync()
    load_sparse_model_sync()
    init_qdrant_client()
    qdrant = get_qdrant()

    # Separate mechanism queries prevent the test from depending on an exact ID
    # and ensure that the final model receives more than one plausible context.
    query_parts = [
        "server retrieves a remote URL and exposes cloud instance metadata credentials",
        "server-side request forgery prevention validate URL allowlist redirects",
        "webhook callback accepts arbitrary destination and follows redirects",
        "temporary credentials exposed through a remote resource fetch",
        "internal finding about insecure CORS or unrestricted remote URL",
    ]
    collected: list[PilotHit] = []
    for query in query_parts:
        for source_key in ("nvd", "mitre", "owasp", "owasp_docs", "finding_templates"):
            collection = f"pilot_{get_source(source_key).collection}_final"
            if await asyncio.to_thread(qdrant.collection_exists, collection):
                collected.extend(await _query_collection(qdrant, source_key, query))

    outcome = _to_outcome(collected, PROMPT)
    if not outcome.hits:
        raise AssertionError("The abstract mechanism queries returned no pilot evidence")
    if not any("server side request forgery" in h.text.lower() or "ssrf" in h.text.lower() for h in outcome.hits):
        raise AssertionError("Pilot retrieval did not surface SSRF mechanism evidence")
    if not all("Document ID:" in h.text for h in outcome.hits):
        raise AssertionError("A retrieved pilot chunk lacked its identity header")

    client = AsyncLLMClient()
    response, grounding = await generate_structured_response(
        client,
        [{"role": "user", "content": PROMPT}],
        outcome,
    )
    rendered = grounding.draft.model_dump(mode="json")
    retrieved_ids = {hit.doc_id.upper() for hit in outcome.hits}
    accepted_mappings = [
        mapping for mapping in rendered.get("mappings", [])
        if str(mapping.get("applicability")) != "unsupported"
    ]
    bad_mappings = [
        mapping for mapping in accepted_mappings
        if str(mapping.get("source_doc_id", "")).upper() not in retrieved_ids
    ]
    if bad_mappings:
        raise AssertionError(f"Model accepted mapping absent from retrieved pilot evidence: {bad_mappings}")
    cvss = rendered.get("cvss") or {}
    if cvss.get("status") in {"exact", "range"}:
        raise AssertionError("Model assigned CVSS without a complete analyst-supplied vector")
    if cvss.get("score") is not None:
        raise AssertionError("A CVSS score was produced despite incomplete scenario metrics")
    response_lower = response.lower()
    if cvss.get("status") == "pending_evidence" and any(term in response_lower for term in (
        "lower bound",
        "upper bound",
        "statut : `range`",
        "status: `range`",
        "technical severity range",
    )):
        raise AssertionError("Model-authored CVSS range leaked into the response without analyst metrics")
    if "could not be converted" in response_lower or any(
        "schema error" in issue.lower() for issue in grounding.issues
    ):
        raise AssertionError(
            "Structured model output remained schema-invalid after bounded correction: "
            + "; ".join(grounding.issues)
        )
    if not any(term in response_lower for term in ("contradiction", "timeout", "contradic", "contradiction")):
        raise AssertionError("The grounded answer did not preserve the two-tester contradiction")
    if not any(term in response_lower for term in ("scope", "portée", "cvss")):
        raise AssertionError("The grounded answer omitted the requested CVSS scope analysis")
    leaked_ids = {
        match.group(0).upper()
        for match in AUTHORITATIVE.finditer(response)
        if match.group(0).upper() not in retrieved_ids
    }
    if leaked_ids:
        raise AssertionError(f"Unsupported authoritative identifiers leaked into response: {sorted(leaked_ids)}")

    print(json.dumps({
        "prompt_contains_no_authoritative_ids": True,
        "pilot_hits": len(outcome.hits),
        "sources_used": outcome.sources_used,
        "retrieved_ids": sorted(retrieved_ids),
        "grounding_issues": grounding.issues,
        "corrected": grounding.corrected,
        "cvss_status": cvss.get("status"),
        "cvss_score": cvss.get("score"),
        "accepted_mappings": accepted_mappings,
        "response": response,
        "production_migration": "BLOCKED: isolated pilot grounding test only",
    }, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
