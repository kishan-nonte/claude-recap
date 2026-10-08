# recap

A Claude Code plugin that turns a long session into a scannable list of **your
questions** paired with **what Claude actually did** for each one.

You come back to a session after a day and have no idea what you asked. The
scrollback is thousands of lines of tool calls. `/recap` gives you the shape of
the conversation in a dozen lines.

```
$ /recap

session a1b2c3d4  .  /home/you/project  .  main
2026-10-08 13:49 -> 14:03  .  4 questions

#1   13:49   2m  fix the auth redirect bug
          |- Readx4, Editx2, Bashx3
          |- files: session.ts, auth.ts
          `- ran: Run the test suite

#2   13:52   5m  now add refresh tokens
          |- Editx3, Bashx2
          `- files: tokens.ts, session.ts

#3   13:58  <1m  why did CI fail?
          `- Bashx2, Read

#4   14:01   2m  ship it
          |- Bashx4
          `- ran: Commit changes; Push to origin
```

## Why it is cheap

The summaries are **derived from the actual tool calls** in the transcript, not
written by a model. `/recap` costs no tokens, runs instantly, and cannot
hallucinate what happened. If you want prose instead of tool counts, `--prose`
hands the extracted data to Claude to phrase.

## Install

```
/plugin marketplace add kishan-nonte/claude-recap
/plugin install recap@claude-recap
```

Then run `/recap` in any session. Requires Python 3.9+ (no dependencies).

## Usage

| Command | What it does |
|---|---|
| `/recap` | the current session |
| `/recap --list` | every session for this project, with days left before cleanup |
| `/recap --session <id>` | a specific session (an id prefix is enough) |
| `/recap --full` | don't truncate long questions |
| `/recap --prose` | emit JSON so Claude can write human one-liners |

## What counts as a question

Claude Code writes a lot of things to the transcript as "user" messages that you
never typed: tool results, skill bodies, slash-command payloads, task
notifications. In one real 1.4 MB session there were 69 user entries and only
**7 actual questions**.

`recap` keeps only genuine prompts. A real one carries a `promptSource`
(`typed`, `suggestion_accepted`, `queued`); injected ones carry none, and
`system` marks task-notification wake-ups. A skill you invoked shows up as a
`Skill` tool call *under* the question that triggered it, where it belongs.

Transcripts written before `promptSource` existed fall back to counting every
user message, and the header says so rather than silently guessing.

## Retention: read this one

Claude Code deletes transcripts after `cleanupPeriodDays`, **default 30**. On the
machine this was built on, 102 of 119 sessions had already lost their transcript.

When a transcript is gone, `--session <id>` falls back to
`~/.claude/history.jsonl`, which keeps your prompts far longer but holds no
record of what Claude did. The output says so plainly instead of guessing.

To keep more, set it in `~/.claude/settings.json`:

```json
{ "cleanupPeriodDays": 3650 }
```

Nothing prunes after that, so keep an eye on `du -sh ~/.claude/projects`.

## Secrets

Prompts often contain pasted credentials - this was found the hard way, with a
live AWS key sitting in a months-old history file. Before printing anything,
`recap` redacts AWS key ids, GitHub and Slack tokens, API keys, JWTs and long
base64 blobs.

**Redaction is display-only.** It does not clean the files on disk. If a
credential was ever pasted into a prompt, rotate it.

## Privacy

Everything runs locally and reads only files already on your machine
(`~/.claude/projects/*.jsonl` and `~/.claude/history.jsonl`). Nothing is sent
anywhere.

## Tests

```
python3 -I tests/test_recap.py
```

16 tests, synthetic fixtures only - no real transcripts in this repo.

## License

MIT
