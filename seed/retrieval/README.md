# Retrieval Seed

This directory is the cloneable, ready-to-query knowledge corpus used for a
fresh installation. It contains compressed authoritative PostgreSQL rows and
the corresponding Qdrant dense/sparse vectors.

The bundle includes NVD, MITRE ATT&CK, OWASP Top 10, official OWASP guides, and
the extracted finding-template records. It deliberately excludes accounts,
conversations, engagements, findings, reports, audit logs, Ghostwriter client
data, and internal analyst notes.

Docker restores this bundle after Alembic migrations. It does not scrape public
sites or re-embed the corpus during first startup. Scheduled sync jobs can add
newer records afterward. The embedding models still ship in the backend image
because live searches need them to encode each user query.

To refresh the release bundle from validated local PostgreSQL and Qdrant
services:

```powershell
python -m scripts.export_retrieval_seed --output seed/retrieval
python -m scripts.restore_retrieval_seed --seed seed/retrieval
python -m scripts.check_index_signature
```

The restore refuses a bundle whose signature differs from the application's
model and chunking configuration. Existing updated corpora are left untouched.
