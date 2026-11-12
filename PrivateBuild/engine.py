#!/usr/bin/env python3
"""Prepare/check the pinned AetherEngine plus this fork's patch. Never overwrite an existing checkout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CHECKOUT = Path('PrivateBuild/.build/AetherEngine')
PATCH = Path('PrivateBuild/engine-patches/0001-fel-accuracy.patch')


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-c', 'core.fsmonitor=false', '-c', 'diff.algorithm=myers',
                                    '-C', str(path), *args], text=True, stderr=subprocess.PIPE)


def check(root: Path) -> None:
    path = root / CHECKOUT
    pin = json.loads((root / 'PrivateBuild/UPSTREAM.json').read_text())['engine']
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Engine checkout missing or symlinked; run python3 PrivateBuild/engine.py')
    if Path(git(path, 'rev-parse', '--show-toplevel').strip()).resolve() != path.resolve():
        raise ValueError('Engine must be a standalone Git checkout')
    if git(path, 'rev-parse', 'HEAD').strip() != pin['revision']:
        raise ValueError('Engine revision changed; preserve the checkout elsewhere and prepare it again')
    if git(path, 'ls-files', '--others', '--exclude-standard').strip():
        raise ValueError('Engine has untracked files; preserve them before preparing again')
    diff = git(path, 'diff', '--binary', '--no-ext-diff', '--no-color', '--no-renames', 'HEAD', '--')
    if diff != (root / PATCH).read_text():
        raise ValueError('Engine differs from the recorded patch; preserve local edits before preparing again')


def prepare(root: Path) -> None:
    path = root / CHECKOUT
    if path.exists() or path.is_symlink():
        check(root)
        return
    pin = json.loads((root / 'PrivateBuild/UPSTREAM.json').read_text())['engine']
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='engine-', dir=path.parent))
    try:
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', pin['version'], '--no-checkout',
                        pin['url'], str(staging)], check=True)
        if git(staging, 'rev-parse', 'HEAD').strip() != pin['revision']:
            raise ValueError('Upstream tag no longer matches the reviewed revision')
        git(staging, 'checkout', '--detach', pin['revision'])
        if (root / PATCH).read_text():
            git(staging, 'apply', '--check', '--index', str(root / PATCH))
            git(staging, 'apply', '--index', str(root / PATCH))
        staging.rename(path)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    check(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify only, without network or writes')
    args = parser.parse_args()
    try:
        (check if args.check else prepare)(ROOT)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'FAIL: {exc}')
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip())
        return 1
    print('PASS: pinned AetherEngine with the exact Reference Player patch')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
