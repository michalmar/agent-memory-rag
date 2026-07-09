---
name: idea-capture
description: Capture IDEA messages into the project IDEAS.md file with readable metadata.
---

# Idea Capture Skill

## Purpose

Use this skill when the user writes `IDEA: ...` and wants the idea saved in this repository.

The goal is to keep a simple, append-only idea log in the root `IDEAS.md` file. The log should be readable in plain Markdown and easy to update when ideas are implemented.

## Trigger

Use this skill when a user message starts with or clearly contains:

```text
IDEA: <idea text>
```

Capture the text after the first `IDEA:` marker as the idea body. Preserve the user's meaning and wording; lightly clean only obvious extra whitespace.

## File format

Store ideas in the repository root file:

```text
IDEAS.md
```

If the file does not exist, create it with this heading:

```markdown
# Ideas

Use this file to collect workshop and project ideas. New ideas are appended at the end so the list stays easy to scan.
```

Each idea must be a separate `##` section appended to the end of the file:

```markdown
## Short idea title

**Date:** YYYY-MM-DD  
**Author:** @username  
**Implemented:** No

Idea text goes here.
```

## Capture procedure

1. Read `IDEAS.md` first so existing ideas are preserved.
2. Derive a short, human-readable `##` heading from the idea text.
3. Use the current date in `YYYY-MM-DD` format.
4. Use the current user as the author when available; otherwise use `Unknown`.
5. Set `Implemented` to `No` for new ideas.
6. Append the new idea to the end of `IDEAS.md`; do not reorder or rewrite older ideas unless the user asks.
7. Keep the idea text concise and readable. Use plain paragraphs unless the user provides a list.

## Marking ideas implemented

When the user asks to mark an idea as done or implemented:

1. Find the matching `##` idea section.
2. Change `**Implemented:** No` to `**Implemented:** Yes`.
3. Preserve the original date, author, title, and idea text.
4. If multiple ideas could match, ask which one to update.

## Example prompt

```text
IDEA: Add a short facilitator checklist for choosing the right Copilot mode during demos.
```
