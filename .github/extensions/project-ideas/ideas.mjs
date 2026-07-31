import { readFile } from "node:fs/promises";

const archiveHeadingPattern = /^#\s+Archive\s*$/i;
const ideaHeadingPattern = /^##\s+(.+?)\s*$/;
const metadataPattern = /^\s*<sub>(.*?)<\/sub>\s*$/i;
const planPattern =
    /^\s*\*\*Implementation plan:\*\*\s*(.+?)\s*$/i;
const markdownLinkPattern = /^\[([^\]]+)\]\(([^)]+)\)$/;

function trimBlankLines(lines) {
    let start = 0;
    let end = lines.length;

    while (start < end && lines[start].trim() === "") {
        start += 1;
    }
    while (end > start && lines[end - 1].trim() === "") {
        end -= 1;
    }

    return lines.slice(start, end);
}

function parseMetadata(rawMetadata) {
    const metadata = {};
    if (!rawMetadata) {
        return metadata;
    }

    for (const part of rawMetadata.split(/\s+\u00b7\s+/)) {
        const match = part.match(/^\*\*([^*]+):\*\*\s*(.*?)\s*$/);
        if (!match) {
            continue;
        }
        const key = match[1]
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "_")
            .replace(/^_|_$/g, "");
        metadata[key] = match[2].trim();
    }

    return metadata;
}

function parsePlan(rawPlan) {
    if (!rawPlan) {
        return undefined;
    }

    const linkMatch = rawPlan.match(markdownLinkPattern);
    if (linkMatch) {
        return {
            label: linkMatch[1].replace(/`/g, ""),
            target: linkMatch[2],
        };
    }

    return {
        label: rawPlan.replace(/\*\*|`/g, "").trim(),
        target: undefined,
    };
}

function slugify(value) {
    return value
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
}

function finalizeIdea(draft) {
    let rawMetadata;
    let rawPlan;
    const descriptionLines = [];

    for (const line of draft.lines) {
        const metadataMatch = line.match(metadataPattern);
        if (metadataMatch) {
            rawMetadata = metadataMatch[1];
            continue;
        }

        const planMatch = line.match(planPattern);
        if (planMatch) {
            rawPlan = planMatch[1];
            continue;
        }

        descriptionLines.push(line);
    }

    const metadata = parseMetadata(rawMetadata);
    return {
        archived: draft.archived,
        description: trimBlankLines(descriptionLines).join("\n"),
        implemented: /^yes$/i.test(metadata.implemented ?? ""),
        metadata,
        plan: parsePlan(rawPlan),
        title: draft.title,
    };
}

function assignStableIds(ideas) {
    const occurrences = new Map();

    return ideas.map((idea, index) => {
        const baseId = slugify(idea.title) || `idea-${index + 1}`;
        const occurrence = (occurrences.get(baseId) ?? 0) + 1;
        occurrences.set(baseId, occurrence);
        return {
            ...idea,
            id: occurrence === 1 ? baseId : `${baseId}-${occurrence}`,
        };
    });
}

export function parseIdeas(source) {
    const ideas = [];
    let archived = false;
    let draft;

    const flushDraft = () => {
        if (draft) {
            ideas.push(finalizeIdea(draft));
            draft = undefined;
        }
    };

    for (const line of source.split(/\r?\n/)) {
        if (archiveHeadingPattern.test(line)) {
            flushDraft();
            archived = true;
            continue;
        }

        const headingMatch = line.match(ideaHeadingPattern);
        if (headingMatch) {
            flushDraft();
            draft = {
                archived,
                lines: [],
                title: headingMatch[1].trim(),
            };
            continue;
        }

        draft?.lines.push(line);
    }
    flushDraft();

    return assignStableIds(ideas);
}

export async function readIdeasFile(path) {
    return parseIdeas(await readFile(path, "utf8"));
}

export function summarizeIdeas(ideas) {
    return {
        active: ideas.filter((idea) => !idea.archived).length,
        archive: ideas.filter((idea) => idea.archived).length,
        actionable: ideas.filter((idea) => !idea.implemented).length,
        implementationReady: ideas.filter(
            (idea) => !idea.implemented && idea.plan,
        ).length,
        needsPlanning: ideas.filter(
            (idea) => !idea.implemented && !idea.plan,
        ).length,
        total: ideas.length,
    };
}
