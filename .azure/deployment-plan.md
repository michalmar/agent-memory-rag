# Azure Deployment Plan - Azure Blob Directive Source

> **Status:** Validated
>
> **Current release status:** Deployed; cold-load benchmark complete; live rollback/recovery exercise pending

Generated: 2026-07-25T20:33:12Z

## Current release: bounded v3 directive ingestion

Approved: 2026-08-16

This release deploys fail-closed publication-gate awareness before replacing
the current derived v2 directive corpus with the bounded
`directive-v3-bounded-ingestion` output in `directive-chunks-v3`. The cutover is
destructive only for derived directive data: it preserves the
`directive-source` PDF corpus and does not retain parallel indexes, aliases,
fallbacks, or backup generations.

Automated Cosmos item TTL and Blob lifecycle rules remain disabled for this
release. Diagnostic records, extraction caches, quarantine artifacts, and
evidence therefore remain retained until explicitly removed. Retention periods
and lifecycle automation are deferred to a future operational/data-owner review;
stable publication data and source/canonical artifacts must never expire.

### All validation checks pass

- [x] Terraform and Azure CLI installations.
- [x] Authentication, exact subscription, tenant, resource group, and East US
      2 target confirmed.
- [x] Terraform initialization, recursive formatting, and syntax validation.
- [x] Saved Terraform plan reviewed with state access and policy compatibility.
- [x] No unresolved azd Go-template variables.
- [x] Static managed-identity and least-privilege RBAC review.
- [x] Ingestion, contracts, backend, Hosted Agent, frontend, hosting, and
      infrastructure guard suites pass.
- [x] Frontend production build passes.
- [x] No ingestion execution is active and the preserved source corpus matches
      the approved inventory.
- [x] Saved plan contains no unapproved resource deletion or replacement.
- [x] Backend gate-awareness deployment can precede all live data mutation.

### Validation proof

Validated at `2026-08-16T20:02:29Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3, Azure CLI 2.80.0, subscription `ME-MngEnvMCAP372348-mimarusa-1` (`7bc68c68-f434-49ad-ab3e-b883ec39da86`), tenant `a7b1484c-f66a-496a-b1cf-35631a50396c`, and isolated Azure CLI profile confirmed |
| Target | Existing `rg-agent-memory-rag` resources confirmed in East US 2; Search remains in its existing West Europe location |
| Terraform | Initialization, recursive formatting, syntax validation, 98-resource state access, and exact saved-plan contract assertions passed |
| Saved cutover plan | The original combined two-update plan is superseded and must not be applied; staged plans isolate ingestion maintenance configuration from the later backend gate/index switch |
| Drift exclusion | Refreshed preview exposed an unrelated resource-group policy tag and storage-network drift; both are excluded from the approved plan and will not be changed |
| Plan integrity | SHA-256 `68e5e3206f9a75f0be931bd6118997922fa690511c81a1a853d254bb41cac6a1` |
| Azure Policy | Effective subscription and management-group assignments reviewed; the plan creates or deletes no Azure resource and introduces no policy conflict |
| Static RBAC | Backend and ingestion identities retain resource-scoped Search data/service, Cosmos data, Blob data, Document Intelligence, OpenAI, and ACR roles required by their code paths; no role assignment changes are planned |
| Ingestion | 185 tests passed |
| Contracts and backend | 208 tests and 31 subtests passed |
| Frontend | 43 tests, TypeScript compilation, and Vite production build passed |
| Shared hosting and Directive Hosted Agent | 24 hosting tests and 7 Directive Hosted Agent tests passed |
| Infrastructure | Guard fixtures, Bash 3 compatibility self-tests, Bash syntax, Terraform format/validate, saved-plan assertions, and `git diff --check` passed |
| Container build | Local Docker is unavailable; immutable server-side ACR builds are required during deployment |
| Source and job safety | Guarded dry run found exactly 2 protected PDFs, source digest `d6d8b2305c6a6c0de8079c25b5f34933aa20038190fb554e8f94275c07d6d2d4`, and no active ingestion execution |
| Retention | Cosmos item TTL and Blob lifecycle automation remain disabled; future operational/data-owner review is documented |

**Superseded combined plan — do not apply:**
`/Users/mimarusa/.copilot/session-state/bf889d53-eaf5-4931-8116-184e1a7612e3/files/directive-v3-cutover-approved.tfplan`

**Validated by:** `azure-validate`

#### Cold-load infrastructure recreation validation proof

Validated at `2026-08-17T05:54:49Z`.

| Check | Result |
| --- | --- |
| Target | User confirmed subscription `ME-MngEnvMCAP372348-mimarusa-1` (`7bc68c68-f434-49ad-ab3e-b883ec39da86`) and the existing East US 2 resource group |
| Empty-state prerequisite | Evidence SHA-256 `f841ed31115e64b3d0c130e9ff36cc8cd293b32ce8df2aa24cea5ad978ca01c0`; no directive Cosmos containers, no current source/artifact blobs, no `directive-chunks-v3`, no active execution, and the job remains in maintenance |
| Terraform | Initialization, recursive format check, syntax validation, default workspace, and 98-resource state access passed |
| Saved plan | Exactly 3 additions, 0 changes, and 0 deletions: `catalog`, `directive_content`, and `user_mandates` |
| Schema | Planned partition keys are exactly `/directive_id`, `/directive_version_id`, and `/user_id`; the content container retains its content-field indexing exclusion |
| Drift exclusion | A temporary deployment-only override ignores only the policy-owned resource-group `SecurityControl` tag; the tag and every unrelated resource are absent from the saved plan |
| Azure Policy | The isolated Azure CLI profile found no effective assignments at the resource-group scope; Azure MCP policy lookup used a different identity and was not accepted as evidence |
| Plan integrity | SHA-256 `69e5770930eb149697144198a354a1b955f3ddce3c55fb61b46661b4e0133398` |

**Validated by:** `azure-validate`

Applied at `2026-08-17T05:57:14Z`.

| Deployment check | Result |
| --- | --- |
| Terraform apply | 3 resources added, 0 changed, and 0 destroyed from the saved plan |
| Restored resources | Empty `catalog`, `directive_content`, and `user_mandates` containers are present in Azure and Terraform state with exact partition keys and no default TTL |
| Cold-load boundary | Source and artifact containers still contain 0 current blobs; `directive-chunks-v3` remains absent; no ingestion execution started |
| Runtime safety | Ingestion job remains idle in `maintenance`; its database-scoped Cosmos contributor assignment is present |
| Drift review | Full post-apply plan contains only the previously documented Storage firewall drift and no Cosmos change; that unrelated drift was not applied |
| Policy tag | Resource-group `SecurityControl=Ignore` remains intact; the temporary deployment override was removed |
| Endpoint | `https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/` returned HTTP 200 |

#### Cold-load initial-ingestion benchmark

Measured from an empty derived-data baseline on `2026-08-17` UTC.

| Boundary | Result |
| --- | --- |
| Cold baseline | 2 newly uploaded PDFs / 365,420 bytes; 0 artifact blobs; empty `catalog`, `directive_content`, and `user_mandates`; no `directive-chunks-v3`; idle maintenance job |
| Immutable runtime | ACR build `ch56` succeeded in 45 seconds; image digest `sha256:48b246120bf5b581dc932af80b7d6b7fc193a4dd53b0479cac57439391b6826d` |
| Validation orchestration | 419 seconds; bootstrap 41 seconds, gate bootstrap 37 seconds, preflight 41 seconds, and validation 35 seconds at the Container Apps execution boundary |
| Interrupted publication | The first 136-second local wrapper attempt ended with SIGPIPE after a successful 30-second idempotent Search bootstrap. No approval was reserved and no `run-daily` publication was dispatched; this was an operator-shell failure, not an ingestion or Azure execution failure |
| Successful publication | Retry completed in 418 seconds; bootstrap 37 seconds, gate bootstrap 34 seconds, and `run-daily` 143 seconds at the Container Apps execution boundary |
| End-to-end timing | 1,126 seconds (18m 46s) from frozen baseline to verified completion: 973 active orchestration seconds plus 153 seconds of operator handoff/inspection. The successful validation-plus-publication path used 837 active seconds (13m 57s) |
| Outcome | 8/8 Azure job executions and 8/8 durable metric records succeeded; publication execution `job-agmem-directive-ingest-bywwn5r`, run `20260817T061742Z-ebcee5e8`, activation result `success` |

Validation performed the cold extraction and populated the extraction cache.
Publication then reused both cache entries.

| Measured quantity | Result |
| --- | --- |
| In-job source transfer | 4 downloads / 730,840 bytes across validation and publication |
| Operator integrity rehashing | 4 inventory refreshes, 8 downloads / 1,461,680 bytes outside ingestion-run metrics |
| Extraction cache | 2 misses and 2 fallbacks during validation; 2 hits during publication; 0 invalidations |
| Document Intelligence | 9 requests total, including preflight; 6 validation polls |
| Summary model | 2 requests; 11,824 input tokens and 6,423 output tokens |
| Embeddings | 2 requests; 48 items and 11,788 input tokens |
| Search | 54 requests, 41 queries, 38 visibility polls, and 144 publication actions; final authoritative document count is 48 |
| Retries and throttles | 0 total retries; 0 Document Intelligence, OpenAI, or Search throttles |
| Failures | 0 Azure execution failures and 0 durable ingestion failures; 1 recovered local shell-output failure before publication dispatch |
| Peak memory | 231,534,592 bytes (220.8 MiB) during `run-daily` |

Individual operator integrity-refresh duration was not separately instrumented;
it remains included in the orchestration wall-clock measurements.

Stage durations are internal application timings. Concurrent per-document stages
overlap, so these values must not be summed to derive wall time.

| Stage | Validation | Cold `run-daily` |
| --- | ---: | ---: |
| Total internal duration | 5,612 ms | 117,270 ms |
| Planning | 5,599 ms | - |
| Source listing | 708 ms | 34 ms |
| Download | 55 ms | 58 ms |
| Cache lookup | 18 ms | 84 ms |
| Cache write | 97 ms | - |
| Extraction | 9,100 ms aggregated across concurrent documents | - |
| Metadata | 7 ms | - |
| Canonicalization | 50 ms | 50 ms |
| Chunking | - | 16 ms |
| Summary | - | 60,266 ms |
| Embedding | - | 659 ms |
| Staging | - | 5,725 ms |
| Blob staging | - | 254 ms |
| Cosmos staging | - | 3,376 ms |
| Search publication | - | 9,444 ms |
| Catalog publication | - | 169 ms |
| Activation | - | 9,898 ms |
| Verification | - | 14,710 ms |
| Cleanup | - | 5,068 ms |
| Activation gate end-to-end | - | 39,817 ms |

Final exact verification found 2 directives and versions, 2 current pointers, 48
current Search documents, 82 content items, 4 required publication artifacts,
2 source-state records, and the active empty mandate snapshot with 0 assignments.
The artifact container has 13 current blobs when extraction cache, approval,
source-inventory, source-state, and validation evidence records are included.
The publication gate is `committed`, no execution is active, the job is restored
to `directive-ingest maintenance`, and the frontend returns HTTP 200. Cosmos
default TTL and Blob lifecycle automation remain disabled.

The current metrics schema declares `catalog_reads`, `catalog_writes`,
`blob_reads`, `cosmos_reads`, and `cosmos_writes`, but the implementation does
not increment those counters. Exact request counts for those stores cannot be
reconstructed from this run; the exact final object counts above remain verified.

| Evidence | SHA-256 |
| --- | --- |
| `cold-load-ingestion-baseline.json` | `fcf7c7a49901204f9556d5b7042a1bdf6181af93cf32ebb714dad99433de1052` |
| `cold-load-validation-evidence.json` | `dc545b15b82d21c7743bc48e3ccc9cc68cfda7205769bb966cd4b45e5f7d924a` |
| `cold-load-run-metrics.json` | `e08367b46a71e81833232cd2d3418bf1db843b9957c89a1f20ea819af29e407b` |
| `cold-load-publication-verification.json` | `1832ae80483359b5408f15892f5b974c053e84618d11e63f3435228364dc5f8c` |
| `cold-load-final-state.json` | `a7e6071f9010d122ee1ce2710d051dd24f6721047ba81467c51bc49babb74357` |
| `cold-load-benchmark-report.json` | `b7d1aabee62fdce9f37e65758676cb9a9902fe81d0f19af8727a1fe7bc189469` |

#### Split staging validation proof

Validated at `2026-08-16T21:32:37Z`.

| Check | Result |
| --- | --- |
| Staging plan | 0 additions, 1 in-place update, 0 deletions, and 0 replacements; only `azurerm_container_app_job.directive_ingestion` changes |
| Safety boundary | Job command remains `directive-ingest maintenance`; backend remains on `directive-chunks-v2` with strict gate reads disabled |
| Environment | Processing changes from `directive-v2-czech-layout` to `directive-v3-bounded-ingestion`, Search from `directive-chunks-v2` to `directive-chunks-v3`, and only the reviewed bounded provider/concurrency/table settings are added |
| Terraform | Init, recursive format check, syntax validation, state access, exact action assertions, and unresolved-template checks passed |
| Azure Policy | Effective management-group and subscription assignments reviewed; the plan changes no resource type, SKU, location, network, identity, role, or tag |
| Static RBAC | Existing job identity retains resource-scoped ACR pull, source Blob read, artifact Blob write, Cosmos data contributor, Search service/index contributor, Document Intelligence user, and OpenAI user roles |
| Plan integrity | SHA-256 `5eaa03154f87e10c900084d73e0aee6798703a0cc1889302df9c8e31718cf645` |

**Validated staging plan:**
`/Users/mimarusa/.copilot/session-state/bf889d53-eaf5-4931-8116-184e1a7612e3/files/directive-v3-job-stage.tfplan`

#### Backend strict-gate validation proof

Validated at `2026-08-16T22:24:31Z`.

| Check | Result |
| --- | --- |
| Backend plan | 0 additions, 1 in-place update, 0 deletions, and 0 replacements; only `azurerm_container_app.backend` changes |
| Runtime safety | Live image `backend:v3-bounded-20260816-2` is identical before and after the plan; all non-environment fields are unchanged |
| Environment | Adds `DIRECTIVE_PUBLICATION_GATE_ENABLED=true` and changes only `DIRECTIVE_SEARCH_INDEX` from `directive-chunks-v2` to `directive-chunks-v3`; every other environment value is unchanged |
| Drift exclusion | Refreshed planning used temporary plan-only lifecycle overrides for the already-reviewed resource-group tag and storage-network drift; the overrides were removed and Terraform formatting and validation passed again |
| Superseded plans | `directive-v3-backend-strict.tfplan` includes unrelated drift and `directive-v3-backend-strict-isolated.tfplan` preserves a stale backend image; neither may be applied |
| Terraform | Terraform 1.13.3, Azure CLI 2.80.0, initialization, recursive format check, syntax validation, 98-resource state access, exact JSON action assertions, and unresolved-template checks passed |
| Build and regression | Backend suite passed with 180 tests and 31 subtests; ingestion suite passed with 195 tests |
| Azure validation | Managed-identity preflight and metadata-only validation succeeded for both protected PDFs on `directive-ingestion:v3-bounded-20260816-7`; fresh approval evidence was written without publishing data |
| Azure Policy | Effective management-group and subscription assignments were reviewed; the plan updates only two environment values on an existing Container App and creates, deletes, scales, exposes, or re-roles no resource |
| Static RBAC | Backend identity retains resource-scoped Search Index Data Reader, directive Cosmos data reader, directive artifact Blob reader, source Blob contributor, OpenAI user, ACR pull, and telemetry roles; no role assignment changes are planned |
| Plan integrity | SHA-256 `16ac9f963fbd45ff6fc400ab8a1384d77c63756ec8b33d54d9e418198f765fcb` |

**Validated backend plan:**
`/Users/mimarusa/.copilot/session-state/bf889d53-eaf5-4931-8116-184e1a7612e3/files/directive-v3-backend-strict-final.tfplan`

**Validated by:** `azure-validate`

#### Backend strict-gate deployment proof

Verified at `2026-08-16T22:31:31Z`.

| Item | Result |
| --- | --- |
| Apply | Exact SHA-256-verified saved plan applied: 0 additions, 1 in-place update, 0 deletions |
| Revision | `ca-agmem-backend--0000068` is healthy, running at max scale, and receives 100% traffic |
| Image | Immutable `backend:v3-bounded-20260816-2` remained unchanged |
| Strict reads | Live environment has `DIRECTIVE_PUBLICATION_GATE_ENABLED=true` and `DIRECTIVE_SEARCH_INDEX=directive-chunks-v3` |
| Readiness | Public `https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/api/health/ready` returns ready with no degraded dependencies |
| Live RBAC | Backend identity has ACR pull, Search index read, directive artifact read, source Blob contribution, OpenAI use, telemetry publishing, Foundry agent consumption, directive Cosmos read, and support/account Cosmos contribution at the reviewed scopes |
| Ingestion safety | The ingestion job remains idle and pinned to nonpublishing maintenance mode |
| Targeted output warning | Terraform warned that outputs may remain stale after a targeted apply; the stale `directive_search_index_name` output was ignored and the live Container App environment was independently verified as v3 |

#### Hosted Agent and authenticated directive-path deployment proof

Verified at `2026-08-16T22:38:45Z`.

| Item | Result |
| --- | --- |
| Support Hosted Agent | Version 10 is active on `customer-support-maf-hosted:v3-bounded-20260816-1`, digest `sha256:6b6252b48e6908d0818edc53abe04653c39bbfc9e10907d9fdd42812391a2496`; direct remote invocation returned `OK` |
| Directive Hosted Agent | Version 6 is active on `directive-rag-maf-hosted:v3-bounded-20260816-1`, digest `sha256:fb680b9f1e2a2d089a9ce50a4c272b81bb30a3df1a44cad3e6d13838f7815077`; its principal remains in the exact directive-agent allowlist |
| Frontend | Revision `ca-agmem-frontend--0000032` is ready on `frontend:v3-bounded-20260816-1` |
| Authenticated directive path | A delegated-user POST to `/api/chat` with `agent_type: "directive-rag"` created conversation `f42671c0-e33b-4f96-9c06-f48c6199c0df`, bound the stateful runtime, returned exactly `OK`, and emitted `RUN_FINISHED` |
| Direct directive invocation | A direct CLI invocation was rejected by `/api/internal/agent-state/resolve` with `403`, the expected fail-closed result because that path bypasses the backend-created application session binding |
| Data safety | No directive data was published or deleted |

#### Terraform v3 output-state validation proof

Validated at `2026-08-16T22:45:00Z`.

| Check | Result |
| --- | --- |
| Saved plan | Changes only the stored `directive_search_index_name` output from `directive-chunks-v1` to `directive-chunks-v3` |
| Resource safety | Exact JSON inspection found zero resource actions, additions, updates, deletions, or replacements |
| Drift exclusion | Temporary plan-only lifecycle overrides excluded the already-reviewed resource-group tag and storage-network drift; the override file was removed before apply |
| Terraform | Recursive format check, syntax validation, 98-resource state access, exact output assertions, and unresolved-template checks passed after override removal |
| Policy and RBAC | No Azure resource or role-assignment action exists in the plan, so Azure Policy and live RBAC are unchanged |
| Plan integrity | SHA-256 `f7ab2a8ac003f7ffde1eb59d756a593d4b3854702ba0e4f62def05cf71f6726a` |

**Validated output-state plan:**
`/Users/mimarusa/.copilot/session-state/bf889d53-eaf5-4931-8116-184e1a7612e3/files/directive-v3-output-refresh.tfplan`

**Validated by:** `azure-validate`

#### Terraform v3 output-state deployment proof

Verified at `2026-08-16T22:47:00Z`.

| Item | Result |
| --- | --- |
| Apply | Exact SHA-256-verified saved plan applied: 0 additions, 0 changes, 0 deletions |
| Output | Both `terraform output` and the remote state now report `directive_search_index_name=directive-chunks-v3` |
| Runtime | Backend remains on revision `ca-agmem-backend--0000068`, strict gate reads remain enabled, and its live Search index remains `directive-chunks-v3` |
| Ingestion | Job remains on the approved `-7` digest, configured for v3, manual, idle, and in nonpublishing maintenance mode |
| Readiness | Public readiness remains `ready` with every dependency `ok` |
| Data safety | The state-only apply performed no Azure resource action and published or deleted no directive data |

### Deployment sequence

1. [x] Deploy backend publication-gate awareness with strict reads disabled while
   v2 remains committed.
2. [x] Apply the validated job-only staging plan above.
3. [x] Build and pin the bounded v3 ingestion job in nonpublishing maintenance
   mode.
4. [x] Bootstrap the v3 Search schema and a committed gate matching live v2.
5. [x] Run managed-identity preflight and metadata-only validation.
6. [x] Validate and apply a separate backend-only strict-gate/v3-index plan.
7. [x] Deploy the four-tool Directive Hosted Agent and frontend/runtime updates.
8. [x] Confirm the guarded destructive reset of v2/v3 derived data.
9. [x] Regenerate the complete v3 corpus once from preserved source PDFs.
10. [x] Run deep source audit, exact cross-store verification, retrieval/citation
   acceptance, and unchanged-rerun checks.
11. Inject an activation failure, verify compensation or
   `recovery_required`, recover, and rerun acceptance.
12. [x] Confirm no legacy directive data or retired model-visible tools remain.

#### Destructive reset and bounded v3 publication proof

Verified on `2026-08-17` UTC.

| Item | Result |
| --- | --- |
| Reset boundary | Deleted and recreated only the three derived Cosmos containers, purged only derived Blob prefixes, and deleted the v2/v3 directive Search indexes before regeneration |
| Source protection | Both source PDFs remained byte-identical at 183,445 and 181,975 bytes; inventory digest remained `d6d8b2305c6a6c0de8079c25b5f34933aa20038190fb554e8f94275c07d6d2d4` |
| Retention | Recreated Cosmos containers have `defaultTtl=null`; the storage account has no Blob management policy; existing soft-delete/versioning settings were not changed |
| Publication | `job-agmem-directive-ingest-jus8sd6` published the two-document corpus with immutable image digest `sha256:abe9e5741ab340da32fa903ca5565a770bdedda8354cfc0f039e0dcafc508a9f` |
| Evidence recovery | Read-only execution `job-agmem-directive-ingest-bdrdxf7` recovered exact publication evidence after the local wrapper failure without republishing |
| Exact corpus | 2 directives, 2 versions/current pointers, 48 current Search chunks, 82 content sections/parts, 4 required artifacts, 2 source-state records, and the approved empty mandate snapshot |
| Publication evidence | `directive-v3-publication-verification.json`, SHA-256 `0c986a926d3d75cde825efd4893bff85935f6d5182e244e68865ccb6a26817b0` |

#### Unchanged-corpus and performance proof

| Item | Changed publication | Unchanged acceptance |
| --- | --- | --- |
| Image | `sha256:abe9e5741ab340da32fa903ca5565a770bdedda8354cfc0f039e0dcafc508a9f` | `sha256:f371bc1a5c1a778e32983a42f77fe41ce682d585edd5da09db47916bdac214da` |
| Run | `20260817T033512Z-5a9e76ca` | `20260817T040041Z-c531f5bf` / execution `job-agmem-directive-ingest-6w8tm47` |
| Result | `succeeded`, 2 changed | `skipped`, 2 skipped |
| Source body transfer | 2 downloads / 365,420 bytes | 0 downloads / 0 bytes |
| Publication work | 2 Blob writes, 48 embedded items, 144 Search actions | No Blob, model, embedding, catalog, Cosmos, or Search publication-write counters |
| Duration | 123,338 ms | 32,065 ms |
| Peak RSS | 231,620,608 bytes | 183,250,944 bytes |

The unchanged workflow reduced measured wall time by **74.0%**, exceeding the
60% acceptance target while preserving all cross-store identity digests.
Persisted metrics are in `directive-v3-run-metrics.json`, SHA-256
`4a04a4b0b398a3e0deb63fcd3b00836cb77d2ea9136de93745c0946c59e4de2f`.

#### Deep source-audit and online acceptance proof

Verified on `2026-08-17` UTC.

| Item | Result |
| --- | --- |
| Deep audit | Execution `job-agmem-directive-ingest-jgqfm7h`, run `20260817T043225Z-5ae9016e`, used `verification_mode=deep-source-audit`, redownloaded and rehashed both PDFs (365,420 bytes), and completed with zero warnings |
| Cross-store identities | Catalog `2f525347...fdefa`, Search `cf734d37...ce846`, content `1be4798f...48370`, artifacts `372c03b6...25c4`, source state `68b8a3cb...0309`, mandates `4f53cda1...2b945` |
| Deep-audit evidence | `directive-v3-deep-audit-verification.json`, SHA-256 `acdda2a4f68042eacc63d31a636ef8b8c0acf1824c967164c45d8e7bdb612e25` |
| Discovery retrieval | Authenticated conversation `16fb2d0b-1904-47d2-ae5b-817f99169f9e` discovered both directives, exercised all four approved tools, emitted 11 section/page citations, returned the complete empty mandate snapshot, and finished successfully |
| Focused retrieval | Same bound conversation filtered Search to `MP/25/0277`, read exact section content, correctly distinguished public ChatGPT from M365 Copilot classification rules, emitted 6 narrow citations, and returned `non_mandatory` |
| Exact routes | Both exact Markdown and PDF routes returned `200`; PDF SHA-256 values matched weak identity ETags and protected source hashes; both conditional PDF requests returned `304` |
| Hosted Agent | Active version 6 uses image digest `sha256:fb680b9f1e2a2d089a9ce50a4c272b81bb30a3df1a44cad3e6d13838f7815077` and exports exactly `get_directive`, `search_directives`, `get_directive_content`, and `get_user_directive_mandates` |
| Legacy cleanup | The only directive Search index is `directive-chunks-v3`; exact container counts contain no extra directive/version/content identities; retired tool names are absent from the deployed image |
| Online evidence | `directive-v3-online-acceptance.json`, SHA-256 `d355120398164f05b39bd2532b086e6bd7d426f67822b98233f3c3edcbe31e1e`; exact-route evidence SHA-256 `1b69a3f05ab114e318b49c1e5497788dacee75630b74c7f89756c66c11afebe2` |

The ingestion job is manual, idle, pinned to the accepted `-8` digest, and
restored to `directive-ingest maintenance`.

#### Remaining acceptance

The only live rollout item not yet exercised is controlled activation failure,
successful rollback, forced `recovery_required`, and operator recovery. No safe
production fault-injection hook is currently deployed. Do not mutate live
catalog/Search/gate state to satisfy this item without a separately reviewed
method and explicit destructive confirmation.

## Current release: directive document processing v2

Deployed and verified: 2026-08-15

This release destructively rebuilds only derived directive data from the
preserved `directive-source` corpus. It deploys the v2 ingestion runtime,
publishes to `directive-chunks-v2`, and deletes the complete legacy
`directive-kb-v1` -> `directive-chunks-ks-v1` -> `directive-chunks-v1` graph
only after fresh cross-store verification succeeds.

### Validation steps

- [x] Run complete ingestion, contracts, backend, and frontend test suites.
- [x] Build the frontend production bundle.
- [x] Validate Terraform formatting and syntax.
- [x] Run Bash 3.2 infrastructure guards, Bash syntax, and ShellCheck.
- [x] Confirm the exact Azure subscription, tenant, resource group, storage
      account, source corpus, and ingestion job.
- [x] Confirm no ingestion execution is active.
- [x] Produce and review a saved Terraform cutover plan.
- [x] Verify the plan contains no delete or replacement action.
- [x] Statically verify the operator's source-container reader assignment.
- [x] Confirm the guarded reset preserves `directive-source`.
- [x] Approve an explicitly empty mandate mapping for the initial two-PDF
      rollout.

### Validation proof

Validated at `2026-08-15T06:23:22Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Target subscription `7bc68c68-f434-49ad-ab3e-b883ec39da86`, tenant `a7b1484c-f66a-496a-b1cf-35631a50396c`, and isolated Azure CLI profile confirmed |
| Integrated review | Final integration tip `d555543195c13cab3f545d7fa1a83866366202a6`; independent code review approved |
| Ingestion and contracts | 157 ingestion, 20 directive-contract, and 6 agent-contract tests passed |
| Backend | 175 tests and 30 subtests passed |
| Frontend | 40 tests, TypeScript compilation, and Vite production build passed |
| Infrastructure | Terraform format/validate, Bash 3.2 guards, Bash syntax, directive-script ShellCheck, and `git diff --check` passed |
| Source corpus | 2 PDFs, 365,420 bytes total; source container is preserved |
| Job safety | 0 active executions; current job is manual and will be moved to nonpublishing `maintenance` mode |
| Saved cutover plan | 1 create, 2 updates, 0 deletes, 0 replacements |
| Planned resources | Backend Search index to `directive-chunks-v2`; ingestion job to `maintenance` with `directive-v2-czech-layout` and `directive-chunks-v2`; add operator `Storage Blob Data Reader` on `directive-source` |
| Excluded drift | Resource-group tag and storage-network drift are excluded from this cutover plan |
| Plan integrity | SHA-256 `61f3617bf8c1ff4d2bc78e82dc29f0774c86b66221a9e67630d695e51de8596d` |
| Mandates | Explicit empty mapping approved for this rollout |
| Container build limit | Local Docker daemon unavailable; static Docker-context/import checks passed and immutable ACR build is required during deployment |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/d55001a6-b789-4936-a5b1-c2fcceeb558c/files/directive-v2-cutover.tfplan`

**Validated by:** `azure-validate`

### Deployment sequence

1. [x] Apply only the saved cutover plan above.
2. [x] Build and pin the v2 ingestion image in ACR.
3. [x] Run managed-identity `preflight`, metadata-only `validate`, and inspect
   the normalized IDs and warnings.
4. [x] Run the guarded derived-data reset while preserving `directive-source`.
5. [x] Publish once with the approved empty mandate mapping.
6. [x] Run two fresh pinned cross-store verifications and smoke tests.
7. [x] Prove a second unchanged run performs no paid or publication work.
8. [x] Delete the complete v1 Search graph only through guarded finalize.

### Deployment proof

Verified on 2026-08-15 UTC.

| Item | Result |
| --- | --- |
| Published corpus | `MP/23/0141:v1` and `MP/25/0277:v1.1` |
| Search | `directive-chunks-v2` contains exactly 45 documents; all 45 are current |
| Cross-store state | 2 catalog versions, 2 current pointers, 82 content records, 4 required artifacts, and 2 source-state records |
| Mandates | Active empty snapshot, 0 assignments, checksum `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Source protection | The 2 approved PDFs remain unchanged at 183,445 and 181,975 bytes |
| Validation digest | `79421579a91668b58ad89f6a419aaf0220f9d90d55554bafe9048232b7dba22c` |
| Published state digest | `a62d5c8bb6a2c0ace77ccab451eb0ba9415531c5f62b5d3980cfa59d9c2d8c30` |
| Idempotency | Unchanged rerun skipped both sources with 0 changed documents and no mandate change |
| Ingestion | Image `sha256:1262bfb27657b5c89a5b26fa2e9a787e45a56fbcb341d02e65236422dbab2709`; job remains `directive-ingest maintenance` |
| Backend | Revision `ca-agmem-backend--0000063` on `sha256:f1c7c8a841377d259a736eb732e19dff94a57bc1e1f20be1b9f96584816d05c5`, healthy and latest-ready |
| Frontend | Revision `ca-agmem-frontend--0000030` on `sha256:478478382ee418e807eb8d05078b4e3dc92ad4ba3f3f04516af96bae97fbe50e`, healthy and latest-ready |
| Directive Hosted Agent | Version 5 active on `sha256:9fa233316235d92a03026538a5e72c9f91eeb85aad043985e2eb774a0d3ff557` |
| Live acceptance | Readiness, both exact Markdown/PDF routes, weak-ETag `304`, and the authenticated directive-agent chat path succeeded |
| Legacy cleanup | `directive-kb-v1`, `directive-chunks-ks-v1`, and `directive-chunks-v1` are absent; v2 remains present |

## Current release: Hosted Agent readiness deadlock

Deployed: 2026-08-14

Backend readiness previously required the support Hosted Agent runtime. That
runtime initializes by calling its MCP tool through the public frontend and
back into the backend. Container Apps withheld backend routing while readiness
was 503, so MCP initialization timed out and the runtime could never recover.

The support Hosted Agent runtime is now reported as an optional degraded
dependency during initialization. Gateway authorization, Cosmos DB, Search,
directive data, and other backend dependencies remain required. Once backend
routing becomes available, the existing retry loop initializes the Hosted
Agent and clears the degraded status.

### Validation proof

| Check | Result |
| --- | --- |
| Regression tests | 29 tests and 7 subtests passed |
| Review | Five-axis review found no required issues |
| Failure reproduction | Direct Hosted Agent invocation failed because MCP initialization through `/api/mcp/` timed out while backend readiness was 503 |
| Safety boundary | `hosted_tool_gateway` remains required; only `foundry_hosted_maf` is callback-dependent and optional during recovery |

### Deployment proof

Verified at `2026-08-14T12:31:14Z`.

| Item | Result |
| --- | --- |
| Commit | `22f3c330b03e6a6560705e57f42ece6041c0ae6c` |
| Workflow | [31800291794](https://github.com/michalmar/agent-memory-rag/actions/runs/31800291794) succeeded; backend only |
| Backend image | `agmem5df652acr.azurecr.io/backend:22f3c330b03e6a6560705e57f42ece6041c0ae6c-31800291794-1` |
| Revision | `ca-agmem-backend--0000061`, running and latest-ready |
| Public readiness | HTTP 200 with status `ready` |
| Recovery behavior | `foundry_hosted_maf` was briefly degraded, then recovered to `ok`; `degraded_dependencies` is empty |
| Hosted Agent | Direct remote invocation completed successfully and returned `OK` |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

## Current release: portable existing-environment Terraform

Approved: 2026-08-14

This release makes the repository the environment-neutral development master
while preserving the currently deployed Azure environment. Model deployment
SKUs become explicit Terraform inputs, existing directive-model adoption uses
Terraform state rather than source-file mutation, new snapshots no longer track
target mandate identities, and Hosted Agent manifests resolve endpoints and
immutable images from each azd environment. Historical commits still contain
the former demo mapping and require a separately approved history rewrite if
the repository's sharing boundary changes.

- [x] Read current model names, versions, SKUs, and capacities from live Azure.
- [x] Make the current environment values explicit in ignored
      `infra/terraform.tfvars`.
- [x] Confirm the existing directive model is already managed in Terraform
      state, so no import is needed for this environment.
- [x] Remove committed Azure endpoints, image tags, and mandate identities.
- [x] Add a target-local mandate workflow with missing, sample, and empty-file
      safety gates.
- [x] Run Terraform formatting and syntax validation.
- [x] Run targeted directive-ingestion tests and package validation.
- [x] Produce and review a saved zero-destruction Terraform plan.
- [x] Apply the exact reviewed plan.
- [x] Verify live model configuration, RBAC, frontend service, and post-apply
      Terraform drift.
- [x] Diagnose the application readiness failure as the existing
      `foundry_hosted_maf` dependency, independent of the zero-change apply.
- [x] Commit and push the portable source changes.

### Validation steps

- [x] Terraform and Azure CLI installations
- [x] Azure authentication and confirmed subscription
- [x] Terraform initialization and state access
- [x] Recursive Terraform format check
- [x] Terraform syntax validation
- [x] Saved Terraform plan preview and integrity hash
- [x] Azure policy assignment review
- [x] No unresolved azd Go-template variables
- [x] Static model and RBAC configuration review
- [x] Directive-ingestion tests and handover package verification

### Validation proof

Validated at `2026-08-14T12:01:00Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3, Azure CLI 2.80.0, and confirmed subscription `7bc68c68-f434-49ad-ab3e-b883ec39da86` |
| Existing target | `rg-agent-memory-rag` and Container Apps environment `cae-agmem-5df652` are healthy in East US 2 |
| Live model configuration | Chat `GlobalStandard`/30, embedding `Standard`/30, and directive `GlobalStandard`/250 exactly match explicit local variables |
| Terraform | Initialization, recursive formatting, syntax validation, and 97-resource state access passed |
| Saved plan | No changes: 0 added, 0 changed, 0 destroyed |
| Plan integrity | SHA-256 `b104f47511adb3b7b8188b250640007130bbe2f8e5db2ea25109f67c943077b1` |
| Azure Policy | Six effective subscription policy assignments reviewed; no conflict with the no-change apply |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Tests | 21 directive-ingestion tests passed; modified shell scripts passed syntax validation |
| Package | Sanitized handover archive built, checksum-verified, and contained only environment placeholders |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/09c77802-79f9-4826-888c-c4bd3567d363/files/portable-env-validation.tfplan`

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-08-14T12:02:00Z`.

| Item | Result |
| --- | --- |
| Terraform apply | Exact reviewed plan applied: 0 added, 0 changed, 0 destroyed |
| Post-apply drift | No changes |
| Live model deployments | All three deployments remain `Succeeded` with the configured versions, SKUs, and capacities |
| Live RBAC | Backend, frontend, and ingestion identities retain ACR-scoped `AcrPull` |
| Frontend | Latest revision `ca-agmem-frontend--0000029` is running and serves HTTP 200 |
| Readiness | Existing backend reports HTTP 503 because `foundry_hosted_maf` fails; Search and directive retrieval checks remain successful |
| Causality | The readiness condition predates and is unchanged by this Terraform apply, which made no Azure changes |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`

## Current release: selective GitHub application deployment

Approved: 2026-08-11

This CI/CD-only release adds one GitHub Actions workflow that builds and deploys
the backend and frontend independently. Backend code, shared contracts, and the
root Docker context select the backend; frontend code selects the frontend.
Manual runs can target either component or both. Azure infrastructure and
application source remain unchanged.

- [x] Detect backend-only, frontend-only, and combined push ranges.
- [x] Treat cross-component renames as delete plus add.
- [x] Serialize backend and frontend deployments independently.
- [x] Check out current `main` only after acquiring the component lock.
- [x] Build with the existing ACR Tasks Docker contexts.
- [x] Wait for the expected ready revision and verify public health.
- [x] Configure and verify GitHub OIDC secrets, variables, federation, and RBAC.
- [x] Validate workflow syntax, embedded shell, selectors, and deployment roles.
- [x] Merge the workflow to `main`.
- [x] Run backend-only and frontend-only deployments.
- [x] Confirm each deployment leaves the untouched Container App image unchanged.
- [x] Record workflow runs, revisions, image references, and live acceptance.

### Validation proof

Validated at `2026-08-11T09:36:00Z`.

| Check | Result |
| --- | --- |
| Workflow | `actionlint` passed |
| Embedded shell | All 7 workflow shell blocks passed `bash -n` |
| Push selection | Historical backend-only, frontend-only, and combined commit ranges passed |
| Manual selection | `backend`, `frontend`, and `all` inputs produced the expected matrices |
| Change boundaries | Cross-component rename and root `.dockerignore` targeting passed |
| GitHub configuration | Three OIDC secrets and five Azure resource variables are configured |
| Azure target | `ME-MngEnvMCAP372348-mimarusa-1` / `rg-agent-memory-rag` in `eastus2` is provisioned |
| Deployment identity | `id-agmem-github-5df652` trusts only `repo:michalmar/agent-memory-rag:ref:refs/heads/main` and has resource-group `Contributor` |
| Image pull | Backend and frontend managed identities retain ACR-scoped `AcrPull` |
| Change scope | No Terraform or application-source change; CI/CD workflow and documentation only |

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-08-11T09:46:00Z`.

| Item | Result |
| --- | --- |
| Pull request | [#2](https://github.com/michalmar/agent-memory-rag/pull/2) merged as `66e684374890817d294637db7031b7e2d81b989c` |
| Backend-only run | [31478710782](https://github.com/michalmar/agent-memory-rag/actions/runs/31478710782) succeeded; only `deploy (backend)` ran |
| Frontend-only run | [31478990188](https://github.com/michalmar/agent-memory-rag/actions/runs/31478990188) succeeded; only `deploy (frontend)` ran |
| Backend isolation | Backend changed from `202607252050-blobsource`; frontend remained `session-agents-20260731125529` |
| Frontend isolation | Frontend changed from `session-agents-20260731125529`; backend retained the image from run `31478710782` |
| Backend image | `agmem5df652acr.azurecr.io/backend:66e684374890817d294637db7031b7e2d81b989c-31478710782-1` (`sha256:6b658e856fa70488bd13c5e95250765e3bfe2699010e8b1187357888cbbec295`) |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:66e684374890817d294637db7031b7e2d81b989c-31478990188-1` (`sha256:cabdb3633a6b29801c1f9378bfbab6ca9408cc8a007a03d91e8c338cf3c0ec26`) |
| Revisions | Backend `ca-agmem-backend--0000060` and frontend `ca-agmem-frontend--0000028` are healthy, running, and latest-ready |
| Live acceptance | Frontend returned HTTP 200; `/api/health/ready` returned HTTP 200 with status `ready` and every dependency `ok` |
| Live RBAC | Deployment identity retains resource-group `Contributor`; both Container App identities retain ACR-scoped `AcrPull` |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`

## Current release: session agent indicators

Approved: 2026-07-31

This frontend-only release adds a distinct agent badge to every session in the
left thread panel. The badge uses the persisted agent label and matching icon,
with a clear fallback for legacy sessions that do not have runtime metadata.
Azure infrastructure, backend, subscription, and region remain unchanged.

- [x] Add typed agent indicator presentation metadata.
- [x] Render and style the agent badge in every session row.
- [x] Cover persisted, inferred, and legacy agent metadata.
- [x] Run the targeted frontend tests and production build.
- [x] Validate the image-only release against the existing Terraform state.
- [x] Build and deploy a uniquely tagged frontend image.
- [x] Verify the active Azure revision and public endpoint.

### Validation proof

Validated at `2026-07-31T12:54:30Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3 and Azure CLI authentication available |
| Frontend | 2 targeted tests passed; TypeScript and Vite production build passed |
| Terraform | Initialization, recursive format check, syntax validation, 97-resource state access, and no-change plan passed |
| Saved plan | No infrastructure changes; SHA-256 `ffcc10abb13323f73bc5f44323ff82cc6840ecbbb39d96d4d2f26b1f0c8bf32f` |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Static RBAC | Frontend managed identity retains the Terraform-managed `AcrPull` assignment on the exact ACR scope |

**Saved plan:**
`/tmp/session-agent-indicator.tfplan`

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-07-31T12:56:30Z`.

| Item | Result |
| --- | --- |
| Terraform apply | No infrastructure changes; 0 added, 0 changed, 0 destroyed |
| ACR Task | Run `ch3f` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:session-agents-20260731125529` |
| Active revision | `ca-agmem-frontend--0000027`, healthy, running, one replica, 100% traffic |
| Public endpoint | HTTP 200; deployed bundle contains the agent badge and legacy fallback |
| Backend readiness | HTTP 200 |
| Live role verification | Frontend identity retains `AcrPull` on the exact ACR scope |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

**Deployed by:** `azure-deploy`

## Previous release: semantic health badge colors

Approved: 2026-07-31

This frontend-only release gives each health value an explicit semantic color:
green for `Healthy`, amber for `Degraded`, red for `Unhealthy`, and neutral
gray for `Checking`. It reuses the existing light/dark theme token system and
rolls only the frontend Container App image. Azure infrastructure, backend,
subscription, and regions remain unchanged.

- [x] Update theme tokens and badge state styles.
- [x] Run frontend tests and production build.
- [x] Validate the image-only release.
- [x] Build and deploy a uniquely tagged frontend image.
- [x] Verify the healthy green badge bundle and active Azure revision.

### Validation proof

Validated at `2026-07-31T11:56:30Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3, Azure CLI 2.80.0, and enabled default subscription `7bc68c68-f434-49ad-ab3e-b883ec39da86` |
| Frontend | 35 tests passed; TypeScript and Vite production build passed |
| Terraform | Initialization, recursive format check, syntax validation, 97-resource state access, and no-change plan passed |
| Saved plan | No infrastructure changes; SHA-256 `22ec22d5adc8545ae8e16e9232573de0e274200a11c6259e286c662cfa72b778` |
| Azure Policy | Six effective policy assignments retrieved without blocking the image-only release |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Static RBAC | Frontend managed identity retains `AcrPull` on the exact ACR scope |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/87b04967-9dab-4fb2-bd6d-c20e7982c81f/files/badge-colors-validation.tfplan`

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-07-31T09:59:30Z`.

| Item | Result |
| --- | --- |
| Terraform apply | No infrastructure changes; 0 added, 0 changed, 0 destroyed |
| ACR Task | Run `ch3e` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:health-colors-20260731095739` |
| Image digest | `sha256:6ff8e576c39eb6494432e1f423cd80eeec0c22151cfda9af0bbe9ced441c6a72` |
| Active revision | `ca-agmem-frontend--0000026`, healthy, running, one replica, 100% traffic |
| Public endpoint | HTTP 200; deployed bundle contains success, warning, and danger badge tokens |
| Backend readiness | HTTP 200 |
| Live role verification | Frontend principal retains `AcrPull` on the exact ACR scope |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

**Deployed by:** `azure-deploy`

## Current release: application health badge and semantic billing

Approved: 2026-07-31

This release adds a continuously refreshed health badge to the authenticated
application header. The frontend reads the existing public
`/health/ready` contract without requiring a user token and maps required
dependency failures to `Unhealthy`, optional dependency failures to
`Degraded`, and a fully ready response to `Healthy`. Network failures and
timeouts are reported as `Unhealthy`; polling is bounded and cancelled when
the component disconnects.

Terraform updates the existing Azure AI Search service in place from free
semantic-query quota to billed standard semantic search. The subscription,
tenant, resource group, application region, Search region, identities,
networking, databases, and indexes remain unchanged.

### Current release execution plan

- [x] Diagnose the production failure and confirm Cosmos DB data-plane health.
- [x] Confirm the user-approved subscription and regions.
- [x] Load Azure, Terraform, validation, and deployment guidance.
- [x] Implement and test the frontend health contract and header badge.
- [x] Update and format Terraform semantic billing configuration.
- [x] Set this plan to `Ready for Validation`.
- [x] Invoke `azure-validate` and record fresh validation proof.
- [x] Apply the reviewed Terraform plan.
- [x] Build and deploy a uniquely tagged frontend image.
- [x] Verify Search semantic billing, backend readiness, the active frontend
      revision, the public endpoint, and the deployed health badge.

### Current release validation proof

Validated at `2026-07-31T09:49:15Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3, Azure CLI 2.80.0, and enabled default subscription `7bc68c68-f434-49ad-ab3e-b883ec39da86` |
| Terraform | Initialization, recursive format check, syntax validation, and 97-resource state access passed |
| Saved plan | Exactly one in-place update: `azurerm_search_service.main.semantic_search_sku` from `free` to `standard` |
| Saved plan integrity | SHA-256 `13246d31bb34a719b1b5034aada0bb04b29ab22e531d9e958f0c96799500d600` |
| Azure Policy | Subscription and inherited Defender, Security Baseline, MCAP deploy/deny/audit assignments reviewed; no conflict with the in-place Search update |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Frontend | 35 tests passed; TypeScript and Vite production build passed; local preview returned HTTP 200 and contained the health badge implementation |
| Review and integrity | Five-axis code review reported no required findings; `git diff --check` and deployment script syntax passed |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/87b04967-9dab-4fb2-bd6d-c20e7982c81f/files/health-semantic-validation.tfplan`

**Validated by:** `azure-validate`

### Current release deployment proof

Deployed at `2026-07-31T09:52:39Z`.

| Item | Result |
| --- | --- |
| Terraform apply | `azurerm_search_service.main` updated in place; 0 added, 1 changed, 0 destroyed |
| Azure AI Search | Basic service remains running in West Europe; semantic search is `standard`; semantic hybrid query returned HTTP 200 |
| ACR Task | Run `ch3d` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:health-badge-202607310951` |
| Image digest | `sha256:8a52602a3121f22ccb7bd63d3dc64d8f222e3d5a786d9aef3c99b5382e4b6438` |
| Active revision | `ca-agmem-frontend--0000025`, healthy, running, one replica, 100% traffic |
| Backend readiness | HTTP 200, `ready`, no failed or degraded dependencies |
| Public endpoint | HTTP 200; deployed `assets/index-CqXPirKP.js` contains the health badge and dependency-failure labels |
| Live role verification | Frontend principal retains `AcrPull` on the exact ACR scope |
| Post-deployment drift | Terraform reports no changes |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`

## 1. Project overview

**Goal:** Deploy the completed cutover from image-packaged directive PDFs to a
private Azure Blob Storage `directive-source` container.

**Path:** Modify the existing Azure application.

The ingestion job will read source PDFs from Blob Storage by managed identity.
The backend will expose role-protected, metadata-only source management, and
the frontend will expose the corresponding right-rail UI and streaming upload
path. No data migration, compatibility mode, or session preservation is
required.

## 2. Requirements and Azure context

| Attribute | Confirmed value |
| --- | --- |
| Classification | Proof of concept |
| Scale | Small, under 1,000 users |
| Budget | Performance-focused |
| Compliance | No additional requirements beyond existing Azure policies |
| Subscription | `ME-MngEnvMCAP372348-mimarusa-1` (`7bc68c68-f434-49ad-ab3e-b883ec39da86`) |
| Tenant | `a7b1484c-f66a-496a-b1cf-35631a50396c` |
| Resource group | `rg-agent-memory-rag` |
| Location | East US 2 (`eastus2`) |
| Deployment mode | Modify the existing Terraform-managed environment |
| Authentication | Existing managed identities and Entra ID |

The user confirmed the subscription, location, requirements, and retention of
the existing Terraform-managed Azure Container Apps architecture.

### Policy constraints

The active subscription and inherited management-group assignments include
Microsoft Defender initiatives, Azure Security Baseline, MCAPSGov deploy,
deny, and audit initiatives, and the classic-resource creation deny policy.
The classic-resource deny rule applies only to legacy `Microsoft.Classic*`
types. This release uses current ARM resource types, private Blob access,
managed identities, OAuth-only data access, and existing required tags. The
saved Terraform plan remains the final policy compatibility check.

## 3. Components detected

| Component | Type | Technology | Path |
| --- | --- | --- | --- |
| Frontend | SPA and reverse proxy | TypeScript, Lit, Vite, Nginx | `frontend/` |
| Backend | API service | Python, FastAPI | `backend/` |
| Directive ingestion | Manual worker job | Python CLI, Azure SDKs | `setup/directive_ingest/` |
| Shared contracts | Python package | Pydantic/dataclasses | `directive_contracts/` |
| Infrastructure | Infrastructure as code | Terraform, AzureRM, AzAPI | `infra/` |
| Identity setup | Entra application automation | Azure CLI, Microsoft Graph | `scripts/create_entra_app.sh` |

No GitHub Copilot SDK markers or specialized deployment route were detected.

## 4. Recipe selection

**Selected:** Pure Terraform plus the repository's existing Azure CLI rollout
scripts.

The environment is already managed with local Terraform state, Azure Container
Registry Tasks, Azure Container Apps, and a managed-identity ingestion job.
The user explicitly chose to keep this architecture rather than introduce azd
or a second state/orchestration layer.

## 5. Architecture

**Stack:** Containers on Azure Container Apps.

| Component | Azure service | Deployment action |
| --- | --- | --- |
| Frontend | Azure Container Apps | Build and roll a new image |
| Backend | Azure Container Apps | Add source settings and roll a new image |
| Directive ingestion | Azure Container Apps Job | Add Blob-source settings and roll a new image |
| Images | Azure Container Registry Premium | Build with ACR Tasks |
| Directive sources | Existing private StorageV2 account | Add private `directive-source` container |
| Directive artifacts | Existing private Blob container | Retain unchanged |
| Catalog/content/mandates | Existing Cosmos DB containers | Retain published data |
| Retrieval | Existing Azure AI Search index | Retain indexed data |
| Parsing and summaries | Existing Document Intelligence and Foundry deployments | Retain unchanged |
| Observability | Existing Application Insights and Log Analytics | Retain unchanged |

The existing Blob private endpoint and private DNS cover both containers.
The backend receives `Storage Blob Data Contributor` only on
`directive-source`; ingestion receives `Storage Blob Data Reader` there and
retains contributor access only on `directive-artifacts`. The frontend and
Hosted Agent identities receive no source-container data role.

## 6. Provisioning limit checklist

| Resource type | Number to deploy | Total after deployment | Limit/quota | Evidence |
| --- | ---: | ---: | --- | --- |
| `Microsoft.Storage/storageAccounts` | 0 | 1 | No quota consumed by this release | `az quota usage list` reports one account in `eastus2` |
| `Microsoft.Storage/storageAccounts/blobServices/containers` | 1 | 2 | No separate container-count quota is published; one container can use the storage account capacity | ARM lists one current container; Microsoft Blob scalability targets |
| `Microsoft.Authorization/roleAssignments` | 2 | 149 | 4,000 per subscription | 147 current assignments plus two planned container-scoped assignments |
| `Microsoft.App/containerApps` | 0 new | Existing count unchanged | No new app capacity | Backend and frontend are updated in place |
| `Microsoft.App/jobs` | 0 new | Existing count unchanged | No new job capacity | Ingestion job is updated in place |

`Microsoft.Quota` was registered for the required check. Its storage usage
endpoint reports one existing storage account; the quota limit endpoint was
still propagating registration, but this release creates no storage account.
The source corpus is additionally capped by the application at 512 MiB and each
management upload at 50 MiB.

**Status:** All planned resources are within applicable limits.

## 7. Execution checklist

### Phase 1: Planning

- [x] Analyze workspace in MODIFY mode.
- [x] Gather classification, scale, budget, compliance, and architecture requirements.
- [x] Confirm subscription and location with the user.
- [x] Inspect subscription policy assignments.
- [x] Register `Microsoft.Quota` with user approval and validate capacity.
- [x] Scan the codebase and specialized technology markers.
- [x] Select the existing pure-Terraform recipe.
- [x] Plan the architecture and rollout.
- [x] User approved this plan.

### Phase 2: Preparation

- [x] Load Blob Storage, identity, Terraform, and Container Apps guidance.
- [x] Confirm the completed implementation matches the approved plan.
- [x] Perform local functional verification.
- [x] Set status to `Ready for Validation`.

### Phase 3: Validation

- [x] Invoke `azure-validate`.
- [x] All validation checks pass:
  - [x] 1. Terraform installation.
  - [x] 2. Azure CLI installation.
  - [x] 3. Authentication and confirmed subscription.
  - [x] 4. Terraform initialization.
  - [x] 5. Terraform recursive format check.
  - [x] 6. Terraform syntax validation.
  - [x] 7. Saved Terraform plan preview.
  - [x] 8. Terraform state-backend access.
  - [x] 9. Azure Policy validation.
  - [x] 10. Template-variable resolution check.
  - [x] 11. Backend, ingestion, frontend, and shared-contract tests.
  - [x] 12. Backend/ingestion compilation and frontend production build.
  - [x] 13. Static least-privilege role verification.
- [x] Record validation proof and set status to `Validated`.

### Phase 4: Deployment

- [x] Invoke `azure-deploy`.
- [x] Review the saved Terraform plan.
- [x] Stop for renewed approval if any resource is destroyed or replaced.
- [x] Obtain explicit confirmation for the two ARM RBAC changes.
- [x] Apply only the reviewed saved Terraform plan.
- [x] Update the existing Entra app, using explicit app ID
      `dbf8eef6-d745-4b73-b3ac-edc93a15c339`, to add
      `DirectiveSource.Manage`.
- [x] Obtain explicit confirmation before assigning that app role to the
      signed-in deployment operator.
- [x] Build backend and frontend images with ACR Tasks and roll
      `ca-agmem-backend` and `ca-agmem-frontend`.
- [x] Verify healthy revisions, frontend HTTP 200, readiness, and role
      protection.
- [x] Upload the seven repository fixture PDFs through the authenticated,
      create-only management API.
- [x] Verify upload did not start the ingestion job.
- [x] Build and deploy the ingestion image with
      `scripts/deploy_directive_ingestion.sh`.
- [x] Complete Blob-mode preflight, publication, verification, and restore the
      job to `run-daily`.
- [x] Verify source metadata, retained published data, retrieval, live RBAC,
      and a clean post-deployment Terraform plan.
- [x] Record fully qualified endpoint URLs and set status to `Deployed`.

## 8. Deployment and security boundary

The expected Terraform plan is three additions, two in-place configuration
updates, and no destruction:

- add `azapi_resource.directive_source_container`;
- add backend `Storage Blob Data Contributor` on that container;
- add ingestion `Storage Blob Data Reader` on that container;
- update backend environment settings in place;
- update ingestion job environment settings in place.

No Search index, Cosmos container, published directive, immutable artifact,
session, model deployment, resource group, storage account, Container App, or
Container Apps Job may be deleted or replaced. A plan containing any destroy or
replacement action requires renewed user approval.

The release intentionally makes security changes: two container-scoped ARM
role assignments, one Entra application role, and one operator app-role
assignment. Each security mutation requires explicit confirmation immediately
before execution.

Source bootstrap creates seven blobs with existing filenames and never
overwrites. Source deletion is not part of deployment. Existing published data
is retained, and the unchanged processing hashes should avoid model and Search
writes during reconciliation.

## 9. Acceptance criteria

- [x] Terraform creates the private `directive-source` container without
      destroying or replacing any resource.
- [x] Live backend and ingestion identities have only the planned
      container-scoped source roles.
- [x] The existing Entra app exposes `DirectiveSource.Manage`, and only the
      approved operator receives it.
- [x] The frontend and backend revisions are healthy.
- [x] The approved operator can list and upload metadata-only source PDFs.
- [x] Seven source PDFs exist in Blob Storage and no upload starts ingestion.
- [x] Blob-mode ingestion preflight, publication, and read-only verification
      succeed.
- [x] The job is restored to `run-daily`.
- [x] Existing published directives, content, mandates, Search chunks, and
      immutable artifacts remain valid.
- [x] Backend readiness and an end-to-end directive retrieval request succeed.
- [x] Post-deployment Terraform plan reports no changes.

## 7. Validation proof

Validated at `2026-07-25T20:39:45Z`.

### Functional verification

- **Backend:** 162 tests and 12 subtests passed; Python compilation passed;
  local `/health/live` and authenticated `/me` returned successful responses.
- **Ingestion:** 49 tests passed; Python compilation passed.
- **Frontend:** 27 tests passed; the production build succeeded; local preview
  returned HTTP 200.
- **Container images:** Docker 27.4.0 is installed, but its daemon is not
  running. Image builds and startup health remain deployment checks through ACR
  Tasks and Azure Container Apps.

| Check | Command run | Result | Timestamp |
| --- | --- | --- | --- |
| Toolchain and authentication | `terraform version`; `az version`; `az account show` | Terraform 1.13.3, Azure CLI 2.80.0, enabled confirmed subscription and tenant | 2026-07-25T20:39:45Z |
| Terraform initialization | `terraform -chdir=infra init -input=false -no-color` | Existing local state initialized; AzureRM 4.80.0 and AzAPI 2.10.0 loaded | 2026-07-25T20:39:45Z |
| Terraform formatting | `terraform -chdir=infra fmt -check -recursive` | Passed | 2026-07-25T20:39:45Z |
| Terraform syntax | `terraform -chdir=infra validate -no-color` | Passed | 2026-07-25T20:39:45Z |
| State access | `terraform -chdir=infra state list` | 94 managed resources readable | 2026-07-25T20:39:45Z |
| Saved plan | `terraform -chdir=infra plan -input=false -no-color -out=<session>/files/blob-source.tfplan` | 3 creates, 2 in-place updates, 0 destroys | 2026-07-25T20:39:45Z |
| Plan action review | `terraform show -json` plus `jq` action inspection | Only source container, two source RBAC assignments, backend update, and ingestion-job update | 2026-07-25T20:39:45Z |
| Saved plan integrity | `shasum -a 256 <session>/files/blob-source.tfplan` | `777184437db510001dbc3e8aee377a1d77bd95c7a49351eb0c7759a4ea03013d` | 2026-07-25T20:39:45Z |
| Azure Policy | `policy_assignment_list` and governing deny-rule inspection | Active Defender/governance policies reviewed; planned current ARM types and settings have no identified conflict | 2026-07-25T20:39:45Z |
| Template variables | Repository search for `{{ .Env.* }}` in Terraform inputs | No unresolved Go-style templates | 2026-07-25T20:39:45Z |
| Backend | `backend/.venv/bin/python -m pytest backend/tests -q`; `compileall` | 162 tests and 12 subtests passed; compilation passed | 2026-07-25T20:39:45Z |
| Ingestion | `setup/directive_ingest/.venv/bin/python -m pytest setup/directive_ingest/tests -q`; `compileall` | 49 tests passed; compilation passed | 2026-07-25T20:39:45Z |
| Frontend | `npm test -- --reporter=dot`; `npm run build` | 27 tests passed; TypeScript and Vite production build passed | 2026-07-25T20:39:45Z |
| Script and patch integrity | `bash -n` on rollout scripts; `git diff --check` | Passed | 2026-07-25T20:39:45Z |

**Saved plan:** `/Users/mimarusa/.copilot/session-state/8321e24b-94a6-4dad-8321-415b9b2a2bcc/files/blob-source.tfplan`

**Validated by:** `azure-validate`

### Static role verification

- **Backend identity:** Existing ACR pull, Search reader, directive Cosmos reader,
  artifact Blob reader, and model roles remain. The plan adds only
  `Storage Blob Data Contributor` on the exact `directive-source` container,
  matching metadata list, create-only upload, and exact delete operations.
- **Ingestion identity:** Existing ACR pull, artifact Blob contributor, Search,
  Cosmos, Document Intelligence, and model roles remain. The plan adds only
  `Storage Blob Data Reader` on the exact `directive-source` container,
  matching list and download operations.
- **Frontend and Hosted Agent identities:** No source-container data role.
- **Credentials:** Azure-hosted backend and ingestion paths use their explicit
  user-assigned `ManagedIdentityCredential`; no storage keys or SAS tokens are
  introduced.
- **Status:** Verified; no role issue found.

## 11. Deployment artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `.azure/deployment-plan.md` | Current deployment source of truth | Deployed |
| `infra/*.tf` | Container, RBAC, and runtime configuration | Applied |
| `backend/Dockerfile` | Backend image | Deployed |
| `frontend/Dockerfile` | Frontend image | Deployed |
| `setup/directive_ingest/Dockerfile` | Blob-mode ingestion image | Deployed |
| `scripts/create_entra_app.sh` | Safe existing-app role update | Executed |
| `scripts/deploy_directive_ingestion.sh` | Ingestion rollout and verification | Executed |

## 12. Deployment verification

Deployed to `ME-MngEnvMCAP372348-mimarusa-1`
(`7bc68c68-f434-49ad-ab3e-b883ec39da86`) in `eastus2` on
2026-07-25 UTC.

### Infrastructure and authorization

- Terraform applied `3 added, 2 changed, 0 destroyed`; the final plan reported
  `No changes. Your infrastructure matches the configuration.`
- Created private container `directive-source` in `agmem5df652docs`.
- Backend principal `8987ff56-8834-45a2-8e18-2f1a776bba37` has
  `Storage Blob Data Contributor` and ingestion principal
  `f98f333c-4a20-4092-938a-3e405c702c4b` has
  `Storage Blob Data Reader`, both scoped exactly to `directive-source`.
- The frontend principal has no source-container assignment.
- Enabled user app role `DirectiveSource.Manage`
  (`993505a1-1ba5-4103-a540-69885b82e1af`) and verified the single approved
  operator assignment. A refreshed token carried the role and `/api/me`
  returned `can_manage_directive_sources: true`.

### Images and revisions

| Workload | Tag | Digest |
| --- | --- | --- |
| Backend | `202607252050-blobsource` | `sha256:bfac89d613033ecae41978696ad11bbe9d32a96926837ac3aaefeaa42eef8ec9` |
| Frontend | `202607252050-blobsource` | `sha256:3b19c05b7e27203482529068a502d37d9850c83e556f4212edcad11d3995c9f5` |
| Ingestion | `202607252055-blobsource` | `sha256:99c1d0009f2890bfd42c8adcfbd447d87849111ac21843ff415400a39c0e490a` |

Backend revision `ca-agmem-backend--0000059` and frontend revision
`ca-agmem-frontend--0000023` are healthy, provisioned, and receive 100% of
traffic. The ingestion job is provisioned successfully on the new image with
command `directive-ingest` and argument `run-daily`.

### Source and ingestion checks

- Uploaded 7 PDFs totaling 803,856 bytes through the authenticated API.
- Source listing returns all 7 items. A duplicate upload returns `409` without
  changing the count, and uploads did not start ingestion.
- Managed-identity preflight passed for source Blob read, artifact Blob write,
  Cosmos, Search, Document Intelligence, embeddings, and summary-model access.
- Blob reconciliation reported `source_count=7`, `skipped_count=7`,
  `changed_count=0`, and `chunk_count=0`.
- Verification passed for 7 published/source versions, 91 content sections,
  91 published chunks, 4 current directives, 14 required artifacts,
  5 mandate assignments, the 3072-dimensional vector configuration, and a
  direct hybrid query.

### Live acceptance

- Frontend:
  `https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`
- Readiness returned `ready` with every dependency `ok`, including
  `directive_sources`.
- An authenticated Directive Assistant request returned HTTP 200, completed
  with `RUN_FINISHED`, exercised `search_directives`, and emitted grounded
  directive references.
- Resource group:
  `https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

## 13. Completion

Current phase: deployed.

## 14. Frontend citation section navigation release

Release date: 2026-07-26.

### Scope

This is an immutable frontend-only release. Inline directive citations and
detailed Sources rows open the existing exact-version document drawer at the
validated Markdown section, display section/page context, and preserve the cited
page when the PDF tab is opened. Grouped Documents actions continue to open the
document at the top. Backend APIs, agent contracts, ingestion, data, RBAC, and
Terraform infrastructure are unchanged.

### Validation checklist

- [x] Invoke `azure-validate`.
- [x] All validation checks pass:
  - [x] 1. Terraform installation.
  - [x] 2. Azure CLI installation.
  - [x] 3. Authentication and confirmed subscription.
  - [x] 4. Terraform initialization.
  - [x] 5. Terraform recursive format check.
  - [x] 6. Terraform syntax validation.
  - [x] 7. Terraform plan preview.
  - [x] 8. Terraform state-backend access.
  - [x] 9. Azure Policy validation.
  - [x] 10. Template-variable resolution check.
  - [x] 11. Frontend unit tests and production build.
  - [x] 12. Browser citation-navigation regression flow.
  - [x] 13. Static least-privilege role verification.
  - [x] 14. Diff and deployment-script integrity.
- [x] Record current validation proof and set this release to `Validated`.

### Validation proof

Validated at `2026-07-26T07:09:55Z`.

| Check | Command or review | Result |
| --- | --- | --- |
| Toolchain | `terraform version`; `az version` | Terraform 1.13.3 and Azure CLI 2.80.0 |
| Authentication | `az account show --subscription 7bc68c68-f434-49ad-ab3e-b883ec39da86` | Enabled subscription `ME-MngEnvMCAP372348-mimarusa-1`, tenant `a7b1484c-f66a-496a-b1cf-35631a50396c` |
| Terraform | `init`; `fmt -check -recursive`; `validate`; `state list`; saved `plan -detailed-exitcode` | Passed; 97 resources readable; exit 0; no changes and 0 changed resources |
| Saved plan integrity | `shasum -a 256` | `2f88dfd983e74c73829615be2211dccb72e5e0c12088a0a29bf285cea9969f62` |
| Azure Policy | `policy_assignment_list` at subscription scope | Current Defender, Azure Security Baseline, and MCAP governance assignments reviewed; this frontend image-only release adds or changes no Azure resource |
| Template variables | Search for unresolved `{{ .Env.* }}` in Terraform inputs | No matches |
| Frontend | `npm test`; `npm run build` | 32 tests passed; TypeScript and Vite production build passed |
| Browser flow | Playwright Chromium regression script | Inline and Sources navigation, exact heading focus, same-document reuse/top reset, cited PDF page, external-source preservation, relative PDF intent, and focus restoration passed |
| Independent review | Five-axis review and re-review | Required scroll-reset finding fixed; no remaining high-confidence issues |
| Static roles | IaC/diff review | No infrastructure diff or new data operation; the frontend retains only its existing ACR-scoped `AcrPull` assignment |
| Integrity | `git diff --check`; `bash -n scripts/deploy_images.sh` | Passed |
| Local image runtime | `docker info` | Docker daemon unavailable; ACR Task build and Container Apps health are deployment gates |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/cb9fd353-0eaf-48c5-b9a8-aa60bef10375/files/citation-navigation-validation.tfplan`

**Validated by:** `azure-validate`

### Deployment checklist

- [x] Invoke `azure-deploy`.
- [x] Build a uniquely tagged frontend image with an ACR Task.
- [x] Roll only `ca-agmem-frontend` to the immutable image.
- [x] Verify the new revision is healthy and receives 100% of traffic.
- [x] Verify the production root, configuration, deployed bundle, and directive
      citation interaction.
- [x] Record the image digest, revision, rollback target, endpoint, and portal URL.

### Deployment proof

Deployed at `2026-07-26T07:20:02Z` to
`ME-MngEnvMCAP372348-mimarusa-1`
(`7bc68c68-f434-49ad-ab3e-b883ec39da86`) in `eastus2`.

| Item | Result |
| --- | --- |
| ACR Task | Run `ch3c` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:citation-sections-20260726071405` |
| Image digest | `sha256:4e2af92d8f67dd3b9648824b26e59950cdb333d41344732e77f1985966ebc525` |
| Active revision | `ca-agmem-frontend--0000024`, healthy, provisioned, one replica, 100% traffic |
| Rollback revision | `ca-agmem-frontend--0000023`, image `frontend:202607252050-blobsource` |
| Runtime dependency audit | `npm audit --omit=dev`: 0 vulnerabilities |
| Public endpoint | HTTP 200; deployed `assets/index-Bd2enmIj.js` contains the citation-location implementation |
| Backend readiness | `ready`; all required dependencies `ok` |
| Authorization boundary | Anonymous exact-version directive document request returns HTTP 401 |
| Deployed browser flow | Passed against the production bundle with isolated in-browser test data and API stubs |
| Live role verification | Frontend principal `2d5ffcf5-89b7-4689-816c-ccc5c62c98bf` has `AcrPull` on the exact ACR scope |
| Post-deployment drift | Terraform exit 0; no changes and 0 changed resources |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`
