---
name: note
description: Append a feedback bullet to docs/feedback/inbox.md. Use whenever you encounter friction with shipped naml work and want to capture the thought without leaving flow. Auto-detects the most recently merged sprint as the target.
disable-model-invocation: true
---

# Note

Thin capture skill for feedback. Adds a structured bullet to `docs/feedback/inbox.md` in two seconds.

## Process

### 1. Parse the invocation

The skill is called in one of three shapes:

**a) Inline single bullet**
```
/note slider doesn't snap to grid values
```
→ Append one bullet under today's date with default target.

**b) Inline with explicit target**
```
/note --sprint=eval-harness CLI doesn't accept stdin
```
→ Same as above but with the named sprint as the target.

**c) No args (interactive multi-bullet)**
```
/note
```
→ Prompt the user: "Paste your feedback (one bullet per line, blank line to finish):". Capture, then append all bullets under today's date with default target.

### 2. Detect target sprint

If `--sprint=` was specified, use that.

Otherwise: look at `.naml/sprints/` and find the most recently merged sprint by `state/sprint.json` (or by filesystem mtime as a fallback). Use it as the default target.

If no merged sprint exists yet: target is `unassigned`.

### 3. Append to inbox

Read `docs/feedback/inbox.md`. Check whether today's date + target combination already has a heading:

```markdown
## YYYY-MM-DD — target: <sprint-id>
```

If yes: append the bullet(s) to that section.

If no: append a new section (with a blank line separator) for today + target, then add the bullet(s).

Result example:

```markdown
# Feedback inbox

## 2026-05-19 — target: html-renderer

- Slider doesn't snap to grid values
- API response is missing `created_at` field

## 2026-05-20 — target: html-renderer

- HTML output uses `<b>` instead of `<strong>` — accessibility issue
```

### 4. Confirm

Reply with a single line:

```
Added 1 bullet to docs/feedback/inbox.md (target: html-renderer).
Run /grill-feedback when you're ready to consolidate.
```

For multi-bullet input:

```
Added 3 bullets to docs/feedback/inbox.md (target: html-renderer).
Run /grill-feedback when you're ready to consolidate.
```

## Safety rules

- **Never modify other dates or sections.** Only append.
- **Never overwrite existing bullets.** Duplicates are fine; `/grill-feedback` de-duplicates at classification time.
- **Never edit `docs/feedback/archive/`.** Only the live `inbox.md`.
- **Never create the inbox file from scratch silently.** If `docs/feedback/inbox.md` doesn't exist, tell the user to run `/setup-naml` first.

## Format invariants

- Top of file is always `# Feedback inbox\n\n`
- Section headers are `## YYYY-MM-DD — target: <sprint-id>` (em-dash or `--`, but be consistent)
- Bullets are simple `- <text>` lines, no nested markup required

`/grill-feedback` is lenient — if the user hand-edits and breaks the strict format, it still extracts bullets. But this skill should always produce the canonical format on its own.
