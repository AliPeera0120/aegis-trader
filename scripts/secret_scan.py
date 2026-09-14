"""Repository-local scan. Heuristic supplement to CI gitleaks; never prints matching contents."""

from pathlib import Path
import re
import sys
import subprocess

root = Path(__file__).resolve().parents[1]
patterns = [
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(
        r'(?i)(?:ALPACA_(?:PAPER|LIVE|DATA)_(?:KEY|SECRET))[ \t]*=[ \t]*["\']?(?!\$|$)[A-Za-z0-9_-]{18,}'
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
excluded = {".git", "var", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "htmlcov", "build", "dist"}
violations = []
listed = subprocess.run(
    ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    cwd=root,
    capture_output=True,
    check=False,
)
# Scan everything that could be committed. Private ignored .env/var are runtime inputs;
# accidentally tracked secrets are still included by --cached even when ignored.
paths = (
    [root / name for name in listed.stdout.decode().split("\0") if name]
    if listed.returncode == 0
    else root.rglob("*")
)
for path in paths:
    if not path.is_file() or any(
        p in excluded or p.endswith(".egg-info") for p in path.relative_to(root).parts
    ):
        continue
    if path.name == "secret_scan.py" or path.suffix in {".png", ".zip", ".pyc", ".db"}:
        continue
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        continue
    if any(pattern.search(text) for pattern in patterns):
        violations.append(str(path.relative_to(root)))
if violations:
    print("Potential secrets detected in: " + ", ".join(violations))
    sys.exit(1)
print("Secret scan passed: no matching credential patterns.")
