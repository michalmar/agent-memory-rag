---
name: idea-capture
description: Capture IDEA messages into the project IDEAS.md file with readable metadata.
---

# Idea Capture Skill

## Purpose

Use this skill when the user writes `IDEA: ...` and wants the idea saved in this repository.

The goal is to keep a simple idea log in the root `IDEAS.md` file. Active ideas and implemented ideas are kept in distinct sections so the list remains easy to scan.

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

Use this file to collect workshop and project ideas. Active ideas stay here; implemented ideas are moved to the Archive section. New ideas are added first.
```

Each idea must be a separate `##` section. Put its description first and its compact metadata on one line after the description:

```markdown
## Short idea title

Idea text goes here.

<sub>**Date:** YYYY-MM-DD · **Author:** @username · **Implemented:** No</sub>
```

## Capture procedure

1. Read `IDEAS.md` first so existing ideas are preserved.
2. Derive a short, human-readable `##` heading from the idea text.
3. Use the current date in `YYYY-MM-DD` format.
4. Use the current user as the author when available; otherwise use `Unknown`.
5. Set `Implemented` to `No` for new ideas.
6. Insert the new idea immediately after the `# Ideas` introduction, before all existing active ideas and the `# Archive` section.
7. Keep the idea text concise and readable. Use plain paragraphs unless the user provides a list.

## Marking ideas implemented

When the user asks to mark an idea as done or implemented:

1. Find the matching active `##` idea section.
2. Change `**Implemented:** No` to `**Implemented:** Yes` in the idea's compact metadata line.
3. If the current date differs from the idea's `**Date:**`, add `· **Implemented date:** YYYY-MM-DD` to the same metadata line.
4. Move the complete idea section to the end of a top-level `# Archive` section. Create that section if it does not exist.
5. Preserve the original date, author, title, and idea text.
6. If multiple ideas could match, ask which one to update.

## Example prompt

```text
IDEA: Add a short facilitator checklist for choosing the right Copilot mode during demos.
```
