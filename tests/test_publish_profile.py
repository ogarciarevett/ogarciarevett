"""Local Git fixtures; fake GitHub/curl/archive clients never contact a service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(os.environ.get('PROFILE_PUBLISH_SCRIPT',
                             Path(__file__).resolve().parents[1] / 'scripts/publish_profile.sh'))
BRANCH = 'automation/profile-contributions'


class PublishProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = {'PATH': str(self.bin) + ':' + os.defpath,
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                    'RUNNER_TEMP': str(self.root), 'DEFAULT_BRANCH': 'main',
                    'GITHUB_REPOSITORY': 'ogarciarevett/ogarciarevett',
                    'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'schedule',
                    'GH_TOKEN': 'synthetic-fixture-token',
                    'FIXTURE_STATE': str(self.root / 'pr.json'),
                    'FIXTURE_LOG': str(self.root / 'calls.jsonl')}
        self.tool('gh', '''import json, os, pathlib, sys
args=sys.argv[1:]; state=pathlib.Path(os.environ['FIXTURE_STATE'])
with open(os.environ['FIXTURE_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[:3]==['api','--method','GET']:
    if state.exists(): print(json.loads(state.read_text())['number'])
elif args[:2]==['pr','create']:
    if state.exists(): sys.exit('duplicate PR creation')
    state.write_text(json.dumps({'number':17,'args':args}))
    if os.environ.get('FAIL_AFTER_CREATE'): sys.exit('synthetic lost response')
    print('https://github.com/ogarciarevett/ogarciarevett/pull/17')
else: sys.exit('unexpected gh operation')
''')
        self.tool('curl', "import pathlib,sys\na=sys.argv;pathlib.Path(a[a.index('--output')+1]).touch()\n")
        self.tool('sha256sum', 'import sys\nsys.stdin.read()\n')
        self.tool('tar', '''import os,pathlib
p=pathlib.Path(os.environ['RUNNER_TEMP'])/'gitleaks'
p.write_text('#!/bin/sh\\necho scanned >> "$FIXTURE_LOG"\\nexit "${FAIL_GITLEAKS:-0}"\\n')
p.chmod(0o700)
''')
        self.origin = self.root / 'origin.git'
        self.git(self.root, 'init', '--bare', str(self.origin))
        self.work = self.clone('initial')
        self.git(self.work, 'switch', '-c', 'main')
        (self.work / 'README.md').write_text('base profile\n')
        self.commit(self.work, 'seed')
        self.git(self.work, 'push', 'origin', 'HEAD:main')
        self.git(self.root, '--git-dir=' + str(self.origin), 'symbolic-ref', 'HEAD', 'refs/heads/main')
        self.base = self.ref('main')
        hook = self.origin / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nwhile read old new ref; do\n'
                        '  [ "$ref" != refs/heads/main ] || exit 1\ndone\n')
        hook.chmod(0o700)

    def tool(self, name, code):
        p = self.bin / name
        p.write_text('#!' + sys.executable + '\n' + code)
        p.chmod(0o700)

    def git(self, cwd, *args, check=True):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env,
                              text=True, capture_output=True, check=check)

    def clone(self, name):
        p = self.root / name
        self.git(self.root, 'clone', str(self.origin), str(p))
        self.git(p, 'config', 'user.name', 'Fixture Human')
        self.git(p, 'config', 'user.email', 'fixture@example.invalid')
        return p

    def commit(self, work, message):
        self.git(work, 'add', '--all')
        self.git(work, 'commit', '-m', message)

    def ref(self, name):
        return self.git(self.root, '--git-dir=' + str(self.origin), 'rev-parse',
                        'refs/heads/' + name).stdout.strip()

    def publish(self, work=None, text='generated public feed\n', **overrides):
        work = work or self.work
        if text is not None:
            (work / 'README.md').write_text(text)
        return subprocess.run(['bash', str(SCRIPT)], cwd=work,
                              env=dict(self.env, **overrides), text=True, capture_output=True)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ref('main'), self.base, 'publisher changed protected main')

    def test_changed_readme_creates_topic_pr_and_keeps_main(self):
        self.assert_success(self.publish())
        self.assertNotEqual(self.ref(BRANCH), self.base)
        pr = json.loads((self.root / 'pr.json').read_text())
        self.assertEqual(pr['number'], 17)
        self.assertIn(BRANCH, pr['args'])
        self.assertEqual(self.git(self.work, 'show', '--format=%an', '--no-patch').stdout.strip(),
                         'github-actions[bot]')
        self.assertIn('scanned', (self.root / 'calls.jsonl').read_text())

    def test_normal_manual_dispatch_is_supported(self):
        self.assert_success(self.publish(GITHUB_EVENT_NAME='workflow_dispatch'))
        self.assertTrue((self.root / 'pr.json').exists())

    def test_unchanged_readme_has_no_branch_or_pr_side_effect(self):
        self.assert_success(self.publish(text=None))
        self.assertEqual(self.git(self.root, '--git-dir=' + str(self.origin),
                                  'for-each-ref', '--format=%(refname)').stdout.strip(),
                         'refs/heads/main')
        self.assertFalse((self.root / 'pr.json').exists())

    def test_second_refresh_updates_same_pr_without_rewriting_history(self):
        self.assert_success(self.publish())
        old = self.ref(BRANCH)
        second = self.clone('second')
        self.assert_success(self.publish(second, 'newer feed\n'))
        self.git(second, 'merge-base', '--is-ancestor', old, self.ref(BRANCH))
        calls = (self.root / 'calls.jsonl').read_text()
        self.assertEqual(calls.count('"create"'), 1)
        self.assertNotIn('"merge"', calls)
        self.assertNotIn('"review"', calls)

    def test_changed_default_content_is_preserved(self):
        self.assert_success(self.publish())
        owner = self.clone('owner-fixture')
        (owner / 'profile.txt').write_text('new approved default content\n')
        self.commit(owner, 'approved fixture default change')
        new_base = self.git(owner, 'rev-parse', 'HEAD').stdout.strip()
        self.git(owner, 'push', 'origin', 'HEAD:refs/heads/fixture-seed')
        self.git(self.root, '--git-dir=' + str(self.origin), 'update-ref', 'refs/heads/main', new_base)
        self.base = new_base
        work = self.clone('after-default-change')
        self.assert_success(self.publish(work, 'next feed\n'))
        self.assertEqual(self.git(work, 'show', 'HEAD:profile.txt').stdout,
                         'new approved default content\n')
        self.git(work, 'merge-base', '--is-ancestor', new_base, self.ref(BRANCH))

    def test_foreign_topic_content_is_rejected(self):
        self.git(self.work, 'switch', '-c', BRANCH)
        (self.work / 'foreign.py').write_text('raise SystemExit("untrusted")\n')
        self.commit(self.work, 'unexpected branch content')
        self.git(self.work, 'push', 'origin', 'HEAD:' + BRANCH)
        old = self.ref(BRANCH)
        work = self.clone('foreign-content')
        self.assertNotEqual(self.publish(work).returncode, 0)
        self.assertEqual(self.ref(BRANCH), old)
        self.assertEqual(self.ref('main'), self.base)
        self.assertFalse((self.root / 'pr.json').exists())

    def test_topic_readme_symlink_cannot_write_outside_checkout(self):
        sentinel = self.root / 'outside-profile.txt'
        sentinel.write_text('untouched fixture sentinel\n')
        self.git(self.work, 'switch', '-c', BRANCH)
        (self.work / 'README.md').unlink()
        (self.work / 'README.md').symlink_to(sentinel)
        self.commit(self.work, 'unexpected README symlink')
        self.git(self.work, 'push', 'origin', 'HEAD:' + BRANCH)
        old = self.ref(BRANCH)
        work = self.clone('symlink-content')
        self.assertNotEqual(self.publish(work).returncode, 0)
        self.assertEqual(sentinel.read_text(), 'untouched fixture sentinel\n')
        self.assertEqual(self.ref(BRANCH), old)
        self.assertFalse((self.root / 'pr.json').exists())

    def test_wrong_repo_event_ref_or_missing_token_is_rejected(self):
        for override in [{'GITHUB_REPOSITORY': 'foreign/profile'},
                         {'GITHUB_EVENT_NAME': 'pull_request'},
                         {'GITHUB_REF': 'refs/heads/other'}, {'GH_TOKEN': ''}]:
            with self.subTest(override=override):
                before = self.git(self.work, 'rev-parse', 'HEAD').stdout.strip()
                self.assertNotEqual(self.publish(**override).returncode, 0)
                self.assertEqual(self.git(self.work, 'rev-parse', 'HEAD').stdout.strip(), before)
                self.assertEqual(self.ref('main'), self.base)
                self.assertFalse((self.root / 'pr.json').exists())

    def test_secret_scan_failure_stops_publication(self):
        self.assertNotEqual(self.publish(FAIL_GITLEAKS='1').returncode, 0)
        self.assertEqual(self.ref('main'), self.base)
        self.assertFalse((self.root / 'pr.json').exists())
        self.assertNotEqual(self.git(self.root, '--git-dir=' + str(self.origin),
                                     'show-ref', '--verify', 'refs/heads/' + BRANCH,
                                     check=False).returncode, 0)

    def test_uncertain_create_retries_by_finding_existing_pr(self):
        self.assertNotEqual(self.publish(FAIL_AFTER_CREATE='1').returncode, 0)
        old = self.ref(BRANCH)
        work = self.clone('retry')
        self.assert_success(self.publish(work))
        self.assertEqual(self.ref(BRANCH), old)
        self.assertEqual((self.root / 'calls.jsonl').read_text().count('"create"'), 1)


if __name__ == '__main__':
    unittest.main()
