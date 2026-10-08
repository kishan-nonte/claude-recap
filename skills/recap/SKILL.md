---
name: recap
description: Summarize a Claude Code session as a scannable list of your questions paired with what Claude did for each - for when you return to a long session after a break and don't want to scroll the whole transcript. Use when the user asks what we did, what this chat was about, for a session summary or recap, or invokes /recap.
---

# Recap

Prints the session as `question -> what Claude did` pairs, read straight from the
session transcript. The summary is derived from the actual tool calls, so it
costs no tokens and cannot hallucinate.

## Usage

Run `recap.py` with Bash from this skill's own directory. The invocation header
gives you that path as "Base directory for this skill" - use it, so the command
works wherever the plugin is installed:

```bash
python3 -I <skill-dir>/recap.py                 # current session
python3 -I <skill-dir>/recap.py --list          # sessions + days until cleanup
python3 -I <skill-dir>/recap.py --session <id>  # a specific session (id prefix is fine)
python3 -I <skill-dir>/recap.py --full          # don't truncate questions
python3 -I <skill-dir>/recap.py --project <dir> # a different project
```

Show the output to the user as-is. Do not re-summarize it; the point is that it
is cheap and literal.

## Prose mode

If the user asks for human-readable summaries rather than tool counts, run with
`--prose`. That emits JSON instead of a report. Read it and write one short line
per turn describing what actually happened, keeping the same numbering.

## What counts as a question

Skill bodies and slash-command payloads are written to the transcript as user
messages, so they are filtered out: a real prompt carries a `promptSource`
(`typed`, `suggestion_accepted`, `queued`), injected ones carry none, and
`system` marks `<task-notification>` wake-ups. A skill the user invoked shows up
as a `Skill` tool call under the question that triggered it, which is where it
belongs.

Transcripts written before `promptSource` existed fall back to counting every
user message, and the header says so.

## Retention

Transcripts are deleted after `cleanupPeriodDays` (default 30). When a transcript
is gone, `--session <id>` falls back to `~/.claude/history.jsonl`, which keeps
prompts far longer but holds no record of Claude's actions. The output says so.
Tell the user plainly that the actions are unrecoverable rather than guessing.

To keep transcripts longer, set `cleanupPeriodDays` in `~/.claude/settings.json`.

## Secrets

Prompts often contain pasted credentials. The script redacts AWS keys, GitHub and
Slack tokens, API keys, JWTs and long base64 blobs before printing. If you spot a
credential that slipped through, tell the user and add a pattern to `SECRETS` in
`recap.py`.
