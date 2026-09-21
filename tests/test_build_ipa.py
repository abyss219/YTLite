"""Regression checks for accepting inputs and retaining the intended tweak."""
import argparse
from pathlib import Path
import plistlib
import shutil
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_ipa as build


def executable(encrypted=False, cpu=0x100000C):
    command = struct.pack('<6I', 0x2C, 24, 0, 0, int(encrypted), 0)
    return struct.pack('<8I', 0xFEEDFACF, cpu, 0, 2, 1, len(command), 0, 0) + command


class BuildChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def ipa(self, binary=None, extra=()):
        path = self.root / 'input.ipa'
        info = {'CFBundleExecutable': 'YouTube', 'CFBundleIdentifier': 'com.google.ios.youtube'}
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Payload/YouTube.app/Info.plist', plistlib.dumps(info))
            archive.writestr('Payload/YouTube.app/YouTube', binary or executable())
            for name, data in extra:
                archive.writestr(name, data)
        return path

    def test_accepts_decrypted_arm64(self):
        root, info, _ = build.inspect_ipa(self.ipa(), clean=True)
        self.assertEqual(root, 'Payload/YouTube.app')
        self.assertEqual(info['CFBundleIdentifier'], 'com.google.ios.youtube')

    def test_rejects_encrypted_input(self):
        with self.assertRaisesRegex(ValueError, 'encrypted'):
            build.inspect_ipa(self.ipa(executable(True)), clean=True)

    def test_rejects_wrong_architecture(self):
        with self.assertRaisesRegex(ValueError, 'ARM64'):
            build.inspect_ipa(self.ipa(executable(cpu=7)), clean=True)

    def test_universal_arm64_slice(self):
        thin = executable()
        fat = struct.pack('>2I', 0xCAFEBABE, 1) + struct.pack('>5I', 0x100000C, 0, 28, len(thin), 0) + thin
        self.assertEqual(build.macho_image(fat)[0], thin)

    def test_rejects_existing_tweak(self):
        path = self.ipa(extra=[('Payload/YouTube.app/Frameworks/YTLite.dylib', b'existing')])
        with self.assertRaisesRegex(ValueError, 'already contains'):
            build.inspect_ipa(path, clean=True)

    def test_rejects_path_traversal(self):
        path = self.ipa(extra=[('../escape', b'invalid')])
        with self.assertRaisesRegex(ValueError, 'Unsafe path'):
            build.inspect_ipa(path)

    def test_rejects_symlink(self):
        link = zipfile.ZipInfo('Payload/YouTube.app/link')
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        path = self.ipa(extra=[(link, b'../../outside')])
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build.inspect_ipa(path)

    def test_rejects_duplicate_archive_member(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            path = self.ipa(extra=[('Payload/YouTube.app/YouTube', executable())])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            build.inspect_ipa(path)

    def test_rejects_wrong_deb_hash_before_parsing(self):
        path = self.root / 'wrong.deb'
        path.write_bytes(b'wrong package')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            build.verify_deb(path, '0' * 64)

    def test_duplicate_deb_is_injected_once(self):
        path = self.root / 'bundled.deb'
        path.write_bytes(b'validated elsewhere')
        extras = self.root / 'extras'
        extras.mkdir()
        shutil.copy2(path, extras / 'ytplus.deb')
        self.assertEqual(build.injection_files(path, extras), [path.resolve()])

    def test_rejects_conflicting_tweak(self):
        path = self.root / 'bundled.deb'
        path.write_bytes(b'primary')
        extras = self.root / 'extras'
        extras.mkdir()
        (extras / 'other.deb').write_bytes(b'conflicting')
        with patch.object(build, 'deb_files', return_value={'Library/YTLite.dylib': b'other'}):
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                build.injection_files(path, extras)

    def test_swift_runtime_resolves_through_system_runpath(self):
        self.assertIsNone(build.resolve_dependency('@rpath/libswiftCore.dylib',
                          'Payload/YouTube.app/Frameworks/Hook', 'Payload/YouTube.app',
                          ['/usr/lib/swift'], set()))

    def test_missing_hook_dependency_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'Missing or unresolved'):
            build.resolve_dependency('@rpath/CydiaSubstrate.framework/CydiaSubstrate',
                                     'Payload/YouTube.app/Frameworks/YTLite.dylib',
                                     'Payload/YouTube.app', ['/usr/lib/swift'], set())

    def test_swift_requires_a_system_search_path(self):
        with self.assertRaisesRegex(ValueError, 'Missing or unresolved'):
            build.resolve_dependency('@rpath/libswiftCore.dylib',
                                     'Payload/YouTube.app/Frameworks/Hook',
                                     'Payload/YouTube.app', [], set())

    def test_failed_output_validation_does_not_publish(self):
        args = argparse.Namespace(bundle_id='com.google.ios.youtube', name='YouTube Plus',
                                  output=self.root / 'output.ipa', deb=self.root / 'package.deb',
                                  sha256='0' * 64, tweaks_dir=None, input=self.ipa(), ipa_url=None,
                                  cyan='cyan')
        with patch.object(build, 'verify_deb', return_value={}), \
             patch.object(build, 'injection_files', return_value=[]), \
             patch.object(build.subprocess, 'run'), \
             patch.object(build, 'verify_output', side_effect=ValueError('damaged code')):
            with self.assertRaisesRegex(ValueError, 'damaged code'):
                build.build(args)
        self.assertFalse(args.output.exists())


if __name__ == '__main__':
    unittest.main()
