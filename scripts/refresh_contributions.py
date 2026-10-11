#!/usr/bin/env python3
"""Refresh public external PRs inside the profile README markers."""
import argparse
import html
import json
import re
import subprocess
from pathlib import Path

START = '<!-- LATEST-CONTRIBUTIONS:START -->'
END = '<!-- LATEST-CONTRIBUTIONS:END -->'
AUTHOR = 'ogarciarevett'


def fetch(state, limit):
    query = (f'is:pr author:{AUTHOR} -user:{AUTHOR} '
             f'-org:claude-hackaton-madrid-team-1 is:public is:{state}')
    result = subprocess.run(
        ['gh', 'api', '-X', 'GET', 'search/issues', '-f', f'q={query}',
         '-f', 'sort=created', '-f', 'order=desc', '-f', f'per_page={limit}'],
        check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    if data.get('incomplete_results', True):
        raise ValueError('GitHub search was incomplete; README left unchanged')
    if len(data['items']) < min(data['total_count'], limit):
        raise ValueError('GitHub search returned too few results')
    return data['items']


def select(items, limit):
    unique = {}
    for item in items:
        url = item['html_url']
        match = re.fullmatch(r'https://github.com/([\w.-]+)/([\w.-]+)/pull/(\d+)', url)
        if not match or match[1].lower() == AUTHOR:
            continue
        if item['user']['login'].lower() != AUTHOR:
            continue
        merged = item['pull_request'].get('merged_at')
        if item['state'] != 'open' and not merged:
            continue
        unique[url] = item
    return sorted(unique.values(), key=lambda row: (row['created_at'], row['html_url']),
                  reverse=True)[:limit]


def render(items):
    lines = []
    for item in items:
        url = item['html_url']
        owner, repo, _, number = url.removeprefix('https://github.com/').split('/')
        status = 'Merged' if item['pull_request'].get('merged_at') else 'Open'
        title = html.escape(' '.join(item['title'].split()), quote=False).replace('--', '—')
        title = re.sub(r'([\\`*_\[\]()])', r'\\\1', title)
        lines.append(f'- **{status}** [{owner}/{repo}#{number}]({url}) — {title}')
    return '\n'.join(lines or ['No public merged or open contributions found.']) + '\n'


def update(readme, block):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError('README must contain exactly one pair of contribution markers')
    start = readme.index(START) + len(START)
    end = readme.index(END)
    if start > end:
        raise ValueError('Contribution markers are reversed')
    return readme[:start] + '\n' + block + readme[end:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--readme', type=Path, default=Path('README.md'))
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error('limit must be between 1 and 100')
    before = args.readme.read_text(encoding='utf-8')
    update(before, '')  # Validate the boundary before contacting GitHub.
    items = select(fetch('merged', args.limit) + fetch('open', args.limit), args.limit)
    block = render(items)
    after = update(before, block)
    print(block, end='')
    print('README would change.' if before != after else 'README unchanged.')
    if not args.dry_run and before != after:
        args.readme.write_text(after, encoding='utf-8')


if __name__ == '__main__':
    main()
