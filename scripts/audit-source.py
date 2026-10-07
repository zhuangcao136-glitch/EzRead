"""Check publishable working-tree files without printing private contents.

Includes tracked and unignored new files, so edits are checked before staging.
This is a small accidental-disclosure check, not a Git-history security audit.
"""

from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORIES = {
    "data", "work", "output", ".deps", ".venv", "venv", "node_modules",
    ".codex", ".aws", "browser-profile", "library", "__pycache__",
}
PATTERNS = {
    "personal absolute path": re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\s\\/<>]+[\\/]"),
    "GitHub token": re.compile(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,}"),
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def source_files():
    if not shutil.which("git") or not (ROOT / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, check=True,
    )
    return sorted(set(result.stdout.decode("utf-8").strip("\0").split("\0")) - {""})


def inspect_file(name):
    path = ROOT / name
    if not path.is_file():
        return []  # Tracked deletions are not part of the delivered source.
    parts = Path(name).parts
    lower = path.name.lower()
    problems = []
    if (parts[0] in PRIVATE_DIRECTORIES or "__pycache__" in parts
            or parts[:2] == ("desktop", "bin")
            or lower == "agents.local.md"
            or (lower.startswith(".env") and lower != ".env.example")
            or path.suffix.lower() in {".pdf", ".db", ".sqlite3", ".zip", ".bak", ".key", ".pem", ".p12", ".pfx"}):
        if name != "assets/dictionaries/ecdict.sqlite3":
            problems.append("private data or generated artifact")
    raw = path.read_bytes()
    if b"\0" not in raw:
        content = raw.decode("utf-8", errors="replace")
        problems.extend(label for label, pattern in PATTERNS.items() if pattern.search(content))
    return problems


def main():
    files = source_files()
    if files is None:
        print("Source audit skipped: Git checkout metadata is unavailable.")
        return
    failures = {name: problems for name in files if (problems := inspect_file(name))}
    for name, problems in failures.items():
        print(f"{name}: {', '.join(problems)}")
    if failures:
        raise SystemExit("Source audit failed; inspect the listed files before publishing.")
    print(f"Source audit passed ({len(files)} candidate paths; file contents only, not Git history).")


if __name__ == "__main__":
    main()
