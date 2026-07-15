# Ideas

Use this file to collect workshop and project ideas. Active ideas stay here; implemented ideas are moved to the Archive section. New ideas are added first.

## Urgently fix Foundry prompt-agent order lookup

Fix the Foundry prompt agent so it can retrieve order details. For the request `Check order ORD-001 and tell me its shipping status.`, it currently returns: "I'm unable to access specific order details like shipping status. I can only provide general information about shipping policies or procedures. If you need help with that, please let me know!"

<sub>**Date:** 2026-07-11 · **Author:** Unknown · **Implemented:** No</sub>

# Archive

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
