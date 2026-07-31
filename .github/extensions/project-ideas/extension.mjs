import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createCanvas, joinSession } from "@github/copilot-sdk/extension";

import { readIdeasFile, summarizeIdeas } from "./ideas.mjs";
import {
    buildSessionCreationInstruction,
    getExpectedAction,
} from "./launch.mjs";
import { renderErrorHtml, renderIdeasHtml } from "./renderer.mjs";

const extensionDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(extensionDirectory, "../../..");
const ideasPath = resolve(repositoryRoot, "IDEAS.md");
const servers = new Map();
const recentLaunches = new Map();
const launchDeduplicationWindowMs = 10_000;
const maximumRequestBytes = 8_192;

let session;

class HttpError extends Error {
    constructor(statusCode, message) {
        super(message);
        this.statusCode = statusCode;
    }
}

function setSecurityHeaders(res) {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'",
    );
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("X-Content-Type-Options", "nosniff");
}

function sendHtml(res, statusCode, html) {
    setSecurityHeaders(res);
    res.writeHead(statusCode, {
        "Content-Type": "text/html; charset=utf-8",
    });
    res.end(html);
}

function sendJson(res, statusCode, body) {
    setSecurityHeaders(res);
    res.writeHead(statusCode, {
        "Content-Type": "application/json; charset=utf-8",
    });
    res.end(JSON.stringify(body));
}

async function readJsonBody(req) {
    const chunks = [];
    let size = 0;

    for await (const chunk of req) {
        size += chunk.length;
        if (size > maximumRequestBytes) {
            throw new HttpError(413, "Request body is too large.");
        }
        chunks.push(chunk);
    }

    try {
        return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
        throw new HttpError(400, "Request body must be valid JSON.");
    }
}

function requireCanvasToken(req, token) {
    if (req.headers["x-canvas-token"] !== token) {
        throw new HttpError(403, "Canvas authorization failed.");
    }
}

function pruneRecentLaunches(now) {
    for (const [key, launchedAt] of recentLaunches) {
        if (now - launchedAt >= launchDeduplicationWindowMs) {
            recentLaunches.delete(key);
        }
    }
}

async function queueSessionLaunch(ideaId, requestedAction) {
    const ideas = await readIdeasFile(ideasPath);
    const idea = ideas.find((candidate) => candidate.id === ideaId);

    if (!idea) {
        throw new HttpError(404, "The idea no longer exists in IDEAS.md.");
    }
    if (idea.implemented) {
        throw new HttpError(409, "This idea is already implemented.");
    }

    const expectedAction = getExpectedAction(idea);
    if (requestedAction !== expectedAction) {
        throw new HttpError(
            409,
            `This idea now requires the ${expectedAction} action. Refresh the canvas.`,
        );
    }

    const now = Date.now();
    const launchKey = `${idea.id}:${requestedAction}`;
    pruneRecentLaunches(now);
    if (recentLaunches.has(launchKey)) {
        throw new HttpError(
            409,
            "A session launch for this idea was already requested. Please wait.",
        );
    }

    recentLaunches.set(launchKey, now);
    try {
        const prompt = buildSessionCreationInstruction(idea, requestedAction);
        await session.send({ prompt });
    } catch (error) {
        recentLaunches.delete(launchKey);
        const message = error instanceof Error ? error.message : String(error);
        throw new HttpError(
            503,
            `Could not request a new Copilot session: ${message}`,
        );
    }

    const actionLabel =
        requestedAction === "implementation" ? "Implementation" : "Planning";
    return {
        message: `${actionLabel} session requested for "${idea.title}".`,
    };
}

async function handleRequest(entry, req, res) {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");

    if (req.method === "GET" && url.pathname === "/") {
        try {
            const ideas = await readIdeasFile(ideasPath);
            sendHtml(
                res,
                200,
                renderIdeasHtml({
                    ideas,
                    initialView: entry.initialView,
                    token: entry.token,
                }),
            );
        } catch (error) {
            sendHtml(res, 500, renderErrorHtml(error));
        }
        return;
    }

    if (req.method === "POST" && url.pathname === "/launch") {
        requireCanvasToken(req, entry.token);
        const input = await readJsonBody(req);
        if (
            typeof input?.ideaId !== "string" ||
            !["implementation", "planning"].includes(input?.action)
        ) {
            throw new HttpError(
                400,
                "The launch request must include a valid ideaId and action.",
            );
        }
        const result = await queueSessionLaunch(input.ideaId, input.action);
        sendJson(res, 202, result);
        return;
    }

    sendJson(res, 404, { message: "Not found." });
}

async function startServer(initialView) {
    const entry = {
        initialView,
        server: undefined,
        token: randomBytes(24).toString("hex"),
        url: "",
    };

    const server = createServer((req, res) => {
        void handleRequest(entry, req, res).catch((error) => {
            const statusCode =
                error instanceof HttpError ? error.statusCode : 500;
            const message =
                error instanceof Error
                    ? error.message
                    : "Unexpected canvas error.";
            sendJson(res, statusCode, { message });
        });
    });
    entry.server = server;

    await new Promise((resolveListen, rejectListen) => {
        server.once("error", rejectListen);
        server.listen(0, "127.0.0.1", () => {
            server.off("error", rejectListen);
            resolveListen();
        });
    });

    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    entry.url = `http://127.0.0.1:${port}/`;
    return entry;
}

session = await joinSession({
    canvases: [
        createCanvas({
            id: "project-ideas",
            displayName: "Project ideas",
            description:
                "Browse current and archived project ideas and launch planning or implementation sessions.",
            inputSchema: {
                type: "object",
                additionalProperties: false,
                properties: {
                    initialView: {
                        type: "string",
                        enum: ["active", "archive"],
                    },
                },
            },
            actions: [
                {
                    name: "get_ideas_summary",
                    description:
                        "Return current, archived, and actionable idea counts from IDEAS.md.",
                    handler: async () => {
                        const ideas = await readIdeasFile(ideasPath);
                        return summarizeIdeas(ideas);
                    },
                },
            ],
            open: async (ctx) => {
                const initialView =
                    ctx.input?.initialView === "archive"
                        ? "archive"
                        : "active";
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(initialView);
                    servers.set(ctx.instanceId, entry);
                } else {
                    entry.initialView = initialView;
                }

                const summary = summarizeIdeas(
                    await readIdeasFile(ideasPath),
                );
                return {
                    title: "Project ideas",
                    status: `${summary.active} current / ${summary.archive} archived`,
                    url: entry.url,
                };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (!entry) {
                    return;
                }
                servers.delete(ctx.instanceId);
                await new Promise((resolveClose) =>
                    entry.server.close(resolveClose),
                );
            },
        }),
    ],
});
