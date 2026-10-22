"""Load the shipped local payload. No downloads, server credentials or activation."""
import hashlib
import json
import sys
from pathlib import Path


def package_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def prepare_local_release(optimizer):
    root = package_root()
    manifest = json.loads((root / 'assets/public-payload-hashes.json').read_text(encoding='utf-8'))
    for relative, digest in manifest.items():
        path = root / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('本地载荷路径不合法')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'本地文件不完整：{relative}，请重新获取公开版本')
    from tools.apply_approved_hub_release import configure_release_profile
    profile = json.loads((root / 'assets/approved_hub_release_profile.json').read_text(encoding='utf-8'))
    release_id = configure_release_profile(profile)
    optimizer.runtime_bundle_root = str(root)
    optimizer.runtime_release_info = {
        'release_id': release_id,
        'component_versions': {'RaidOptimizerHub': '0.5.49'},
        'policy': {'schema_version': 1, 'details_mode': 'native-safe', 'fallback': 'native'},
        'free_mode': True,
    }
    return release_id
