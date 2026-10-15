import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from build_zimaos import (INPUT_FILES, config_changes, config_values, extract_inputs,
                          symbol_inventory, valid_hash, valid_version, verify_symbols)


class BuildValidationTests(unittest.TestCase):
    def test_version_and_hash_reject_shell_content(self):
        self.assertEqual(valid_version('6.18.9'), '6.18.9')
        self.assertEqual(valid_hash('A' * 64), 'a' * 64)
        for bad in ('6.18.9;echo bad', '../6.18.9', '6.18.9\n', '6.18.9-zima'):
            with self.assertRaises(ValueError):
                valid_version(bad)
        with self.assertRaises(ValueError):
            valid_hash('abc')

    def test_config_changes_keep_abi_options_strict(self):
        before = config_values('CONFIG_GCC_VERSION=140300\n# CONFIG_MODVERSIONS is not set\nCONFIG_PREEMPT=y\n')
        after = config_values('CONFIG_GCC_VERSION=140200\nCONFIG_MODVERSIONS=y\n# CONFIG_PREEMPT is not set\n')
        self.assertEqual(config_changes(before, after), {
            'CONFIG_MODVERSIONS': ['n', 'y'], 'CONFIG_PREEMPT': ['y', 'n']})

    def test_symbol_audit_rejects_missing_and_modular_dependencies(self):
        exports = symbol_inventory('ec_read builtin\nbattery_hook_register [battery]\n')
        verify_symbols(['ec_read'], exports, False)
        verify_symbols(['battery_hook_register'], exports, True)
        with self.assertRaises(ValueError):
            verify_symbols(['unknown'], exports, True)
        with self.assertRaises(ValueError):
            verify_symbols(['battery_hook_register'], exports, False)
        with self.assertRaises(ValueError):
            symbol_inventory('ec_read builtin\nec_read [other]\n')

    def archive(self, folder, names):
        path = folder / 'input.tar.gz'
        with tarfile.open(path, 'w:gz') as tar:
            for name, kind in names:
                info = tarfile.TarInfo(name)
                if kind == 'link':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/etc/passwd'
                    tar.addfile(info)
                else:
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b'x'))
        return path

    def test_complete_flat_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.archive(root, [('./' + n, 'file') for n in INPUT_FILES - {'Module.symvers'}])
            extract_inputs(archive, root / 'out')
            self.assertTrue((root / 'out/kernel.config').is_file())

    def test_rejects_traversal_links_duplicates_and_incomplete_bundle(self):
        cases = [
            [('../kernel.config', 'file')], [('kernel.config', 'link')],
            [('kernel.config', 'file'), ('kernel.config', 'file')],
            [('kernel.config', 'file')], [('/kernel.config', 'file')],
        ]
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with self.assertRaises(ValueError):
                    extract_inputs(self.archive(root, entries), root / 'out')


if __name__ == '__main__':
    unittest.main()
