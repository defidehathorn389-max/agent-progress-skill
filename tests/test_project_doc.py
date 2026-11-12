import os
import subprocess
import sys
import tempfile
import unittest

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')


def run(*a):
    return subprocess.run([sys.executable] + list(a), capture_output=True, text=True)


class ProjectDoc(unittest.TestCase):
    def test_card_written_on_new_and_checked(self):
        with tempfile.TemporaryDirectory() as root:
            r = run(os.path.join(S, 'handoff.py'), '--root', root, 'new', '--project', 'demo/x', '--title', 'T', '--goal', 'G')
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = open(os.path.join(root, 'projects/demo/x/PROJECT.md'), encoding='utf-8').read()
            heads = [l for l in doc.splitlines() if l.startswith('## ')]
            self.assertEqual(heads, ['## 最后更新', '## 现状', '## 入口文件', '## 下一步', '## 注意事项'])
            self.assertIn('无', doc)
            r = run(os.path.join(S, 'project_doc.py'), '--root', root, 'check')
            self.assertEqual(r.returncode, 0, r.stdout)
            os.remove(os.path.join(root, 'projects/demo/x/PROJECT.md'))
            r = run(os.path.join(S, 'project_doc.py'), '--root', root, 'check')
            self.assertEqual(r.returncode, 1)
            r = run(os.path.join(S, 'project_doc.py'), '--root', root, 'build')
            self.assertIn('WROTE demo/x', r.stdout)


if __name__ == '__main__':
    unittest.main()
