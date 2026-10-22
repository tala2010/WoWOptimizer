"""Reconstruct the GPL R11 research delta from an exact local upstream copy.

Writes only a NEW output directory, never an installed game directory.
User aura source and SavedVariables are intentionally neither read nor shipped.
"""
import argparse
import hashlib
import json
from pathlib import Path


def rebuild(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or source == output or source in output.parents or output in source.parents:
        raise ValueError('use a new output directory separate from source')
    data = json.loads(Path(__file__).with_name('patches.json').read_text(encoding='utf-8'))
    prepared = {}
    for row in data['files']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid patch path')
        path = source / relative
        if not path.resolve().is_relative_to(source) or path.is_symlink():
            raise ValueError('linked input')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['before_sha256']:
            raise ValueError(f"unsupported upstream file: {row['path']}")
        lines = raw.decode('utf-8-sig').replace('\r\n', '\n').splitlines(keepends=True)
        for start, end, replacement in reversed(row['ops']):
            lines[start:end] = replacement
        result = ''.join(lines).encode('utf-8')
        if hashlib.sha256(result).hexdigest() != row['after_sha256']:
            raise ValueError('reconstructed output mismatch')
        prepared[relative] = result
    output.mkdir(parents=True)
    for relative, raw in prepared.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return len(prepared)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='directory containing an original WeakAuras folder')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'Rebuilt {rebuild(args.source, args.output)} research files; NOT installed or runtime-validated.')
