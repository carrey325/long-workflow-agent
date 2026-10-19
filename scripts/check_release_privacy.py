"""Inspect every staged blob before publication; diagnostics never echo matched secrets."""
import argparse
import json
import re
import subprocess
from pathlib import Path

RULES = {
    'conversation-id': re.compile(
        r'(?:chatgpt\.com/c/|codex://threads/)[0-9a-f-]{20,}|'
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.IGNORECASE),
    'credential': re.compile(
        r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
        r'sk-(?:proj-)?[A-Za-z0-9_-]{24,})\b'),
    'personal-path': re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+|/(?:Users)/[^/\s]+'),
}
PRIVATE_PARTS = {'.runs', '.venv', '.env', '.codex', 'skill-backups', '__pycache__'}
PRIVATE_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.png', '.jpg', '.jpeg', '.webp'}


def git_bytes(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True).stdout


def scan_staged(repo, extra_denied=()):
    hits = []
    names = git_bytes(repo, 'ls-files', '--cached', '-z').decode('utf-8').split('\0')
    for name in filter(None, names):
        path = Path(name)
        if (PRIVATE_PARTS.intersection(path.parts) or path.suffix.lower() in PRIVATE_SUFFIXES
                or path.name == 'runtime-location.txt' or '.sqlite' in path.name):
            hits.append({'path': name, 'rule': 'private-runtime-path'})
        raw = git_bytes(repo, 'cat-file', 'blob', ':' + name)
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            hits.append({'path': name, 'rule': 'binary-content'})
            continue
        for rule, pattern in RULES.items():
            if pattern.search(content):
                hits.append({'path': name, 'rule': rule})
        if any(literal and literal.casefold() in content.casefold() for literal in extra_denied):
            hits.append({'path': name, 'rule': 'explicit-private-literal'})
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('--deny-file', type=Path,
                        help='Local-only UTF-8 file with one additional private literal per line')
    args = parser.parse_args()
    denied = args.deny_file.read_text('utf-8-sig').splitlines() if args.deny_file else []
    hits = scan_staged(args.repository, denied)
    print(json.dumps({'passed': not hits, 'findings': hits}, ensure_ascii=False, indent=2))
    return 1 if hits else 0


if __name__ == '__main__':
    raise SystemExit(main())
