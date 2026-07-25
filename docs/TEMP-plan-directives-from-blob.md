# Plan: Azure Blob source for directive ingestion

**Status:** Implemented

**Date:** 2026-07-23
**Updated:** 2026-07-25

## Objective

Move directive source PDFs from the ingestion container image to a dedicated
`directive-source` container in the existing directive storage account. Add an
authenticated right-pane source manager that lists source-blob metadata and
supports PDF upload and deletion without exposing document content or storage
credentials. Preserve the manually triggered ingestion lifecycle and the current
ingestion outputs and runtime contracts so the Directive Assistant, backend
tools, Search index, Cosmos catalog, and artifact reads do not change.

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
- Limit the source manager to metadata listing, create-only PDF upload, and
  confirmed deletion. Do not provide preview, download, rename, or overwrite.
- Route source-manager operations through the authenticated backend and its
  managed identity. Do not give the browser Blob credentials, SAS tokens,
  storage URLs, or direct data-plane access.
- Require a dedicated Entra application role for every source-manager operation;
  the general delegated `access_as_user` scope is not sufficient authorization.
- Keep the Container Apps ingestion job manually triggered. Uploading or deleting
  a source blob must not start, stop, schedule, or otherwise modify the job.
- When manually started in Blob mode, the job lists and reads the current source
  PDFs from `directive-source`.
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
- The backend has read-only artifact access and no source-container repository,
  source-management API, upload limits, or endpoint-specific management role.
- The frontend right rail exposes saved memory only and has no metadata-only
  source manager.
- Nginx has no dedicated upload route, explicit PDF body limit, or streaming
  proxy configuration.

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
- Grant the backend managed identity source-container permissions required to
  list, create, and delete source blobs. Keep its existing read-only access to
  `directive-artifacts`; do not grant artifact write access.
- Do not grant the frontend browser identity or Hosted Agent access to
  `directive-source`.
- Add Terraform variables and outputs for the source container and optional prefix.
- Pass Blob source settings to the Container Apps ingestion job.
- Reuse the existing storage private endpoint and private DNS path; verify no new
  public access is introduced.
- Add and document an endpoint-specific Entra application role such as
  `DirectiveSource.Manage`, assignable to approved users or groups.

### 6. Add the authenticated source-manager API

- Add a backend source-container repository that uses the existing asynchronous
  Blob SDK and managed-identity credential path.
- Add an authenticated metadata-list endpoint with bounded pagination. Return
  only the source filename, byte size, and last-modified time; do not return
  content, account or container coordinates, Blob URLs, ETags, version IDs, or
  storage credentials.
- Add a streaming PDF upload endpoint with a configurable hard size limit.
  Enforce the shared filename contract, `.pdf` extension, PDF signature, and
  configured source prefix before committing the blob.
- Make uploads create-only with a conditional write. Return `409 Conflict` when
  the exact source blob already exists; do not silently overwrite or add a
  general update endpoint.
- Add a delete endpoint restricted to an exact validated source filename and
  require explicit confirmation in the client. Deletion affects only the source
  blob and must not delete or retire catalog records, Search documents, Cosmos
  content, or immutable artifacts.
- Require the dedicated source-management application role on list, upload, and
  delete. Keep this authorization separate from the existing global
  `access_as_user` validation.
- Map expected Blob conflicts and missing blobs to stable HTTP responses, and
  surface storage unavailability as an explicit service error.
- Record safe audit telemetry for actor ID, operation, normalized filename, byte
  size, result, and correlation ID. Do not log PDF content, tokens, storage
  coordinates, or hidden source provenance.
- Do not add an endpoint that starts or modifies the ingestion job, and do not
  wait for ingestion from an upload request.

### 7. Add the right-pane source manager

- Add a `Source documents` mode to the existing responsive right rail without
  removing saved-memory behavior.
- Show a metadata-only, paginated list with filename, size, and last-modified
  time. Do not link rows to the existing directive document/PDF viewer and do
  not expose preview or download actions.
- Add an authorized PDF picker/upload action with progress, validation, duplicate,
  size-limit, and service-error states.
- Add per-row delete with an accessible confirmation step that names the exact
  source file and clearly states that previously ingested content is retained.
- Refresh the list after successful upload or deletion while preserving explicit
  loading, empty, and error states.
- Obtain a coarse `can_manage_directive_sources` capability from the backend;
  hiding controls in the frontend is only presentation, while the API remains
  the authorization boundary.
- Add a dedicated Nginx upload location before the generic `/api/` proxy, set an
  explicit request-size limit, and disable request buffering so accepted PDFs
  stream through the frontend rather than being buffered in full.
- Do not show ingestion status or controls in this manager. Its state represents
  only the contents of the source container.

### 8. Stop packaging source PDFs

- Update the ingestion Dockerfile so directive PDFs are no longer copied into the
  production image.
- Continue packaging the mandatory CSV until its source is changed separately.
- Keep fixture PDFs available to unit and local integration tests.
- Confirm document updates no longer require an image rebuild or job redeployment.

### 9. Add automated coverage

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
- Test source-manager role enforcement, metadata response minimization, bounded
  pagination, streaming upload limits, filename and PDF validation, duplicate
  conflict, exact deletion, missing blobs, and storage failures.
- Test that source upload and deletion never invoke the Container Apps job or
  mutate catalog, Search, Cosmos content, or artifact storage.
- Test the right-rail loading, empty, upload, conflict, delete-confirmation,
  unauthorized, and error states, including the absence of preview and download
  actions.
- Update preflight, CLI, configuration, Nginx, frontend, backend, and Terraform
  tests.

### 10. Migrate and release

1. Provision the source container, RBAC, and job settings without changing the
   active source mode.
2. Deploy the source-manager API and right-rail UI, then verify approved users can
   list, upload, and delete while unapproved users receive `403`.
3. Upload the current fixture PDFs to `directive-source` with their existing names.
4. Verify those uploads did not create or start a Container Apps job execution.
5. Deploy the dual-source ingestion image.
6. Run Blob-mode `preflight` and `validate`.
7. Manually start the ingestion job; the existing hashes should cause all
   already-published PDFs to be skipped.
8. Run the read-only cross-store `verify` command and compare counts with the
   current baseline.
9. Upload one controlled test version, manually start the job, and verify
   extraction, Search publication,
   catalog activation, artifacts, citations, and agent retrieval.
10. Delete the controlled source blob and verify that no job starts and its
    already-published catalog, Search, Cosmos, and artifact data remains intact.
11. Remove production PDFs from subsequent ingestion images after the Blob path has
   completed its soak period.

## Rollback

- Switch the job back to `DIRECTIVE_SOURCE_KIND=local` and deploy the previous image
  containing the fixture PDFs.
- Disable the source-manager routes and UI capability, then revoke the backend's
  source-container role assignment if source management must be rolled back.
- Leave the source container and newly generated immutable artifacts in place.
- No Search, catalog, manifest, backend, or agent schema rollback should be needed.

## Acceptance criteria

- Approved users can list source-blob filenames, sizes, and last-modified times
  without retrieving PDF content or storage coordinates.
- Approved users can upload a valid, uniquely named PDF and delete an exact source
  blob; unapproved users cannot perform any source-manager operation.
- Source-manager actions never start, stop, schedule, or modify the ingestion job.
- A manually started Blob-mode job reads the current source PDFs without requiring
  an image rebuild or job redeployment.
- Deleting a source blob does not retire or delete previously published catalog,
  Search, Cosmos, or artifact data.
- An unchanged corpus produces zero changed documents and no model or Search writes.
- Source PDFs and generated artifacts cannot be confused or recursively ingested.
- Every published manifest still references an immutable source PDF, canonical
  Markdown, ordered sections, summary, and manifest in `directive-artifacts`.
- Current and historical version behavior is unchanged.
- Search and cross-store verification counts remain consistent.
- The ingestion job uses managed identity over the existing private storage path.
- The backend has source-container management access only and remains read-only on
  artifacts; the frontend browser and Hosted Agent have no source-container
  permissions.
- No storage URL, token, ETag, or private source locator appears in agent output.
- Directive Assistant answers and citations remain behaviorally unchanged.

## Primary implementation surfaces

- `setup/directive_ingest/src/directive_ingestion/source.py`
- `setup/directive_ingest/src/directive_ingestion/config.py`
- `setup/directive_ingest/src/directive_ingestion/reconcile.py`
- `setup/directive_ingest/src/directive_ingestion/cli.py`
- `setup/directive_ingest/Dockerfile`
- `setup/directive_ingest/tests/`
- `backend/agent_memory_backend/server.py`
- A dedicated backend source-manager repository/service and related tests
- `backend/agent_memory_backend/auth.py`
- `backend/agent_memory_backend/config.py`
- `frontend/src/app.ts`
- `frontend/src/client.ts`
- A dedicated right-rail source-manager component, styles, and tests
- `frontend/nginx.conf.template`
- `infra/directive_data.tf`
- `infra/directive_ingestion_job.tf`
- `infra/compute.tf`
- `infra/variables.tf`
- `scripts/create_entra_app.sh`
- `scripts/deploy_directive_ingestion.sh`
- Related deployment and ingestion documentation
