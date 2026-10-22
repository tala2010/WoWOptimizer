"""Conservative public-export gate. Reports file/line/category, never secret values."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
IGNORE = {'.git', '__pycache__', '.venv', 'build', 'dist'}
PRIVATE_NAMES = {'license_manager.py', 'license_server_local.py', 'reseller_client_app.py',
                 'cloud_release_manager.py', 'runtime_grant_trust.py', '.env'}
PATTERNS = {
    'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b',
    'cloud-key': r'\bAKIA[A-Z0-9]{16}\b',
    'account-or-contact': r'\b\d{6,12}#\d{1,4}\b|(?:微信|QQ)\s*[:：]\s*[A-Za-z0-9]{5,}',
    'developer-path': r'(?i)[a-z]:[\\/]+Users[\\/]+(?!Public\b|Default\b)[A-Za-z0-9_]{2,}',
    'embedded-secret': r'(?i)(?:api_key|password|admin_token|secret_key)\s*=\s*["\'][^"\'\s]{12,}["\']',
}


def audit(root=ROOT):
    findings, count = [], 0
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if set(rel.parts) & IGNORE:
            continue
        if path.is_symlink() or path.is_junction():
            findings.append({'file': rel.as_posix(), 'category': 'linked-path'})
            continue
        if not path.is_file():
            continue
        count += 1
        if path.name in PRIVATE_NAMES or path.suffix.lower() in {'.pem', '.key', '.seed', '.sqlite', '.db', '.bak', '.exe', '.dll'}:
            findings.append({'file': rel.as_posix(), 'category': 'private-or-binary-file'})
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            findings.append({'file': rel.as_posix(), 'category': 'unreviewed-binary'})
            continue
        # Scanner signatures themselves are intentionally not literal evidence.
        if path.resolve() == Path(__file__).resolve():
            continue
        for category, pattern in PATTERNS.items():
            for match in re.finditer(pattern, text):
                findings.append({'file': rel.as_posix(), 'line': text.count('\n', 0, match.start()) + 1, 'category': category})
        if path.suffix == '.py':
            try:
                ast.parse(text, filename=rel.as_posix())
            except SyntaxError as exc:
                findings.append({'file': rel.as_posix(), 'line': exc.lineno, 'category': 'python-syntax'})
    payload = root / 'assets/public-payload-hashes.json'
    if payload.is_file():
        for name, digest in json.loads(payload.read_text(encoding='utf-8')).items():
            path = root / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                findings.append({'file': name, 'category': 'payload-integrity'})
    return {'passed': not findings, 'files_checked': count, 'findings': findings,
            'limitation': 'Pattern scan complements the explicit allowlist and manual provenance review; it is not proof of all possible secrets.'}


if __name__ == '__main__':
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
