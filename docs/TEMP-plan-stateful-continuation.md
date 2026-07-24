# Stateful Continuation Migration Plan

Status: Implemented in code; deployment canary and operational acceptance remain.

## Decision

Encrypted reasoning is not a directive-agent requirement. It is required only
when reasoning/tool state is replayed with `store=false`.

The target design is:

1. Keep the existing outer Foundry Hosted Agent conversation as the
   application-visible transcript and routing authority.
2. Have the backend create and persist a separate inner Foundry model
   conversation for each Hosted Agent conversation.
3. Run the Microsoft Agent Framework agent with `store=true` and an
   `AgentSession` whose `service_session_id` is that inner conversation ID.
4. Send only new input on later turns; do not replay the outer transcript into
   the inner model after bootstrap.
5. Resolve the inner ID inside the host through an authenticated, session-bound
   backend endpoint; never take it from user/model input.
6. Delete the inner conversation directly from the backend when the application
   conversation is deleted.

Do not ship `store=true` by itself. It makes tool iterations stateful within one
`Agent.run()` but starts a new stored response chain on every Hosted Agent turn,
does not provide cross-turn inner continuation, and creates stored response data
that the backend cannot currently delete.

## Goals

- Eliminate stateless inner model requests from the directive agent.
- Preserve conversation context across separate user turns.
- Continue tool loops by service-managed state rather than encrypted reasoning
  replay.
- Keep the backend-owned application transcript and outer Foundry conversation
  intact.
- Preserve tenant isolation, retries, deletion, and immutable agent selection.
- Keep the support agent on its known-good dependency stack until the directive
  canary proves the full path.

## Non-goals

- Do not merge the outer Hosted Agent conversation and inner model conversation.
- Do not make raw `conv_*` or `resp_*` IDs client-visible.
- Do not replace the backend transcript with model-provider state.
- Do not change support-agent dependencies in the first rollout.
- Do not retain a permanent stateless fallback after the stateful release is
  accepted. Roll back by deploying the previous immutable image.

## Current state

There are two state layers:

| Layer | Current behavior |
| --- | --- |
| Outer Hosted Agent | Stateful. The backend creates and persists a Foundry conversation and Hosted session, then reuses both on every turn. |
| Inner model client | Stateless. `ResponsesHostServer` loads the full outer history and calls `agent.run()` without an `AgentSession`; both agents set `store=false`. |

Current request path:

```text
application conversation
  -> backend RuntimeState
     -> outer Foundry conversation + Hosted session
        -> ResponsesHostServer.get_history()
           -> Agent.run(full history, no AgentSession)
              -> inner model request store=false
                 -> encrypted reasoning replay across tool calls
```

The pinned `agent-framework-foundry-hosting==1.0.0b260722` regular-agent
implementation confirms this behavior: it calls `context.get_history()`, adds
the current input, and invokes `agent.run()` without `session=`.

## Verified framework behavior

Local probes against the exact installed packages established:

- Core 1.12.1 with `store=true` automatically carries the first response ID into
  the next tool call as `previous_response_id`.
- Streaming and non-streaming tool loops both send only the function result on
  the second model call.
- `AgentSession(service_session_id="conv_inner")` causes every model call,
  including tool continuations, to use the same `conversation_id`.
- OpenAI adapter 1.10.1 and 1.10.2 do not add
  `reasoning.encrypted_content` to a first `store=true` request.
- OpenAI adapter 1.11.0 does add encrypted reasoning when no continuation ID is
  present, even when `store=true`.
- OpenAI adapter 1.11.0 does not add encrypted reasoning when a
  `conversation_id` is present.
- Adapter 1.10.2 and 1.11.0 both map a `conv_*` service session ID to the
  Responses API `conversation` field.

Therefore the final dedicated-conversation design can use adapter 1.11.0 safely
only if a guard proves that every directive model call has an inner
`conversation_id`. A `store=true` request without that ID remains a regression.

## Target architecture

```text
application conversation
  -> backend RuntimeState
     -> outer Foundry conversation + Hosted session + inner model conversation
        -> session-aware Responses host
           -> authenticated state resolver
              -> trusted per-session cache/turn ledger under $HOME
              -> AgentSession(service_session_id=inner conv_*)
                 -> Agent.run(new input only, store=true)
                    -> inner Foundry conversation
                       -> tool calls continue in the same conversation
```

State ownership:

| State | Owner | Purpose | Deletion |
| --- | --- | --- | --- |
| Application transcript | Backend/Cosmos | UI, audit, citations, routing | Existing application delete |
| Outer Foundry conversation | Backend | Hosted protocol history | Existing `conversations.delete` |
| Hosted session and `$HOME` | Foundry Hosted Agents | Compute affinity and durable session file | Existing Hosted session delete |
| Inner Foundry conversation ID | Backend `RuntimeState`/Cosmos | Durable model/tool continuation mapping | Direct backend `conversations.delete` |
| `AgentSession` record | Session-aware host in `$HOME` | Cache, revision, and turn-recovery state | Removed with Hosted session; never the only copy of the inner ID |

The outer and inner conversations must remain separate. Reusing the outer
conversation for nested model calls is not approved because the outer response
is still in progress while the inner client would write to the same history,
which risks duplicate, recursive, or partially ordered state.

The inner conversation ID must never exist only in Hosted `$HOME`. Hosted
sessions are deleted after inactivity, while the application conversation can
still exist. Backend persistence is required so deletion remains possible even
when Hosted compute or its filesystem is unavailable.

## Dependency policy

### Support agent

Keep unchanged for the first rollout:

```text
agent-framework-core==1.11.0
agent-framework-foundry==1.10.1
agent-framework-foundry-hosting==1.0.0a260709
agent-framework-openai==1.10.1
openai==2.46.0
```

### Directive agent

Pin the complete canary stack instead of relying on transitives:

```text
agent-framework-core==1.12.1
agent-framework-foundry==1.10.1
agent-framework-foundry-hosting==1.0.0b260722
agent-framework-openai==1.11.0
openai==2.48.0
```

The 1.11.0 adapter is acceptable only for the dedicated-conversation target.
If a preliminary `store=true` experiment is run without a pre-created inner
conversation, use 1.10.2 or 1.10.1 and keep it out of production.

## Migration phases

### Phase 0: Correct the contract and freeze the baseline

Files:

- `README.md`
- `IDEAS.md`
- `.azure/deployment-plan.md`
- `docs/PRD-Solution-Challenges-1-5.md`
- Directive and support dependency tests

Changes:

- Document the distinction between outer state, per-run tool continuation, and
  cross-turn inner continuation.
- Correct the deployment note: changing only `store` is sufficient for tool
  continuation within one run, but insufficient for cross-turn inner
  continuation under the current host.
- Keep the support stack and its current request-shape guard unchanged.
- Pin the directive agent's resolved Core, OpenAI adapter, and OpenAI SDK
  versions.

Exit criteria:

- Dependency builds are reproducible.
- Existing stateless behavior remains unchanged until the new host is ready.
- Documentation no longer describes encrypted reasoning as an intrinsic
  directive-agent need.

### Phase 1: Prove identity, continuation, and lifecycle

Run isolated probes against the deployed GPT-5.6 model and current Foundry
project before changing production behavior.

Required live probes:

1. Create and delete a dedicated inner conversation using the backend identity.
2. Resolve that ID from a Hosted Agent call through the existing
   Agent-Identity-authenticated, user/session-bound backend gateway pattern.
3. Confirm the resolver receives trusted platform `user_id`, Hosted session ID,
   and call ID without exposing the inner ID to model input or the outer client.
4. Invoke GPT-5.6 with `store=true` and the resolved inner conversation.
5. Execute at least two tool iterations and confirm all calls use the same
   `conversation`.
6. Confirm no request includes `reasoning.encrypted_content`.
7. Delete the inner conversation from the backend and confirm retrieval returns
   not found even if the Hosted session is unavailable.
8. Verify `$HOME` restoration after more than 15 minutes of inactivity.
9. Verify whether a Hosted session can ever have more than one active compute
   instance. If it can, require a backend/service-side lease before rollout.
10. Identify the exact private `ResponsesHostServer` method that must be
    overridden and prove the pinned hosting build still has the expected
    signature and behavior.

Go/no-go:

- Do not proceed until server-to-server resolution, stateful continuation,
  deletion independent of Hosted-session liveness, and the compute concurrency
  invariant are proven.

### Phase 2: Make the backend own the inner conversation

Primary files:

- `agent_contracts/models.py`
- `backend/agent_memory_backend/conversation_history.py`
- `backend/agent_memory_backend/conversation_coordinator.py`
- `backend/agent_memory_backend/foundry_hosted_maf_runtime.py`
- `backend/agent_memory_backend/agent_tool_gateway.py` or a separate internal
  state route using the same authorization policy
- Corresponding backend tests

State changes:

- Add `inner_model_conversation_id` to `RuntimeState`.
- Add an explicit inner-state/bootstrap status and advance the runtime-state
  schema version.
- Treat the inner ID as trusted server state. Do not return it from public
  conversation APIs or AG-UI events.

Creation behavior:

1. Create the Hosted session.
2. Create the outer Foundry conversation.
3. Create an empty dedicated inner Foundry conversation.
4. Persist all three IDs atomically in the application conversation document.
5. On any partial failure, clean up all resources already created in reverse
   order and surface the original failure.

State resolver:

- Add a non-model-visible internal operation for the Hosted Agent to resolve
  state from trusted `user_id`, Hosted `session_id`, and `call_id`.
- Reuse the existing app-role, issuer, audience, tenant, and principal allowlist
  enforced by the Agent tool gateway.
- Look up the application conversation through
  `get_by_hosted_session(user_id, session_id)`.
- Verify the stored Hosted session, agent type, physical agent/release, and user
  binding before returning the inner ID.
- Return only the minimum state needed by the host, and never log the ID.

Existing conversations:

- Lazily allocate an inner conversation when restoring a pre-migration
  directive `RuntimeState`.
- Persist it with a Cosmos ETag/conditional update before invoking the host.
- Mark that conversation as requiring one bootstrap from the authoritative
  outer history.
- Ensure two concurrent migrations cannot allocate two inner conversations; if
  one loses the ETag race, delete its unreferenced inner conversation.

Exit criteria:

- The backend can delete the inner conversation without contacting the Hosted
  session.
- Cross-user/session resolver calls fail with 403/not found.
- Runtime-state serialization and restoration support both old and new schema
  documents without losing deletion ownership.

### Phase 3: Add a session-aware regular-agent host

Primary files:

- `maf_hosting/runtime.py`
- New `maf_hosting/session_state.py`
- New/updated `maf_hosting/tests/`
- `agents/directive-rag-maf/src/directive-rag-maf/main.py`

Implementation boundary:

- The pinned regular-agent host currently loads `context.get_history()` and
  calls a private `_handle_inner_agent` path without `session=`.
- Implement a narrowly scoped subclass/adapter for the pinned hosting build.
- Add a compatibility guard test that fails if the private method signature,
  history-loading behavior, or `Agent.run` call shape changes on dependency
  upgrade.
- Prefer an upstream/public session hook when one becomes available; do not
  copy the entire hosting package into this repository.

Host behavior:

1. Require `context.platform_context.user_id_key`,
   `context.platform_context.call_id`, `context.conversation_id`, and the
   platform Hosted session ID.
2. Resolve the backend-owned inner conversation through the authenticated state
   resolver. Never take a raw service ID from request metadata, user input, or
   model output.
3. Validate any local record against the returned owner, outer conversation,
   agent release, and inner conversation.
4. Construct or restore
   `AgentSession(service_session_id=<inner conversation ID>)`.
5. If backend state says bootstrap is required, load outer history exactly once
   and send it with current input into the empty inner conversation.
6. On later turns, do not call `context.get_history()` for model input; pass only
   current input items and the restored `AgentSession`.
7. Run with `default_options={"store": True}`.
8. Persist local revision/recovery state atomically only after successful
   completion.
9. Report successful bootstrap through a conditional backend state update so a
   lost local file cannot cause history to be inserted twice.

Session file requirements:

- Store under `$HOME/.agent-memory-rag/inner-session.json` with mode `0600`.
- Write a temporary file, flush/fsync, then atomically replace.
- Treat it as a cache and turn-recovery ledger, not the authoritative copy of
  the inner conversation ID.
- Include schema version, hashed platform user partition, outer conversation,
  inner-state digest, agent logical/release identity, revision, and pending/last
  completed turn metadata.
- Reject mismatches instead of silently starting a different chain.
- Never log raw IDs, prompts, tool payloads, or serialized state.

Feature rollout:

- Gate the new host behind a directive-only deployment setting for the canary.
- Keep the current immutable directive image as rollback.
- Never fall back automatically to stateless replay when resolution or
  restoration fails.

Exit criteria:

- Streaming and non-streaming tests have equivalent state transitions.
- Every inner call has `store=true` and the expected conversation.
- Later turns provide only new input.
- No encrypted reasoning include is present.
- Host compatibility, owner binding, and missing-state failures are explicit.

### Phase 4: Add durable idempotency and concurrency

Primary files:

- Backend chat request/turn models
- `conversation_coordinator.py`
- `foundry_hosted_maf_runtime.py`
- Internal state resolver/lease logic
- Session-aware host state

Changes:

- Assign a durable application turn ID before the Hosted Agent call.
- Pass it through trusted server-to-server state resolution, not model input.
- Store `pending`, `completed`, payload hash, and state revision in durable
  backend state; mirror only recovery data in `$HOME`.
- Keep the existing in-process `CONVERSATION_BUSY` lease as a fast local guard.
- Add a Cosmos ETag/lease or equivalent service-side conditional write as the
  cross-process serialization authority.
- Do not rely on `asyncio.Lock` alone; it cannot serialize separate compute
  instances or backend replicas.
- Require direct Foundry/published-channel calls to acquire the same durable
  lease through the state endpoint before advancing the inner conversation.
- For an exact completed duplicate, replay a stored outer result or return a
  deterministic duplicate outcome without appending inner state.
- Reconcile a pending turn after interruption; do not append the same user input
  again by default.
- Reject a reused turn ID with a different payload hash.

Exit criteria:

- Two concurrent calls cannot branch one inner conversation, including across
  backend or Hosted compute instances.
- A dropped stream and retry do not duplicate user input or tool side effects.
- Restart between inner completion and outer completion has a deterministic
  recovery path.

### Phase 5: Wire direct deletion and retention

Primary file:

- `backend/agent_memory_backend/foundry_hosted_maf_runtime.py`

Deletion order:

1. Read the backend-owned inner conversation ID.
2. Delete the inner conversation directly with `conversations.delete`.
3. Confirm deletion or a documented already-absent result.
4. Delete the outer Foundry conversation.
5. Delete the Hosted session if it still exists.
6. Delete the backend transcript/memory record through the existing coordinator
   flow.

Failure behavior:

- If inner deletion fails transiently, leave all mappings intact and surface the
  error so deletion can be retried.
- Hosted-session absence must not prevent inner deletion.
- Treat only documented not-found responses as idempotent success.
- Emit privacy-safe metrics for cleanup attempts, failures, and orphan
  detection.

Retention facts to preserve in documentation:

- Azure OpenAI Responses data is retained for 30 days by default unless
  explicitly deleted.
- Hosted Agent compute idles after 15 minutes, restores `$HOME` on resume, and
  permanently deletes an inactive session after 30 days.
- Hosted session deletion does not prove deletion of a separately created inner
  model conversation.

Exit criteria:

- A deletion test proves the inner conversation, outer conversation, Hosted
  session, Cosmos transcript, and conversation memory are all absent.
- The same deletion still succeeds when the Hosted session was already removed.
- Repeating deletion is safe and does not recreate state.

### Phase 6: Directive canary

Deploy a new immutable directive-only image and release ID.

Canary scenarios:

- Three or more user turns that depend on facts from earlier turns.
- Multiple tool calls in one turn.
- Streaming cancellation and client disconnect.
- Retry after a response is committed but before client completion.
- Concurrent duplicates across separate backend/host processes.
- Hosted compute deactivation and resume after more than 15 minutes.
- Conversation deletion with and without a live Hosted session.
- Cross-user attempts with known outer, inner, response, and Hosted session IDs.
- Long conversation behavior near the model context window.
- In-place bootstrap of a pre-migration directive conversation.

Telemetry:

- Count inner conversation creation, continuation, bootstrap, restore,
  mismatch, cleanup, lease conflict, and failure events.
- Record request-shape booleans only: `store`, has conversation ID, has previous
  response ID, has encrypted reasoning include.
- Do not record IDs or content.
- Compare input tokens, cached tokens, time to first token, total latency, tool
  count, and error rate with the current directive release.

Acceptance:

- 100 percent of inner model calls have `store=true`.
- 100 percent have the expected dedicated conversation.
- 0 percent request `reasoning.encrypted_content`.
- No full outer-history replay occurs after bootstrap.
- No duplicate tool side effects occur under tested retries.
- Explicit deletion leaves no retrievable inner state even after Hosted-session
  loss.

Rollback:

- Route new conversations to the previous immutable directive image if needed.
- Do not run an already-stateful conversation through the old stateless image.
- Keep schema-aware routing or require a new conversation for rollback traffic.

### Phase 7: Evaluate support-agent adoption

Only after directive acceptance:

1. Run the same dedicated-conversation request-shape tests with `gpt-4o-mini`.
2. Verify tools, three-turn continuation, resume, retry, and deletion.
3. Compare the known-good support stack with the candidate pinned stack.
4. Upgrade support only if every request has a conversation ID and no encrypted
   reasoning include.
5. Retain the existing support image as rollback.

Do not infer support compatibility from the directive result; the models and
hosting versions differ.

### Phase 8: Remove obsolete stateless machinery

After the directive release is stable:

- Remove the directive `store=false` setting.
- Replace encrypted-reasoning replay tests with stateful conversation guards.
- Remove directive documentation that describes encrypted replay as intended.
- Remove the canary-only mode switch after the rollback window; retain rollback
  through immutable image/version selection.
- Keep the support replay guard until support completes its own migration.

Ask before deleting shared encrypted-reasoning conversion code because the
support agent or third-party stateless callers may still depend on it.

## Required deterministic tests

### Adapter tests

- First request: `store=true`, `conversation=<conv_*>`.
- Tool continuation: same conversation, only function output as input.
- No `reasoning.encrypted_content` on any request.
- A request lacking a conversation fails the directive guard.

### Host tests

- Host resolves the backend-created inner conversation and never accepts a raw
  ID from public request/model data.
- Existing outer conversation bootstraps history exactly once.
- Later turns do not load or replay outer history into the model.
- Streaming and non-streaming paths persist the same session state.
- A failed model call advances only the recovery ledger revision, never the
  durable outer transcript; retry first rotates to a fresh inner conversation.
- Owner, outer conversation, agent version, and schema mismatches fail closed.
- Atomic file replacement survives an interrupted write.
- A hosting dependency change that invalidates the private override fails its
  compatibility guard.

### Backend tests

- The same outer conversation and Hosted session are reused.
- Creation persists one distinct inner conversation and cleans every partial
  allocation on failure.
- The internal resolver enforces user, Hosted session, call, agent, and release
  binding.
- Old runtime state allocates and persists exactly one inner conversation under
  concurrent migration attempts.
- A durable application turn ID is sent.
- Durable and in-process leases reject concurrent calls.
- Deletion executes in the required order and stops on inner deletion failure.
- Inner deletion still runs when the Hosted session is already absent.
- Runtime restoration preserves stateful-release compatibility.

### Acceptance tests

- Turn 1: establish a fact.
- Turn 2: perform at least two directive tools.
- Turn 3: answer using the fact and prior tool result without replayed history.
- Resume after idle deactivation.
- Retry without duplicated input or tool side effects.
- Delete and verify every state layer is absent.
- Attempt cross-user resumption and receive an authorization/not-found result.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Inner and outer histories drift | Outer remains authoritative for UI/audit; inner state is advanced under a durable turn ledger and conditional lease. |
| Stored model data survives app deletion | Persist the inner ID in backend `RuntimeState` and delete it directly before outer/session deletion. |
| Hosted session or session file disappears | Reconstruct `AgentSession` from backend-owned inner state; never make `$HOME` the only copy. |
| Dependency drift restores encrypted reasoning | Pin the complete directive and support stacks and assert outbound request shape. |
| Adapter 1.11.0 sees a request without continuation | Guard every directive call for a `conversation_id`; treat absence as a test and runtime failure. |
| Concurrent turns branch service state | Cross-process conditional lease plus existing backend and host-local locks. |
| Retry duplicates tool side effects | Durable turn ID, payload hash, pending/completed ledger, deterministic duplicate handling. |
| Old and new agent versions share state | Store agent/release identity in the session record and reject incompatible restoration. |
| Hosted session expires before app conversation | Add lifecycle monitoring and define a separate rebootstrap migration before claiming conversations can outlive 30 days of inactivity. |
| Private hosting override breaks on upgrade | Pin the hosting build and guard the exact override signature/behavior; move to a public hook when available. |

## Final go-live gate

The migration is complete only when all of the following are true:

- The directive agent has no `store=false` model calls.
- Every inner request uses the dedicated inner conversation.
- Encrypted reasoning is absent from outbound requests.
- Cross-turn recall works without full-history replay.
- Streaming, tools, restart, concurrency, retry, and cancellation tests pass.
- Inner, outer, Hosted, backend, and memory state all delete correctly.
- Tenant ownership checks cover all service-side IDs.
- The exact dependency stack is pinned and guarded.
- The previous immutable directive image has a tested rollback path.

## Official references

- [Azure OpenAI Responses API](https://learn.microsoft.com/azure/foundry/openai/how-to/responses)
- [Agent Framework sessions](https://learn.microsoft.com/agent-framework/agents/conversations/session)
- [Agent Framework storage](https://learn.microsoft.com/agent-framework/agents/conversations/storage)
- [Host Agent Framework agents in Foundry](https://learn.microsoft.com/azure/foundry/how-to/develop/framework-hosted-agents)
- [Foundry Hosted Agents concepts](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
