# Production release gates

This hardening release protects identity, review history and FHIR exports. Passing the synthetic
suite is not a clinical accuracy or deployment certification. Do not route patient traffic until
all gates below have evidence from the target deployment.

## Upgrade

1. Back up PostgreSQL and object storage together; verify the restore procedure in `docs/dr.md`.
   Stop web intake and workers during migration. Retain the previous application artifact.
2. Run `python -m alembic upgrade head` (revision `0021`). This adds birth-date precision and
   canonical PRN dosing. Existing dates without full-date evidence are conservatively marked
   year-only; they are no longer exported as an exact birthday. Reconcile uncertain dates with
   the patient registry. Historical canonical JSON bundles are immutable snapshots: reconcile
   PRN, condition-status and birth-date errors through the clinical amendment process before
   sharing them. Do not assume migrating table columns repairs previously exported JSON.
3. Refit calibration curves and derive thresholds on adjudicated data: the isotonic algorithm
   now preserves fitted plateaus and handles tied scores correctly. Old curve JSON remains
   readable but must not be used to claim validation of the corrected algorithm.
4. Set `CDI_PRODUCTION=true` for web and worker processes. It rejects development credentials,
   the stub model, unpinned model revisions, insecure cookies, in-process jobs, and missing or
   inconsistent calibration settings. Use the authenticated web app; the legacy ingest-only
   API refuses production startup.
5. Set `CDI_FHIR_REQUIRE_HAPI=true` (the default). Install and checksum-verify the pinned validator
   and offline IG cache using `infra/runpod/versions.env` and `scripts/fhir_ig.py`. Missing or
   failing validation blocks sharing. The Python fallback is only for synthetic development.
6. Run `python -m cdi_adapter.preflight` with the actual service environment. It must exit zero.
   It checks effective configuration, schema, calibrator existence, object storage, Redis,
   validator artifacts, and the loaded model version. It does not alter data or print secrets.
7. Start services using the repository's deployment scripts; verify `/readyz`, worker delivery,
   reviewer authentication, and a representative accepted and rejected document end to end.

## Required clinical and operational evidence

- Re-run the golden path and real offline HL7 validation with zero error/fatal findings on the
  bundles intended for sharing. All seven checked-in golden bundles currently validate with zero
  errors; validate the target deployment’s representative documents as well.
- Obtain clinical sign-off on model accuracy, identity false matches, medication numeric errors,
  de-identification and calibrated auto-accept thresholds using held-out adjudicated data.
- Verify TLS ingress, private backend binding/firewall rules, least-privilege service accounts,
  secret rotation, access reviews, audit retention, encrypted backups, and a timed restore drill.
- Load-test the actual model hardware and queue with the expected workload. Check bounded memory,
  queue recovery after worker termination, request timeouts, alerts, and storage retention.
- Check dependency advisories against the deployed lockfiles. CPU CI constraints do not replace
  the separately pinned GPU, quantization and deployment environments.

## Limits and behavior changes

Defaults: 12 MiB/file, 64 MiB/request, 50 pages/document, 10 million pixels/page, and 50 million
pixels/document. Tune `CDI_UPLOAD_MAX_BYTES`, `CDI_REQUEST_MAX_BYTES`, `CDI_DOCUMENT_MAX_PAGES`,
`CDI_PAGE_MAX_PIXELS`, and `CDI_DOCUMENT_MAX_PIXELS` against measured capacity. Configure ingress
body timeouts and connection/rate limits too; byte limits alone do not prevent slow clients.

Known identity conflicts fail before replacing facts. Re-upload/replay preserves documents with
human decisions or claimed/final review items. Correct and resolve every held fact before
approval; approval and correction share document locks. Approved snapshots cannot be edited
through the fact-review endpoint. FHIR download returns HTTP 409 until governance and validation
pass. Read-only job FHIR requests project only the job's documents without database writes.

For local synthetic CI only, set `CDI_FHIR_REQUIRE_HAPI=false`; never use that override with
patient traffic. Production mode rejects it.
