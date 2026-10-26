import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import refresh_contributions as refresh


def pr(number, state='open', owner='external', merged=None, title='Fix behavior'):
    return {'html_url': f'https://github.com/{owner}/project/pull/{number}',
            'title': title, 'state': state, 'created_at': f'2026-10-{number:02d}T00:00:00Z',
            'user': {'login': 'ogarciarevett'}, 'pull_request': {'merged_at': merged}}


class RefreshTests(unittest.TestCase):
    def test_owner_organizations_and_private_repositories_are_excluded(self):
        outside = pr(1)
        private = pr(5)
        private['repository'] = {'private': True}
        rows = refresh.select([outside, pr(4, owner='Edel-Finance'),
                               pr(3, owner='eignalabs'), private,
                               pr(2, owner='claude-hackaton-madrid-team-1')], 10)
        self.assertEqual(rows, [outside])

    def test_newest_first_filters_closed_and_owned_and_deduplicates(self):
        older = pr(1, 'closed', merged='2026-10-02T00:00:00Z')
        newer = pr(3)
        rows = refresh.select([older, newer, newer, pr(9, owner='ogarciarevett'),
                               pr(8, 'closed')], 2)
        self.assertEqual([row['html_url'] for row in rows],
                         [newer['html_url'], older['html_url']])
        self.assertEqual(len(refresh.select(rows, 1)), 1)

    def test_merge_transition_and_untrusted_title(self):
        row = pr(1, title='<img src=x> [link](evil)\nnext -- text')
        block = refresh.render([row])
        self.assertIn('**Open**', block)
        self.assertNotIn('<img', block)
        self.assertNotIn('\nnext', block)
        self.assertNotIn('[link](evil)', block)
        self.assertNotIn('--', block)
        row.update(state='closed', pull_request={'merged_at': '2026-10-02T00:00:00Z'})
        self.assertIn('**Merged**', refresh.render([row]))

    def test_preserves_surrounding_readme_and_is_idempotent(self):
        before = f'Intro\n{refresh.START}\nold\n{refresh.END}\nOutro\n'
        after = refresh.update(before, 'new\n')
        self.assertEqual(after, f'Intro\n{refresh.START}\nnew\n{refresh.END}\nOutro\n')
        self.assertEqual(refresh.update(after, 'new\n'), after)

    def test_missing_duplicate_and_reversed_markers_fail(self):
        for readme in ['missing', refresh.START * 2 + refresh.END,
                       refresh.END + refresh.START]:
            with self.subTest(readme=readme), self.assertRaises(ValueError):
                refresh.update(readme, '')

    def test_search_is_public_external_and_newest_first(self):
        response = subprocess.CompletedProcess([], 0,
            '{"incomplete_results":false,"total_count":0,"items":[]}')
        with patch.object(refresh.subprocess, 'run', return_value=response) as run:
            self.assertEqual(refresh.fetch('merged', 10), [])
        arguments = run.call_args.args[0]
        self.assertIn('q=is:pr author:ogarciarevett -user:ogarciarevett -org:claude-hackaton-madrid-team-1 -org:Edel-Finance -org:EignaLabs is:public is:merged', arguments)
        self.assertIn('sort=created', arguments)
        self.assertIn('order=desc', arguments)

    def test_incomplete_search_and_api_failure_fail(self):
        response = subprocess.CompletedProcess([], 0, '{"incomplete_results":true}')
        with patch.object(refresh.subprocess, 'run', return_value=response):
            with self.assertRaises(ValueError):
                refresh.fetch('open', 10)
        with patch.object(refresh.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'gh')):
            with self.assertRaises(subprocess.CalledProcessError):
                refresh.fetch('open', 10)

    def test_dry_run_does_not_write_and_live_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'README.md'
            before = f'{refresh.START}\nold\n{refresh.END}\n'
            path.write_text(before)
            with patch.object(refresh, 'fetch', return_value=[pr(1)]), contextlib.redirect_stdout(io.StringIO()):
                refresh.main(['--readme', str(path), '--dry-run'])
                self.assertEqual(path.read_text(), before)
                refresh.main(['--readme', str(path)])
                after = path.read_bytes()
                refresh.main(['--readme', str(path)])
                self.assertEqual(path.read_bytes(), after)


if __name__ == '__main__':
    unittest.main()
