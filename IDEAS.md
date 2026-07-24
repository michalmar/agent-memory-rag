# Ideas

Use this file to collect workshop and project ideas. Active ideas stay here; implemented ideas are moved to the Archive section. New ideas are added first.

## Consolidate the MAF Hosted agents

Remove accidental divergence between the two Foundry Hosted MAF agents (`customer-support-maf` and the new `directive-rag-maf` Directive Assistant) while keeping their intentional scope differences. Extract the duplicated identity/observability/middleware bootstrap and the gateway tool-invoke transport into one shared `maf_hosting` package, symmetrize the two agents' folder layout, reconcile the `azure.yaml`/azd build path with the script-based build, add the directive agent to `deploy_images.sh`, and harden the `DIRECTIVE_MAX_ITERATIONS` parse and tool-timeout layering. Consolidation only — no change to prompts, tool schemas, the public citation model, or the backend runtime.

**Implementation plan:** [`TEMP-plan-maf-hosted-agent-consolidation.md`](docs/TEMP-plan-maf-hosted-agent-consolidation.md)

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

## Use Azure Blob Storage as the directive source

Store uploaded directive PDFs in a dedicated `directive-source` container in the existing directive storage account. Add a managed-identity Blob source adapter while keeping generated immutable PDFs, canonical Markdown, sections, manifests, and summaries in the existing `directive-artifacts` container.

**Implementation plan:** [`TEMP-plan-directives-from-blob.md`](docs/TEMP-plan-directives-from-blob.md)

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

## Explain directive agents and document ingestion in plain English

Document in detail, using plain English, how directive agents work with documents: how documents are added, ingested, chunked or indexed, retrieved, and used to produce answers.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

## Simplify the directive RAG pattern

Review the directive RAG pattern for unnecessary complexity and simplify its design and implementation without changing required behavior.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

## Compare legacy and directive agents

Compare the old agents with the new directive agent and identify inconsistencies in behavior, configuration, tools, prompts, data access, and implementation. Resolve the inconsistencies where appropriate.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

## Add slash commands to directive agents

Add leading slash commands to directive agents, such as `/search`, `/compare`, and `/id`, with clear routing, validation, and help behavior.

<sub>**Date:** 2026-07-23 · **Author:** Unknown · **Implemented:** No</sub>

# Archive

## Investigate the directive agent's local Docker dependency

Confirmed local Docker is not required anywhere. All container images build server-side via ACR Tasks (`az acr build` in `scripts/deploy_images.sh`, `build_hosted_agent_image.sh`, `deploy_directive_ingestion.sh`) and azd remote build (`docker.remoteBuild: true` in both agent `azure.yaml` files). Local dev runs native processes (`uv`/`uvicorn`, `npm run dev`), and there is no CI that builds images. A repo-wide sweep for local `docker build/run/compose/push` found none.

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
