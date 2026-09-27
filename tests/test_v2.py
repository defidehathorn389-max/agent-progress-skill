"""v2 features: iterative chain walk, patch updates, resume brief, lint, readable views."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / 'state.json'


def state(**over):
    s = json.loads(TEMPLATE.read_text(encoding='utf-8'))
    s.update(title='Test project', goal='Recover verified progress')
    s.update(over)
    return s


class Base(unittest.TestCase):
    project = 'test/example'

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def new(self, s=None):
        return h.checkpoint(self.r, self.project, s or state(), 'NEW', 'create')


class DeepChain(Base):
    def test_long_history_has_no_recursion_limit(self):
        """v1 recursed once per revision and crashed near 1000 revisions."""
        p = self.r / 'projects' / 'test' / 'example'
        (p / 'checkpoints').mkdir(parents=True)
        parents, s = [], state()
        for i in range(1, 1501):
            cid = f'20260101T{i // 3600:02d}{(i // 60) % 60:02d}{i % 60:02d}Z-{i:012x}'
            cp = {'schema_version': 1, 'checkpoint_id': cid, 'parents': parents, 'revision': i, 'domain': 'test',
                  'project': 'example', 'updated_at': '2026-01-01T00:00:00+00:00', 'note': 'n', 'state': s}
            data = h.dump(cp)
            (p / 'checkpoints' / (cid + '.json')).write_bytes(data)
            parents = [{'checkpoint_id': cid, 'sha256': h.digest(data)}]
        h.atomic(p / 'HEAD.json', h.dump({'schema_version': 1, 'checkpoint_id': cid, 'sha256': parents[0]['sha256'],
                                          'revision': 1500}))
        self.assertEqual(h.validate(self.r)['projects'][0]['checkpoints_checked'], 1500)
        nxt = h.update(self.r, self.project, {'set': {'status': 'PAUSED'}}, cid, 'pause after long history')
        self.assertEqual(nxt['revision'], 1501)
        self.assertIn('rev 1501', h.resume_text(self.r, self.project))

    def test_missing_ancestor_read_lenient_validate_strict(self):
        a = self.new()
        b = h.update(self.r, self.project, {'set': {'status': 'PAUSED'}}, a['checkpoint_id'], 'b')
        h.update(self.r, self.project, {'set': {'status': 'IN_PROGRESS'}}, b['checkpoint_id'], 'c')
        (h.project_dir(self.r, self.project) / 'checkpoints' / (a['checkpoint_id'] + '.json')).unlink()
        self.assertIn('本地缺 1 个祖先', h.resume_text(self.r, self.project))
        with self.assertRaises(h.HandoffError):
            h.validate(self.r)

    def test_ancestors_checked_structurally_deep_is_advisory(self):
        a = self.new()
        h.update(self.r, self.project, {'set': {'status': 'PAUSED'}}, a['checkpoint_id'], 'b')
        self.assertEqual(h.validate(self.r, deep=True)['projects'][0]['ancestor_advisories'], [])


class Patches(Base):
    def test_patch_operations_in_order(self):
        base = state(pending=[{'id': 'p1', 'action': 'old', 'done_when': 'x'}, {'id': 'p2', 'action': 'keep', 'done_when': 'y'}],
                     next_actions=['a', 'b'])
        a = self.new(base)
        patch = {
            'set': {'status': 'READY_FOR_REVIEW', 'handoff.waiting_for': 'user review', 'context.episode': 'E-7'},
            'unset': ['handoff.waiting_for_missing_is_fine'],
            'remove': {'next_actions': ['a']},
            'upsert': {'pending': [{'id': 'p1', 'action': 'new', 'done_when': 'x2'}, {'id': 'p3', 'action': 'add', 'done_when': 'z'}]},
            'append': {'evidence': [{'id': 'ev1', 'kind': 'local_test', 'source': 'unittest', 'scope': 'patch'}],
                       'completed': [{'item': 'patched', 'evidence': ['ev1']}]},
        }
        out = h.update(self.r, self.project, patch, a['checkpoint_id'], 'patch')
        self.assertEqual(out['revision'], 2)
        s = h.checked_head(h.project_dir(self.r, self.project))[1]['state']
        self.assertEqual(s['status'], 'READY_FOR_REVIEW')
        self.assertEqual(s['handoff']['waiting_for'], 'user review')
        self.assertEqual(s['context'], {'episode': 'E-7'})
        self.assertEqual(s['next_actions'], ['b'])
        self.assertEqual([x['id'] for x in s['pending']], ['p1', 'p2', 'p3'])
        self.assertEqual(s['pending'][0]['action'], 'new')
        self.assertEqual(s['completed'][0]['evidence'], ['ev1'])
        self.assertFalse(h.validate(self.r)['projects'][0]['warnings'])

    def test_dry_run_writes_nothing(self):
        a = self.new()
        res = h.update(self.r, self.project, {'set': {'goal': 'changed'}}, a['checkpoint_id'], 'dry', dry_run=True)
        self.assertTrue(res['dry_run'])
        self.assertEqual(res['changed_keys'], ['goal'])
        self.assertEqual(h.checked_head(h.project_dir(self.r, self.project))[0]['checkpoint_id'], a['checkpoint_id'])

    def test_rejections(self):
        a = self.new()
        cid = a['checkpoint_id']
        bad = [
            {},                                             # empty
            {'bogus': {}},                                  # unknown op
            {'set': {'goal': 'Recover verified progress'}},  # no change
            {'set': {'unknown_top': 1}},                    # outside schema
            {'unset': ['goal']},                            # top-level unset
            {'remove': {'pending': ['nope']}},              # nothing matched
            {'remove': {'pending': 'p1'}},                  # not an array
            {'upsert': {'pending': [{'action': 'no id'}]}},  # upsert needs id
            {'append': {'goal': ['x']}},                    # not a list target
            {'set': {'handoff.first_action': ''}},          # fails validation
            {'set': {'goal': 'gh' + 'p_' + 'A' * 36}},      # secret
        ]
        for patch in bad:
            with self.subTest(patch=patch), self.assertRaises(h.HandoffError):
                h.update(self.r, self.project, patch, cid, 'bad')
        h.update(self.r, self.project, {'set': {'goal': 'v2'}}, cid, 'ok')
        with self.assertRaises(h.HandoffError):  # stale expected after a successful write
            h.update(self.r, self.project, {'set': {'goal': 'v3'}}, cid, 'stale')

    def test_cli_update_from_stdin(self):
        a = self.new()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps({'set': {'status': 'PAUSED'}}))
        try:
            with redirect_stdout(io.StringIO()) as buf:
                code = h.main(['--root', str(self.r), 'update', '--project', self.project, '--expected',
                               a['checkpoint_id'], '--patch', '-', '--note', 'pause'])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue())['revision'], 2)


class SchemaAndViews(Base):
    def test_context_optional_unknown_rejected(self):
        self.new(state(context={'episode': 'E-1'}))
        with self.assertRaises(h.HandoffError):
            h.checkpoint(self.r, 'test/other', dict(state(), extra=1), 'NEW', 'x')

    def test_lint_warnings(self):
        s = state(status='COMPLETED', pending=['free text'], sync={'memory': 'PENDING_SYNC'})
        s['handoff'].update({f'k{i}': i for i in range(20)})
        lint = h.lint_state(s)
        joined = ' | '.join(lint)
        for needle in ('handoff has', 'COMPLETED but', 'sync.memory', 'free text'):
            self.assertIn(needle, joined)
        self.assertEqual(h.lint_state(state(pending=[{'id': 'a', 'action': 'b', 'done_when': 'c'}])), [])

    def test_current_view_readable_and_stable(self):
        s = state(pending=[{'id': 'p1', 'action': 'Do it', 'blocked_by': 'user', 'done_when': 'approved'}],
                  evidence=[{'id': 'ev', 'kind': 'test', 'source': 'ci', 'scope': 'unit'}],
                  completed=[{'item': 'Built', 'evidence': ['ev']}])
        s['handoff'] = {'running_operations': [], 'waiting_for': 'review', 'first_action': 'Open the brief'}
        self.new(s)
        text = (h.project_dir(self.r, self.project) / 'CURRENT.md').read_text(encoding='utf-8')
        self.assertIn('**[p1]** Do it（阻塞：user；完成判据：approved）', text)
        self.assertIn('Built（证据：ev）', text)
        self.assertLess(text.index('## 接手第一步'), text.index('## 目标'))
        self.assertNotIn('{"action"', text)
        self.assertFalse(h.validate(self.r)['projects'][0]['warnings'])  # re-rendered view is byte-identical

    def test_resume_brief(self):
        s = state(pending=[{'id': 'p1', 'action': 'Ship', 'done_when': 'user approves'}])
        s['handoff']['waiting_for'] = 'final review'
        a = self.new(s)
        text = h.resume_text(self.r, self.project)
        for needle in ('接手第一步', 'waiting_for: final review', '**[p1]** Ship', a['checkpoint_id'], '--expected ' + a['checkpoint_id']):
            self.assertIn(needle, text)

    def test_generic_exception_does_not_crash_cli(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(h.main(['--root', str(self.r), 'resume', '--project', 'Bad/Name']), 1)
        self.assertIn('Handoff failed', err.getvalue())


if __name__ == '__main__':
    unittest.main()
