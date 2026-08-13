#!/usr/bin/env python3
"""Blocks commits/pushes that add sensitive filenames or secret-shaped content.

Reads a unified diff on stdin (e.g. output of `git diff --cached` or
`git diff <range>`) and exits non-zero with a report if anything looks like
a credential. This is a defense-in-depth check on top of .gitignore — it
also catches force-added (`git add -f`) or newly-introduced sensitive files
that .gitignore doesn't yet know about.
"""

from __future__ import annotations

import re
import sys

EXCLUDE_SUFFIXES = (".example", ".sample", ".template", ".dist")

FILENAME_PATTERNS = [
    re.compile(r"(^|/)[^/]*\.env(\..+)?$"),
    re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$", re.IGNORECASE),
    re.compile(r"(^|/)id_(rsa|ed25519|ecdsa|dsa)(\.\w+)?$"),
    re.compile(r"(^|/)credentials(\.\w+)?$", re.IGNORECASE),
    re.compile(r"(^|/)\.htpasswd$"),
    re.compile(r"(^|/)terraform\.tfvars$"),
    re.compile(r"\.tfstate(\.backup)?$"),
    re.compile(r"(^|/)\.aws/credentials$"),
    re.compile(r"(^|/)inventory\.ini$"),
]

PLACEHOLDER_MARKERS = (
    "your",
    "changeme",
    "change_me",
    "example",
    "xxx",
    "placeholder",
    "dummy",
    "sample",
    "todo",
    "replace_me",
    "insert_",
    "fill_in",
    "<",
)

# These always indicate a real secret if matched — no placeholder exemption.
HARD_CONTENT_PATTERNS = [
    (
        "private key block",
        re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"),
    ),
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "JWT-looking token",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
    ),
]

# These are checked against PLACEHOLDER_MARKERS before flagging, since
# .env.example and docs legitimately contain lines like
# HW_ACCESS_KEY=<your-hw-access-key> or DEMO_PASSWORD=YOUR_DEMO_PASSWORD.
SOFT_CONTENT_PATTERNS = [
    (
        "credential embedded in URL",
        re.compile(r"\b\w{2,10}://[^\s\"'/@]+:([^\s\"'@]+)@[^\s\"'/]+"),
    ),
    (
        "hardcoded secret assignment",
        re.compile(
            # No leading \b: real-world names are usually prefixed, e.g.
            # HW_SECRET_KEY / AWS_SECRET_ACCESS_KEY — "_" doesn't count as a
            # word boundary, so a strict \b here would miss exactly those.
            r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?key|secret|password|passwd|token)"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9/_\-+=]{12,})[\"']?"
        ),
    ),
]


ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def looks_like_placeholder(value: str) -> bool:
    v = value.strip().strip("\"'")
    if not v:
        return True
    # Screaming-snake-case identifiers (e.g. `api_key=MAAS_API_KEY`) are almost
    # always a reference to an env var name in docs/code, not an actual secret.
    if ENV_VAR_NAME_RE.match(v):
        return True
    return any(marker in v.lower() for marker in PLACEHOLDER_MARKERS)


def looks_like_code_expression(content: str, match: re.Match) -> bool:
    """True if the matched value is immediately followed by `.` or `(`, e.g.
    `token = localStorage.getItem(...)` or `secret = os.environ.get(...)`.
    Real hardcoded secrets are string literals, not property/method access —
    this avoids flagging bare identifiers like `localStorage`/`sessionStorage`
    that happen to be 12+ alnum characters."""
    end = match.end(match.lastindex)
    return content[end : end + 1] in (".", "(")


def is_sensitive_filename(path: str) -> bool:
    if path in ("/dev/null", ""):
        return False
    if path.endswith(EXCLUDE_SUFFIXES):
        return False
    return any(p.search(path) for p in FILENAME_PATTERNS)


def scan(diff_text: str) -> list[tuple[str, str | None, str]]:
    findings: list[tuple[str, str | None, str]] = []
    current_file = None

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current_file = path
            if is_sensitive_filename(current_file):
                findings.append((current_file, None, "sensitive filename"))
            continue

        if not line.startswith("+"):
            continue

        content = line[1:]

        for label, pat in HARD_CONTENT_PATTERNS:
            if pat.search(content):
                findings.append((current_file or "?", content.strip()[:80], label))

        for label, pat in SOFT_CONTENT_PATTERNS:
            m = pat.search(content)
            if (
                m
                and not looks_like_placeholder(m.group(m.lastindex))
                and not looks_like_code_expression(content, m)
            ):
                findings.append((current_file or "?", content.strip()[:80], label))

    return findings


def main() -> int:
    diff_text = sys.stdin.read()
    findings = scan(diff_text)
    if not findings:
        return 0

    print(
        "\n\033[1;31mBLOCKED: possible credentials/sensitive files detected\033[0m\n",
        file=sys.stderr,
    )
    for path, snippet, label in findings:
        if snippet:
            print(f"  [{label}] {path}: {snippet}", file=sys.stderr)
        else:
            print(f"  [{label}] {path}", file=sys.stderr)
    print(
        "\nIf this is a false positive, adjust the patterns in "
        ".githooks/check_secrets.py, or bypass once with `git commit/push "
        "--no-verify` (only if you're certain nothing sensitive is included).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
