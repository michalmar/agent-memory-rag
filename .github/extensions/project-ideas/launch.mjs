export function getExpectedAction(idea) {
    return idea.plan ? "implementation" : "planning";
}

export function buildKickoffPrompt(idea, action) {
    if (action === "implementation") {
        return `Implement idea "${idea.title}" according to plan attached to the idea. Note we don't need any data migration or consistency or keep existing sessions etc. can be destrucitve, no real users are using the app now. I prefere straightforward implementation.`;
    }

    if (action === "planning") {
        return `Create comprehend implementation plan for idea "${idea.title}". Take into account current project implementation and plan the idea. If you do not have enough information, ask. The plan will be in "./docs" folder and naming convention is TEMP-plan-<meaningfull name>.md.`;
    }

    throw new Error(`Unsupported launch action: ${action}`);
}

function createSessionName(idea, action) {
    const prefix = action === "implementation" ? "Implement" : "Plan";
    return `${prefix} ${idea.title}`.slice(0, 40).trim();
}

export function buildSessionCreationInstruction(idea, action) {
    const kickoffPrompt = buildKickoffPrompt(idea, action);
    const sessionName = createSessionName(idea, action);

    return `A Project ideas canvas button was clicked. Perform only this orchestration action:

Call the create_session tool exactly once for the current project. Omit base_branch so the project default branch is used. Set these values:
- name: "${sessionName}"
- coordinate_with_creator: false
- notify_on_idle: "once"
- kickoff.mode: "autopilot"
- kickoff.prompt: exactly the text between the <kickoff-prompt> markers below, without the markers

<kickoff-prompt>
${kickoffPrompt}
</kickoff-prompt>

Do not execute or reinterpret the kickoff prompt in this session, and do not change its spelling or punctuation. After create_session returns, respond only with a brief confirmation that the new session was started.`;
}
