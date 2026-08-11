# Ideas

Use this file to collect workshop and project ideas. Active ideas stay here; implemented ideas are moved to the Archive section. New ideas are added first.

## Implement GitHub Actions CI/CD

Implement the reviewed GitHub Actions CI/CD design, including unprivileged pull-request checks, remote Terraform state, protected OIDC deployments, independent release workflows, drift detection, and tested rollback paths.

**Implementation plan:** [GitHub Actions CI/CD design proposal](.azure/deployment-plan.md#github-actions-cicd-design-proposal---2026-07-24)

<sub>**Date:** 2026-07-24 · **Author:** @michalmar · **Implemented:** No</sub>

## Simplify the directive RAG pattern

Review the directive RAG pattern for unnecessary complexity and simplify its design and implementation without changing required behavior.

**Implementation plan:** [`TEMP-plan-simplify-directive-rag.md`](docs/TEMP-plan-simplify-directive-rag.md)

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>


## Add slash commands to directive agents

Add leading slash commands to directive agents, such as `/search`, `/compare`, and `/id`, with clear routing, validation, and help behavior.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

# Archive

## Use Azure Blob Storage as the directive source

Directive PDFs now live in a dedicated private `directive-source` container.
The managed-identity ingestion job reads the current Blob corpus without an
image rebuild, while generated immutable outputs remain isolated in
`directive-artifacts`. Approved operators use the role-protected, metadata-only
Sources rail for create-only PDF upload and confirmed deletion; these actions
do not control ingestion or remove previously published content.

**Implementation plan:** [`TEMP-plan-directives-from-blob.md`](docs/TEMP-plan-directives-from-blob.md)

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-25</sub>

## Remove duplicate directive retrieval planning

GPT-5.6 is now the sole directive retrieval planner. The backend executes each
final intent as a concurrent direct Azure AI Search hybrid query, applies
backend-owned publication and version filters, and fuses results with stable
cross-intent reciprocal-rank fusion while preserving the existing tool and
citation envelopes. Runtime knowledge-base settings, directive knowledge-source
and knowledge-base provisioning, and the dedicated GPT-5 Nano planner deployment
were removed in the same direct cutover.

The implementation uses 50 semantic candidates per intent, RRF constant 60, and
deterministic score, best-rank, matched-intent-count, and document-ID tie
breakers. The existing Search vectorizer and semantic ranker remain.

Deployment was verified in `rg-agent-memory-rag`: the dedicated GPT-5 Nano
planner was removed, ingestion verified the direct hybrid query, backend
readiness passed, and an authenticated directive turn returned ten grounded
references from two final intents.

<sub>**Date:** 2026-07-25 · **Author:** @michalmar · **Implemented:** Yes · **Implemented date:** 2026-07-25 · **Deployed:** Yes · **Deployment verified:** 2026-07-25</sub>

## Consolidate directive content authority in Cosmos

Cosmos DB is now the runtime authority for exact-version manifests, precomputed
summaries, and section content. Each published version is one validated catalog
bundle, sections are immutable generation-scoped content items, and Azure AI
Search remains a derived retrieval projection. Private Blob Storage retains only
complete canonical Markdown and source PDFs.

The change is a destructive schema cutover with no migration, compatibility
reader, or Blob JSON fallback. Existing directive versions must be republished.

**Implementation plan:** [`TEMP-plan-directive-cosmos-content.md`](docs/TEMP-plan-directive-cosmos-content.md)

<sub>**Date:** 2026-07-25 · **Author:** @michalmar · **Implemented:** Yes · **Implemented date:** 2026-07-25</sub>

## Migrate the directive agent to stateful continuation

Replaced stateless inner model replay with a backend-owned inner Foundry
conversation, a directive-only `AgentSession`, and `store=true`. Later turns send
only new input; authenticated state resolution, conditional turn leases,
legacy bootstrap, dependency guards, and ordered inner-state deletion preserve
ownership and lifecycle boundaries. The support agent remains unchanged.

**Implementation plan:** [`TEMP-plan-stateful-continuation.md`](docs/TEMP-plan-stateful-continuation.md)

<sub>**Date:** 2026-07-24 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-24</sub>

## Investigate encrypted reasoning in the directive agent

Confirmed encrypted reasoning is required for the directive agent's stateless
(`store=false`) GPT-5.6 multi-tool flow. The Responses API requires encrypted
reasoning items to preserve reasoning context across those turns, and the
directive hosting package must round-trip that opaque payload. It is not
required by the `gpt-4o-mini` support agent, which rejects the OpenAI adapter
1.11.0 behavior that adds `reasoning.encrypted_content` to every stateless
request.

The support image therefore keeps its known-good Core 1.11.0, Foundry 1.10.1,
Foundry Hosting `1.0.0a260709`, OpenAI adapter 1.10.1, and OpenAI SDK 2.46.0
stack behind dependency and request-shape guard tests. The directive agent
retains Foundry Hosting `1.0.0b260722` and its encrypted-reasoning round-trip
guard. Upgrade support only after the adapter makes encrypted reasoning
capability-aware or explicitly configurable and both model-specific guards
pass.

See [Encrypted reasoning items in the Azure OpenAI Responses API](https://learn.microsoft.com/azure/foundry/openai/how-to/responses#encrypted-reasoning-items).

<sub>**Date:** 2026-07-24 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-24</sub>

## Consolidate the MAF Hosted agents

Removed accidental divergence between the two Foundry Hosted MAF agents while preserving their separate prompts, tools, citation models, and runtime contracts. Both agents now use the shared `maf_hosting` package, have symmetric co-located layouts, build only through the repo-root ACR script, support opt-in directive orchestration, and use hardened directive iteration and timeout configuration.

**Implementation plan:** [`TEMP-plan-maf-hosted-agent-consolidation.md`](docs/TEMP-plan-maf-hosted-agent-consolidation.md)

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-24</sub>

## Investigate the directive agent's local Docker dependency

Confirmed local Docker is not required anywhere. All container images build server-side via ACR Tasks (`az acr build` in `scripts/deploy_images.sh`, `build_hosted_agent_image.sh`, and `deploy_directive_ingestion.sh`); Hosted Agent `azure.yaml` files deploy those prebuilt images without a separate azd build. Local dev runs native processes (`uv`/`uvicorn`, `npm run dev`), and there is no CI that builds images.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-23</sub>

## Align ACR privacy with Foundry

The ACR does not need to be private if Foundry is not private.

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** Yes</sub>

## Implement selectable dual-agent architecture

The selectable dual-agent architecture remains design-only and is not implemented.

<sub>**Date:** 2026-07-10 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-11</sub>

## Enable Entra ID easy authentication

The app is still `AUTH_MODE=mock`. Switch to Entra by setting the printed environment variables on both Container Apps (`AUTH_MODE=entra` plus `ENTRA_*`) and redeploy.

<sub>**Date:** 2026-07-10 · **Author:** Unknown · **Implemented:** Yes</sub>

## Isolate conversation history by user

Fix conversation-history display so users cannot see each other's conversations. Revise the project's user-access strategy.

<sub>**Date:** 2026-07-10 · **Author:** Unknown · **Implemented:** Yes</sub>

## Remove the jump VM

Delete the jump VM because it is no longer required.

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-12</sub>

## Centralize agent observability in Application Insights

Configure the current Foundry resource to send tracing and logging to the project's Application Insights instance. Verify the hosted MAF agent does the same. All agent telemetry and logs must land in the project's Application Insights resource.

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-12</sub>

## Remove obsolete Azure infrastructure

Remove legacy Azure infrastructure that is not part of the final architecture, including the old Foundry resource if it is no longer required, private endpoints, and other obsolete components.

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-12</sub>

## Consider agent deployment without container images

Because the ACR is public, prefer releasing agents without Docker/container images where feasible. Do not use Azure Container Apps jobs unless they are required.

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-12</sub>

## Redesign the UI

Redesign the UI with inspiration from Linear and Stripe.com.

<sub>**Date:** 2026-07-10 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-12</sub>

## Show a login screen before the frontend

When an unauthenticated user accesses the frontend URL, show a login screen before allowing access to the application. Entra ID is the only authentication provider.

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-12</sub>

## Improve citation readability with documents and collapsible sources

Add a `Documents` section above `Sources` that lists the parent documents used for an answer. Keep the current chunk-level sources for detailed evidence, but show them in a collapsible panel that is collapsed by default to avoid overwhelming users when many chunks are returned.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-24</sub>

## Fix source list row layout

Fix the source-display layout so every source is rendered as one complete item in one row. Keep the document title, version, page or section details, and status badge together; do not split, wrap, or misalign source items across columns or rows.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** Yes · **Implemented date:** 2026-07-24</sub>

## Navigate directive citations to exact sections

Make each directive inline citation and detailed Sources row open the exact
published document in the existing sidebar, scroll and focus the cited Markdown
section heading, show its page range, and keep the PDF tab anchored to the cited
start page. Section/page precision is sufficient; sentence, paragraph, table-row,
and PDF-region highlighting are not required.

**Implementation plan:** [Source citation section/page navigation plan](workspace:plan.md)

<sub>**Date:** 2026-07-25 · **Author:** @michalmar · **Implemented:** Yes · **Implemented date:** 2026-07-26</sub>
