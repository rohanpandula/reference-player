"""Patch preparation is reproducible and never discards a developer's engine edits."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class EnginePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'player with spaces'
        self.origin = Path(self.temp.name) / 'upstream'
        self.origin.mkdir()
        engine.git(self.origin, 'init', '-q')
        engine.git(self.origin, 'config', 'user.name', 'Test')
        engine.git(self.origin, 'config', 'user.email', 'test@example.invalid')
        (self.origin / 'a.swift').write_text('old\n')
        engine.git(self.origin, 'add', '.')
        engine.git(self.origin, 'commit', '-qm', 'base')
        engine.git(self.origin, 'tag', '1.0.0')
        revision = engine.git(self.origin, 'rev-parse', 'HEAD').strip()
        patch = self.root / engine.PATCH
        patch.parent.mkdir(parents=True)
        (self.origin / 'a.swift').write_text('new\n')
        patch.write_text(engine.git(self.origin, 'diff', '--binary', 'HEAD'))
        engine.git(self.origin, 'restore', 'a.swift')
        (self.root / 'PrivateBuild/UPSTREAM.json').write_text(json.dumps({'engine': {
            'url': self.origin.as_uri(), 'version': '1.0.0', 'revision': revision}}))

    def test_prepare_is_idempotent_and_keeps_upstream_commit(self):
        engine.prepare(self.root)
        engine.check(self.root)
        engine.prepare(self.root)
        self.assertEqual('new\n', (self.root / engine.CHECKOUT / 'a.swift').read_text())
        self.assertEqual(engine.git(self.origin, 'rev-parse', 'HEAD'),
                         engine.git(self.root / engine.CHECKOUT, 'rev-parse', 'HEAD'))

    def test_patch_can_add_regression_files(self):
        added = self.origin / 'regression.swift'
        added.write_text('regression\n')
        engine.git(self.origin, 'add', '-N', 'regression.swift')
        patch = self.root / engine.PATCH
        patch.write_text(patch.read_text() + engine.git(self.origin, 'diff', '--binary', 'HEAD', '--', 'regression.swift'))
        engine.git(self.origin, 'reset', '--', 'regression.swift')
        added.unlink()
        engine.prepare(self.root)
        engine.check(self.root)
        self.assertEqual('regression\n', (self.root / engine.CHECKOUT / 'regression.swift').read_text())

    def test_empty_patch_uses_upstream_unchanged(self):
        (self.root / engine.PATCH).write_text('')
        engine.prepare(self.root)
        engine.check(self.root)
        self.assertEqual('old\n', (self.root / engine.CHECKOUT / 'a.swift').read_text())

    def test_unexpected_edits_and_untracked_files_are_preserved(self):
        engine.prepare(self.root)
        source = self.root / engine.CHECKOUT / 'a.swift'
        source.write_text('my work\n')
        with self.assertRaisesRegex(ValueError, 'differs from the recorded patch'):
            engine.prepare(self.root)
        self.assertEqual('my work\n', source.read_text())
        source.write_text('new\n')
        extra = source.parent / 'notes.txt'
        extra.write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'untracked'):
            engine.prepare(self.root)
        self.assertEqual('keep me', extra.read_text())

    def test_wrong_revision_and_symlink_refused(self):
        engine.prepare(self.root)
        checkout = self.root / engine.CHECKOUT
        engine.git(checkout, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                   'commit', '-qam', 'unexpected')
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            engine.check(self.root)
        saved = checkout.with_name('saved')
        checkout.rename(saved)
        checkout.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinked'):
            engine.prepare(self.root)

    def test_changed_upstream_tag_is_refused(self):
        pin = self.root / 'PrivateBuild/UPSTREAM.json'
        content = json.loads(pin.read_text())
        content['engine']['revision'] = '0' * 40
        pin.write_text(json.dumps(content))
        with self.assertRaisesRegex(ValueError, 'tag no longer matches'):
            engine.prepare(self.root)
        self.assertFalse((self.root / engine.CHECKOUT).exists())

    def test_patch_conflict_leaves_no_partial_checkout(self):
        patch = self.root / engine.PATCH
        patch.write_text(patch.read_text().replace('-old', '-absent'))
        with self.assertRaises(subprocess.CalledProcessError):
            engine.prepare(self.root)
        self.assertFalse((self.root / engine.CHECKOUT).exists())
        self.assertEqual([], list((self.root / engine.CHECKOUT).parent.iterdir()))
