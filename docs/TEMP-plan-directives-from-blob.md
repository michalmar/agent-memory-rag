# Plan: Azure Blob source for directive ingestion

**Status:** Ready for implementation

**Date:** 2026-07-23

## Objective

Move directive source PDFs from the ingestion container image to a dedicated
`directive-source` container in the existing directive storage account. Preserve
the current ingestion outputs and runtime contracts so the Directive Assistant,
backend tools, Search index, Cosmos catalog, and artifact reads do not change.

## Locked decisions

- Reuse the existing directive storage account.
- Create a separate `directive-source` container for uploaded PDFs.
- Keep generated outputs in the existing `directive-artifacts` container.
- Continue copying every source PDF to its immutable artifact path for
  reproducibility.
- Use the ingestion job's managed identity; do not introduce keys, SAS tokens, or
  connection strings.
- Keep private networking and account-level private endpoints.
- Keep the current PDF filename contract:
  `<eight-digit-id>-<name>-v<number>.pdf`.
- Keep local-folder ingestion available for development and rollback.
- Keep the mandatory-assignment CSV flow unchanged.
- Preserve current deletion behavior: removing a source blob does not retire or
  delete the last published directive version.
- Do not change the agent prompt, tools, backend directive repositories, or public
  citation model.

## Current implementation gaps

- `source.py` discovers PDFs synchronously from a local `Path`.
- `IngestionConfig` requires `DIRECTIVE_SOURCE_DIR`.
- `DirectiveIngestionRunner` calls `discover_pdfs()` directly.
- CLI source overrides accept only local paths.
- The ingestion Docker image copies the PDF fixtures into `/app/fixtures/pdf`.
- Terraform configures only the `directive-artifacts` container and local source
  directory.
- Preflight checks artifact Blob access but not source-container list/read access.

## Implementation phases

### 1. Introduce a source abstraction

- Define an asynchronous directive-source protocol that returns
  `SourceDocument` records.
- Move filename, PDF-signature, content-hash, duplicate ID/version, and empty-corpus
  validation into shared code used by every source implementation.
- Implement `LocalDirectiveSource` with behavior equivalent to the existing
  `discover_pdfs()` function.
- Refactor `SourceDocument` so it uses a source name and private provenance fields
  instead of requiring a local filesystem `Path`.
- Keep source URLs, account names, ETags, and other storage details out of
  model-visible metadata and citations.

### 2. Add the Azure Blob source adapter

- Implement `BlobDirectiveSource` using the existing asynchronous Azure Blob SDK
  and ingestion managed identity.
- List committed blobs under an optional configured prefix and select `.pdf`
  objects.
- Sort blob names deterministically and enforce unique directive ID/version pairs.
- Download each blob with an ETag condition so an overwrite during ingestion fails
  safely rather than producing mixed content.
- Validate the `%PDF` signature and compute the existing SHA-256 `source_hash` from
  downloaded bytes.
- Record blob name, ETag, version ID when available, size, and last-modified time as
  private ingestion provenance.
- Treat missing or inaccessible source containers and empty corpora as explicit
  failures.

### 3. Add configuration and dependency wiring

- Add a source selector such as `DIRECTIVE_SOURCE_KIND=local|azure_blob`.
- Add `DIRECTIVE_SOURCE_CONTAINER`, defaulting to `directive-source`.
- Add optional `DIRECTIVE_SOURCE_PREFIX`.
- Reuse `DIRECTIVE_BLOB_ACCOUNT_URL` because source and artifacts share the account.
- Construct the selected source adapter once in `DirectiveIngestionRunner`.
- Replace direct `discover_pdfs()` calls in validate, verify, reconcile, and
  run-daily paths with the asynchronous source interface.
- Preserve local `--source` overrides for development; reject them when Blob mode
  is selected rather than silently changing source type.
- Include source configuration that affects processing in diagnostics, but do not
  include location-only changes in `processing_hash`.

### 4. Extend preflight and verification

- Preflight source-container list access and a conditional read of a discovered PDF.
- Report source access separately from artifact-container access.
- Keep verification based on source hashes, catalog manifests, artifact existence,
  Search publication counts, current pointers, relations, and mandate snapshots.
- Add private source provenance to ingestion run records if it can be done without
  changing model-visible contracts.
- Ensure a Blob-sourced corpus with the same files is recognized as unchanged.

### 5. Provision the source container and RBAC

- Add a private `directive-source` Blob container to the existing storage account.
- Grant the ingestion UAMI `Storage Blob Data Reader` at source-container scope.
- Retain its write access only where needed for `directive-artifacts`.
- Do not grant the backend or Hosted Agent access to `directive-source`.
- Add Terraform variables and outputs for the source container and optional prefix.
- Pass Blob source settings to the Container Apps ingestion job.
- Reuse the existing storage private endpoint and private DNS path; verify no new
  public access is introduced.
- Document which operator or upstream process is permitted to upload source PDFs.

### 6. Stop packaging source PDFs

- Update the ingestion Dockerfile so directive PDFs are no longer copied into the
  production image.
- Continue packaging the mandatory CSV until its source is changed separately.
- Keep fixture PDFs available to unit and local integration tests.
- Confirm document updates no longer require an image rebuild or job redeployment.

### 7. Add automated coverage

- Test local and Blob source implementations against the same validation contract.
- Test deterministic listing, prefix filtering, filename validation, duplicate
  detection, empty containers, invalid PDF signatures, and content hashing.
- Test conditional download failure when the source ETag changes.
- Test unchanged Blob documents skip extraction, summarization, embedding, and
  publication.
- Test changed content under the same directive version creates a new generation
  and retires the old Search generation only after successful publication.
- Test failed extraction still quarantines the source bytes and prevents activation.
- Test generated artifacts cannot be re-ingested because source and artifact
  containers are distinct.
- Update preflight, CLI, configuration, and Terraform tests.

### 8. Migrate and release

1. Provision the source container, RBAC, and job settings without changing the
   active source mode.
2. Upload the current fixture PDFs to `directive-source` with their existing names.
3. Deploy the dual-source ingestion image.
4. Run Blob-mode `preflight` and `validate`.
5. Run `run-daily`; the existing hashes should cause all already-published PDFs to
   be skipped.
6. Run the read-only cross-store `verify` command and compare counts with the
   current baseline.
7. Upload one controlled test version and verify extraction, Search publication,
   catalog activation, artifacts, citations, and agent retrieval.
8. Remove production PDFs from subsequent ingestion images after the Blob path has
   completed its soak period.

## Rollback

- Switch the job back to `DIRECTIVE_SOURCE_KIND=local` and deploy the previous image
  containing the fixture PDFs.
- Leave the source container and newly generated immutable artifacts in place.
- No Search, catalog, manifest, backend, or agent schema rollback should be needed.

## Acceptance criteria

- Uploading a valid PDF triggers ingestion without rebuilding the container image.
- An unchanged corpus produces zero changed documents and no model or Search writes.
- Source PDFs and generated artifacts cannot be confused or recursively ingested.
- Every published manifest still references an immutable source PDF, canonical
  Markdown, ordered sections, summary, and manifest in `directive-artifacts`.
- Current and historical version behavior is unchanged.
- Search and cross-store verification counts remain consistent.
- The ingestion job uses managed identity over the existing private storage path.
- The backend and Hosted Agent have no source-container permissions.
- No storage URL, token, ETag, or private source locator appears in agent output.
- Directive Assistant answers and citations remain behaviorally unchanged.

## Primary implementation surfaces

- `setup/directive_ingest/src/directive_ingestion/source.py`
- `setup/directive_ingest/src/directive_ingestion/config.py`
- `setup/directive_ingest/src/directive_ingestion/reconcile.py`
- `setup/directive_ingest/src/directive_ingestion/cli.py`
- `setup/directive_ingest/Dockerfile`
- `setup/directive_ingest/tests/`
- `infra/directive_data.tf`
- `infra/directive_ingestion_job.tf`
- `infra/variables.tf`
- `scripts/deploy_directive_ingestion.sh`
- Related deployment and ingestion documentation
