import assert from "node:assert/strict";
import test from "node:test";

import { parseIdeas, summarizeIdeas } from "./ideas.mjs";
import {
    buildKickoffPrompt,
    buildSessionCreationInstruction,
} from "./launch.mjs";
import { renderIdeasHtml } from "./renderer.mjs";

const sampleIdeas = `# Ideas

## Ready idea

Build the ready idea.

**Implementation plan:** [Ready plan](docs/TEMP-plan-ready.md)

<sub>**Date:** 2026-07-27 \u00b7 **Author:** @octocat \u00b7 **Implemented:** No</sub>

## Unplanned idea

Research this first.

<sub>**Date:** 2026-07-27 \u00b7 **Author:** Unknown \u00b7 **Implemented:** No</sub>

# Archive

## Finished idea

Already delivered.

<sub>**Date:** 2026-07-20 \u00b7 **Author:** @octocat \u00b7 **Implemented:** Yes \u00b7 **Implemented date:** 2026-07-21</sub>
`;

test("parses active and archived ideas with plan metadata", () => {
    const ideas = parseIdeas(sampleIdeas);

    assert.equal(ideas.length, 3);
    assert.deepEqual(summarizeIdeas(ideas), {
        active: 2,
        archive: 1,
        actionable: 2,
        implementationReady: 1,
        needsPlanning: 1,
        total: 3,
    });
    assert.deepEqual(ideas[0].plan, {
        label: "Ready plan",
        target: "docs/TEMP-plan-ready.md",
    });
    assert.equal(ideas[0].metadata.author, "@octocat");
    assert.equal(ideas[2].archived, true);
    assert.equal(ideas[2].implemented, true);
});

test("builds the exact requested kickoff prompts", () => {
    const idea = { title: "Ready idea" };

    assert.equal(
        buildKickoffPrompt(idea, "implementation"),
        'Implement idea "Ready idea" according to plan attached to the idea. Note we don\'t need any data migration or consistency or keep existing sessions etc. can be destrucitve, no real users are using the app now. I prefere straightforward implementation.',
    );
    assert.equal(
        buildKickoffPrompt(idea, "planning"),
        'Create comprehend implementation plan for idea "Ready idea". Take into account current project implementation and plan the idea. If you do not have enough information, ask. The plan will be in "./docs" folder and naming convention is TEMP-plan-<meaningfull name>.md.',
    );
});

test("session instruction starts an autopilot child session without changing the prompt", () => {
    const idea = { title: "Ready idea" };
    const kickoffPrompt = buildKickoffPrompt(idea, "implementation");
    const instruction = buildSessionCreationInstruction(
        idea,
        "implementation",
    );

    assert.match(instruction, /create_session tool exactly once/);
    assert.match(instruction, /kickoff\.mode: "autopilot"/);
    assert.ok(instruction.includes(`<kickoff-prompt>\n${kickoffPrompt}\n`));
});

test("renders the correct action only for non-implemented ideas", () => {
    const html = renderIdeasHtml({
        ideas: parseIdeas(sampleIdeas),
        initialView: "active",
        token: "test-token",
    });

    assert.match(
        html,
        /data-idea-id="ready-idea"\s+data-action="implementation"/,
    );
    assert.match(
        html,
        /data-idea-id="unplanned-idea"\s+data-action="planning"/,
    );
    assert.doesNotMatch(
        html,
        /data-idea-id="finished-idea"\s+data-action=/,
    );
    assert.match(html, /id="panel-active"/);
    assert.match(html, /id="panel-archive"/);
});
