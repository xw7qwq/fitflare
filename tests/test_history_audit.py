"""Exercise legacy-history protection with synthetic Git objects only."""
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.audit_history import audit_history


@unittest.skipUnless(shutil.which('git'), 'History tests run on the host; the app image excludes Git.')
class HistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.repo / 'example.txt').write_text('synthetic historical marker\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Synthetic legacy commit')
        self.blocked = {self.git('rev-parse', 'HEAD:example.txt')}

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True, stderr=subprocess.PIPE).strip()

    def test_rejects_legacy_object_even_when_latest_file_is_clean(self):
        (self.repo / 'example.txt').write_text('clean replacement\n')
        self.git('commit', '-qam', 'Replace marker')
        with self.assertRaisesRegex(ValueError, 'Unsanitized legacy history'):
            audit_history(self.repo, self.blocked)

    def test_includes_local_tags_and_accepts_clean_replacement_history(self):
        self.git('tag', 'old-release')
        self.git('checkout', '--orphan', 'clean')
        (self.repo / 'example.txt').write_text('clean replacement\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Clean baseline')
        self.git('branch', '-D', 'main')
        with self.assertRaisesRegex(ValueError, 'Unsanitized legacy history'):
            audit_history(self.repo, self.blocked)
        self.git('tag', '-d', 'old-release')
        self.assertGreater(audit_history(self.repo, self.blocked), 0)

    def test_rejects_incomplete_history(self):
        shallow = Path(self.temp.name) / 'shallow'
        self.git('clone', '-q', '--depth=1', self.repo.as_uri(), str(shallow))
        with self.assertRaisesRegex(ValueError, 'complete clone'):
            audit_history(shallow, self.blocked)


if __name__ == '__main__':
    unittest.main()
