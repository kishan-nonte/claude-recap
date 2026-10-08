#!/usr/bin/env python3
"""recap - summarize a Claude Code session as question -> what-Claude-did pairs.

Reads session transcripts from ~/.claude/projects/<encoded-cwd>/*.jsonl.
Falls back to ~/.claude/history.jsonl (questions only) for sessions whose
transcript has aged out of the 30-day cleanup window.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter

CLAUDE_DIR = os.path.expanduser("~/.claude")
PROJECTS = os.path.join(CLAUDE_DIR, "projects")
HISTORY = os.path.join(CLAUDE_DIR, "history.jsonl")

SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
PASTED = re.compile(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", re.S)
# an unclosed opener still appears when the paste was truncated
PASTED_OPEN = re.compile(r"<pasted_content[^>]*>", re.S)
# <command-name>, <local-command-stdout>, <local-command-caveat>, etc.
CMDTAG = re.compile(r"<((?:local-)?command-[a-z-]+)[^>]*>.*?</\1[^>]*>", re.S)
CMDTAG_OPEN = re.compile(r"</?(?:local-)?command-[a-z-]+[^>]*>", re.S)
PATH_KEYS = ("file_path", "notebook_path", "path")

# Skill bodies and slash-command payloads are recorded as user messages too.
# Real prompts carry a promptSource ("typed", "suggestion_accepted", "queued");
# injected ones carry none, and "system" marks <task-notification> wake-ups.
NON_HUMAN_SOURCES = {"system"}


def is_human_prompt(entry):
    source = entry.get("promptSource")
    return bool(source) and source not in NON_HUMAN_SOURCES

# Prompts routinely contain pasted credentials; never echo them back.
SECRETS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[AWS_KEY_ID]"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}\b"), "[GITHUB_TOKEN]"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"), "[API_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[SLACK_TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"), "[JWT]"),
    (re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40,}(?![A-Za-z0-9/+=])"), "[REDACTED]"),
]


def redact(text):
    for pattern, label in SECRETS:
        text = pattern.sub(label, text)
    return text


def encode_project(path):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def project_dir(path):
    return os.path.join(PROJECTS, encode_project(path))


def parse_ts(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def clean_prompt(text):
    text = SYSREM.sub(" ", text)

    def _pasted(match):
        size = len(match.group(1))
        return f"[pasted {size // 1024}KB]" if size >= 1024 else f"[pasted {size}B]"

    text = PASTED.sub(_pasted, text)
    text = PASTED_OPEN.sub("[pasted]", text)
    text = CMDTAG.sub(" ", text)
    text = CMDTAG_OPEN.sub(" ", text)
    return redact(re.sub(r"\s+", " ", text).strip())


def message_text(content):
    """Return user-authored text, or None for tool results and other noise."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts) if parts else None
    return None


def parse_session(path, strict=True):
    turns, meta, current = [], {}, None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("isSidechain"):
                continue
            kind = entry.get("type")
            message = entry.get("message") or {}

            if kind == "user":
                raw = message_text(message.get("content"))
                if raw is None:
                    continue
                if strict and not is_human_prompt(entry):
                    continue
                question = clean_prompt(raw)
                if not question:
                    continue
                meta.setdefault("cwd", entry.get("cwd"))
                meta.setdefault("branch", entry.get("gitBranch"))
                current = {
                    "ts": entry.get("timestamp"),
                    "end": entry.get("timestamp"),
                    "question": question,
                    "tools": Counter(),
                    "files": [],
                    "commands": [],
                    "reply": "",
                }
                turns.append(current)

            elif kind == "assistant" and current is not None:
                current["end"] = entry.get("timestamp") or current["end"]
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text", "").strip():
                        current["reply"] = block["text"].strip()
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "?")
                        current["tools"][name] += 1
                        payload = block.get("input") or {}
                        if not isinstance(payload, dict):
                            continue
                        for key in PATH_KEYS:
                            value = payload.get(key)
                            if isinstance(value, str) and value:
                                current["files"].append(os.path.basename(value))
                        if name == "Bash":
                            label = payload.get("description") or " ".join(
                                str(payload.get("command", "")).split()[:3]
                            )
                            if label:
                                current["commands"].append(label)
                        elif name in ("Agent", "Task"):
                            label = payload.get("description")
                            if label:
                                current["commands"].append(f"agent: {label}")
    return turns, meta


def read_session(path):
    """Strict parse, falling back to permissive if the transcript predates promptSource."""
    turns, meta = parse_session(path, strict=True)
    if turns:
        return turns, meta, False
    loose, loose_meta = parse_session(path, strict=False)
    if loose:
        return loose, loose_meta, True
    return turns, meta, False


def detail_lines(turn):
    lines = []
    tools = ", ".join(
        f"{name}x{count}" if count > 1 else name
        for name, count in turn["tools"].most_common()
    )
    if tools:
        lines.append(tools)
    files = list(dict.fromkeys(turn["files"]))
    if files:
        shown = ", ".join(files[:4])
        if len(files) > 4:
            shown += f" +{len(files) - 4} more"
        lines.append("files: " + shown)
    commands = list(dict.fromkeys(turn["commands"]))[:3]
    if commands:
        lines.append("ran: " + redact("; ".join(commands)))
    if not tools and turn["reply"]:
        lines.append(redact(re.sub(r"\s+", " ", turn["reply"]))[:90] + "...")
    return lines


def render(turns, meta, path, full=False, width=100, loose=False):
    session_id = os.path.basename(path).removesuffix(".jsonl")
    header = f"session {session_id[:8]}  .  {meta.get('cwd') or '?'}"
    if meta.get("branch"):
        header += f"  .  {meta['branch']}"
    print(header)
    if turns:
        start, end = parse_ts(turns[0]["ts"]), parse_ts(turns[-1]["end"])
        span = start.strftime("%Y-%m-%d %H:%M") if start else "?"
        if end:
            span += " -> " + end.strftime("%H:%M")
        plural = "s" if len(turns) != 1 else ""
        print(f"{span}  .  {len(turns)} question{plural}")
    if loose:
        print("(no promptSource in this transcript - skill payloads may appear)")
    print()

    if not turns:
        print("  (no user questions found)")
        return

    for index, turn in enumerate(turns, 1):
        start, end = parse_ts(turn["ts"]), parse_ts(turn["end"])
        clock = start.strftime("%H:%M") if start else "--:--"
        duration = ""
        if start and end:
            minutes = int((end - start).total_seconds() // 60)
            duration = f"{minutes}m" if minutes else "<1m"
        question = turn["question"]
        if not full and len(question) > width:
            question = question[: width - 1] + "..."
        print(f"#{index:<3} {clock} {duration:>4}  {question}")
        lines = detail_lines(turn)
        for position, line in enumerate(lines):
            connector = "`-" if position == len(lines) - 1 else "|-"
            print(f"          {connector} {line}")
        print()


def load_history(session_id=None, project=None):
    if not os.path.exists(HISTORY):
        return []
    rows = []
    with open(HISTORY, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if session_id and not str(row.get("sessionId", "")).startswith(session_id):
                continue
            if project and row.get("project") != project:
                continue
            rows.append(row)
    return rows


def render_history(rows, session_id, full=False, width=100):
    print(f"session {session_id[:8]}  .  transcript expired (30-day cleanup)")
    print(f"recovered {len(rows)} question(s) from history.jsonl - questions only,")
    print("Claude's actions for this session are gone.")
    print()
    for index, row in enumerate(rows, 1):
        stamp = row.get("timestamp")
        when = "?"
        if isinstance(stamp, (int, float)):
            when = dt.datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d %H:%M")
        question = clean_prompt(row.get("display", ""))
        if not full and len(question) > width:
            question = question[: width - 1] + "..."
        print(f"#{index:<3} {when}  {question}")


def session_files(directory):
    if not os.path.isdir(directory):
        return []
    paths = [
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.endswith(".jsonl")
    ]
    return sorted(paths, key=os.path.getmtime, reverse=True)


def find_session(session_id):
    if os.path.exists(session_id):
        return session_id
    if not os.path.isdir(PROJECTS):
        return None
    for root, _dirs, names in os.walk(PROJECTS):
        for name in names:
            if name.endswith(".jsonl") and name.startswith(session_id):
                return os.path.join(root, name)
    return None


def do_list(directory):
    paths = session_files(directory)
    if not paths:
        print(f"no sessions found in {directory}")
        return
    print(f"{len(paths)} session(s) in {directory}\n")
    for path in paths:
        turns, _meta, _loose = read_session(path)
        when = dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        first = turns[0]["question"][:60] + "..." if turns else "(no questions)"
        age = (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(path))).days
        expires = max(0, 30 - age)
        print(
            f"  {os.path.basename(path)[:8]}  {when}  {len(turns):>3}q  "
            f"{expires:>2}d left  {first}"
        )


def main():
    parser = argparse.ArgumentParser(description="Recap a Claude Code session.")
    parser.add_argument("--session", help="session id (or prefix) or path to a .jsonl")
    parser.add_argument("--project", default=os.getcwd(), help="project cwd")
    parser.add_argument("--list", action="store_true", help="list sessions")
    parser.add_argument("--full", action="store_true", help="do not truncate questions")
    parser.add_argument("--width", type=int, default=100)
    parser.add_argument("--prose", action="store_true", help="emit JSON for the model")
    args = parser.parse_args()

    directory = project_dir(args.project)

    if args.list:
        do_list(directory)
        return 0

    if args.session:
        path = find_session(args.session)
        if not path:
            rows = load_history(session_id=args.session)
            if rows:
                render_history(rows, args.session, args.full, args.width)
                return 0
            print(f"no transcript or history found for '{args.session}'", file=sys.stderr)
            return 1
    else:
        paths = session_files(directory)
        if not paths:
            print(f"no sessions found in {directory}", file=sys.stderr)
            return 1
        path = paths[0]

    turns, meta, loose = read_session(path)

    if args.prose:
        payload = [
            {
                "n": index,
                "ts": turn["ts"],
                "question": turn["question"],
                "tools": dict(turn["tools"]),
                "files": list(dict.fromkeys(turn["files"])),
                "commands": list(dict.fromkeys(turn["commands"])),
                "reply": turn["reply"][:400],
            }
            for index, turn in enumerate(turns, 1)
        ]
        json.dump({"session": os.path.basename(path), "turns": payload}, sys.stdout, indent=1)
        print()
        return 0

    render(turns, meta, path, args.full, args.width, loose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
