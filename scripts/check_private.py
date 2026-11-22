"""Privacy scanner for a public repository (pre-commit hook and CI).

Usage:
    python scripts/check_private.py [--staged | --history | --message-file PATH]
                                    [--builtin-only] [--extra-patterns PATH] [paths...]

Exit code 0 = clean, 1 = something found (printed as ``file:line: description``).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SELF_REL = "scripts/check_private.py"
DEFAULT_EXTRA = REPO.parent / ".private-patterns.txt"

# Paths (repo-relative, forward slashes) that may be binary. Empty on start.
ALLOWED_BINARY: set[str] = set()

ALLOWED_AUTHOR_EMAIL_SUFFIX = "@users.noreply.github.com"
ALLOWED_AUTHOR_NAME = "drzejb"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_EMAIL_OK = re.compile(r"(?:@users\.noreply\.github\.com|^noreply@github\.com)$", re.I)

BUILTIN: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(?:10|127|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\b"),
        "private IP address",
    ),
    (
        re.compile(r"\b(?:lat(?:itude)?|lon(?:gitude)?|lng)\b\s*[:=]\s*-?\d{1,3}\.\d{3,}", re.I),
        "coordinates",
    ),
    (re.compile(r"\b590\d{15}\b"), "PPE-like number"),
]


def scan_text(text: str, extra: list[re.Pattern[str]], builtin: bool = True) -> list[tuple[int, str]]:
    """Return (line number, description) for every finding in text."""
    found: list[tuple[int, str]] = []
    for no, line in enumerate(text.splitlines(), 1):
        if builtin:
            for rx, desc in BUILTIN:
                if rx.search(line):
                    found.append((no, desc))
            for m in _EMAIL.finditer(line):
                if not _EMAIL_OK.search(m.group(0)):
                    found.append((no, "non-noreply e-mail address"))
        for rx in extra:
            if rx.search(line):
                found.append((no, "owner pattern"))
    return found


def load_extra(path: Path) -> list[re.Pattern[str]] | None:
    if not path.is_file():
        return None
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#"):
            out.append(re.compile(raw, re.I))
    return out


SCISSORS = "# ------------------------ >8 ------------------------"


def clean_message(text: str) -> str:
    """Message as git would store it: cut at the scissors line (git commit -v), drop # comments."""
    lines: list[str] = []
    for ln in text.splitlines():
        if ln.startswith(SCISSORS):
            break
        if not ln.startswith("#"):
            lines.append(ln)
    return "\n".join(lines)


def is_added_line(line: str, prev: str) -> bool:
    """True for an added content line of a diff; the '+++ ' file header (after '--- ') is not content."""
    if not line.startswith("+"):
        return False
    return not (line.startswith("+++ ") and prev.startswith("--- "))


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout


def _report(label: str, findings: list[tuple[int, str]]) -> int:
    for no, desc in findings:
        print(f"{label}:{no}: {desc}")
    return len(findings)


def _scan_blob(rel: str, data: bytes, extra: list[re.Pattern[str]], builtin_only: bool) -> int:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if rel in ALLOWED_BINARY:
            return 0
        print(f"{rel}:0: binary or non-UTF-8 file not on ALLOWED_BINARY")
        return 1
    # the scanner itself contains the builtin patterns as regexes
    return _report(rel, scan_text(text, extra, builtin=(rel != SELF_REL)))


def _scan_history(extra: list[re.Pattern[str]]) -> int:
    bad = 0
    raw = _git("log", "--all", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e").decode("utf-8", "replace")
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, an, ae, cn, ce, body = (rec.split("\x1f") + [""] * 6)[:6]
        if an != ALLOWED_AUTHOR_NAME or not ae.endswith(ALLOWED_AUTHOR_EMAIL_SUFFIX):
            print(f"commit {sha[:8]}:0: author is not the allowed noreply identity")
            bad += 1
        if not ((cn == ALLOWED_AUTHOR_NAME and ce.endswith(ALLOWED_AUTHOR_EMAIL_SUFFIX))
                or (cn, ce) == ("GitHub", "noreply@github.com")):
            print(f"commit {sha[:8]}:0: committer is not allowed")
            bad += 1
        bad += _report(f"commit {sha[:8]} message", scan_text(body, extra))
    diff = _git("log", "-p", "--all", "--format=").decode("utf-8", "replace")
    current = ""
    prev = ""
    for no, line in enumerate(diff.splitlines(), 1):
        if line.startswith("diff --git "):
            current = line.rsplit(" b/", 1)[-1]
        elif line.startswith("Binary files ") and current not in ALLOWED_BINARY:
            print(f"history diff {current}:{no}: binary file")
            bad += 1
        elif is_added_line(line, prev):
            bad += _report(f"history diff {current}", [(no, d) for _, d in scan_text(
                line[1:], extra, builtin=(current != SELF_REL))])
        prev = line
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--history", action="store_true")
    mode.add_argument("--message-file")
    ap.add_argument("--builtin-only", action="store_true")
    ap.add_argument("--extra-patterns", default=str(DEFAULT_EXTRA))
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)

    extra: list[re.Pattern[str]] = []
    if not args.builtin_only:
        loaded = load_extra(Path(args.extra_patterns))
        if loaded is None:
            print(f"ERROR: owner patterns file not found: {args.extra_patterns}")
            return 1
        extra = loaded

    bad = 0
    if args.message_file:
        text = Path(args.message_file).read_text(encoding="utf-8", errors="replace")
        text = clean_message(text)
        bad = _report("commit message", scan_text(text, extra))
    elif args.history:
        bad = _scan_history(extra)
    elif args.staged:
        names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8")
        for rel in filter(None, names.split("\0")):
            bad += _scan_blob(rel, _git("show", f":{rel}"), extra, args.builtin_only)
    else:
        if args.paths:
            rels = args.paths
        else:
            rels = [p for p in _git("ls-files", "-z").decode("utf-8").split("\0") if p]
        for rel in rels:
            p = REPO / rel
            if p.is_file():
                bad += _scan_blob(Path(rel).as_posix(), p.read_bytes(), extra, args.builtin_only)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
