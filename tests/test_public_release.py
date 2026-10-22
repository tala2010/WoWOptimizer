import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from free_access import FreeAccess
from local_release import prepare_local_release
from tools import apply_approved_hub_release as installer
from tools.verify_public_tree import audit


class PublicReleaseTests(unittest.TestCase):
    def test_access_and_local_preparation_without_network(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden')):
            status = FreeAccess().ensure_runtime_authorization()
            self.assertTrue(status.active)
            self.assertFalse(status.is_trial)
            target = type('Target', (), {})()
            self.assertEqual(prepare_local_release(target), installer.RELEASE_ID)
            self.assertTrue(target.runtime_release_info['free_mode'])

    def test_unknown_addon_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            addons = base / 'AddOns'
            wa = addons / 'WeakAuras'
            wa.mkdir(parents=True)
            original = b'-- unknown plugin version\n'
            target = wa / 'GenericTrigger.lua'
            target.write_bytes(original)
            result = installer.apply_release(addons, base / 'backup')
            self.assertEqual(result['changed'], 0)
            self.assertEqual(target.read_bytes(), original)

    def test_corrupt_payload_fails_before_optimizer_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'assets').mkdir()
            (root / 'assets/public-payload-hashes.json').write_text(
                json.dumps({'assets/missing.lua': '0' * 64}), encoding='utf-8')
            target = type('Target', (), {})()
            with patch('local_release.package_root', return_value=root):
                with self.assertRaises(ValueError):
                    prepare_local_release(target)
            self.assertFalse(hasattr(target, 'runtime_bundle_root'))

    def test_runtime_config_requires_no_grant_or_device_identity(self):
        from optimizer_core import WoWOptimizer
        target = WoWOptimizer.__new__(WoWOptimizer)
        with tempfile.TemporaryDirectory() as folder:
            target._write_hub_runtime_authorization(folder)
            content = (Path(folder) / 'runtime_config.lua').read_text(encoding='utf-8')
            self.assertIn('freeMode = true', content)
            self.assertNotIn('licenseTag', content)
            self.assertNotIn('expires', content)

    def test_lua_free_gate_and_compilation(self):
        import re
        from lupa.lua51 import LuaRuntime
        root = Path(__file__).resolve().parents[1]
        lua = LuaRuntime()
        for path in (root / 'assets').rglob('*.lua'):
            lua.compile(path.read_text(encoding='utf-8'), name='@' + path.relative_to(root).as_posix())
        source = (root / 'assets/RaidOptimizerHub/RaidOptimizerHub.lua').read_text(encoding='utf-8')
        gate = re.search(r'local function ResolveRuntimeMode\(\).*?\nend', source, re.S).group()
        self.assertEqual(lua.execute(gate + '\nreturn ResolveRuntimeMode()'), 'native')
        lua.execute('RaidOptimizerHubRuntimeConfig = { mode = "central", freeMode = true }')
        self.assertEqual(lua.execute(gate + '\nreturn ResolveRuntimeMode()'), 'central')

    def test_transaction_backup_and_restore(self):
        import hashlib
        before, after = b'local value = 1\n', b'local value = 2\n'
        profile = {'schema': 2, 'release_id': installer.RELEASE_ID, 'components': {
            'weakauras': {'addon': 'WeakAuras', 'files': [{
                'path': 'Test.lua', 'clean_sha256': hashlib.sha256(before).hexdigest(),
                'approved_sha256': hashlib.sha256(after).hexdigest(),
                'newline_mode': 'lf', 'trailing_newline': True, 'ops': [[0, 1, ['local value = 2']]],
            }]}, 'plater': {'addon': 'Plater', 'files': []}, 'ndui': {'addon': 'NDui', 'files': []}}}
        old = installer._PROFILE
        try:
            installer.configure_release_profile(profile)
            with tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                target = base / 'AddOns/WeakAuras/Test.lua'
                target.parent.mkdir(parents=True)
                target.write_bytes(before)
                result = installer.apply_release(base / 'AddOns', base / 'backup')
                self.assertTrue(result['ok'], result)
                self.assertEqual(target.read_bytes(), after)
                again = installer.apply_release(base / 'AddOns', base / 'backup')
                self.assertEqual(again['changed'], 0)
                restored = installer.restore_release(Path(result['ledger_path']))
                self.assertTrue(restored['ok'], restored)
                self.assertEqual(target.read_bytes(), before)
        finally:
            installer.configure_release_profile(old)

    def test_public_tree(self):
        result = audit()
        self.assertTrue(result['passed'], result['findings'])


if __name__ == '__main__':
    unittest.main()
