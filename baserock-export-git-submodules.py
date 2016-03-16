#!/usr/bin/env python
# Copyright (C) 2016  Codethink Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.


'''Sam's Impractical Baserock Definitions to Git Submodules Converter.

Requires morphlib with <https://gerrit.baserock.org/2003/>.

You can produce a megarepo that has each repo involved in your build as a Git
submodule.

Git submodules are a bit crap though, see...

    <http://slopjong.de/2013/06/04/git-why-submodules-are-evil/>
    <https://news.ycombinator.com/item?id=3904932>

'''


import morphlib

import argparse
import logging
import os
import subprocess
import sys


DEFAULT_GIT_CACHE_DIR = '/src/cache/gits'

DEFAULT_ALIASES = [
    'upstream=git://git.baserock.org/delta/#x',
    'baserock=git://git.baserock.org/baserock/#x',
]

DEFAULT_REMOTE_CACHE = 'http://git.baserock.org:8080/'



def argument_parser():
    parser = argparse.ArgumentParser(
        description="Baserock -> Git submodules converter")
    parser.add_argument(
        'definition_file', type=str, metavar='DEFINITION_FILE')
    parser.add_argument(
        'output_dir', type=str, metavar='OUTPUT_DIR')
    parser.add_argument(
        '--git-cache-dir', '-c', type=str, metavar='DIR',
        default=DEFAULT_GIT_CACHE_DIR)
    return parser


def all_repos_and_refs_for_component(repo_cache, definition_file_path):
    '''Generate a list of (repo, ref) pairs for the build graph of a component.

    '''
    # FIXME: morphlib limitations means the definitions must be in a
    # git repo
    definitions_repo_dir = morphlib.util.find_root(
        os.path.dirname(definition_file_path), '.git')
    definition_file = os.path.relpath(
        definition_file_path, start=definitions_repo_dir)

    # Set up definitions repo.
    definitions_repo = morphlib.definitions_repo.open(definitions_repo_dir)
    source_pool_context = definitions_repo.source_pool(
        repo_cache=repo_cache, ref='HEAD', system_filename=definition_file)

    with source_pool_context as source_pool:
        # We now have a list of all sources involved in the build of this
        # component.

        # FIXME: this will break further down if two different refs from the
        # same repo are included.

        returned = set()

        for item in source_pool:
            pair = repo_cache._resolver.pull_url(item.repo_name), item.sha1
            if pair not in returned:
                returned.add(pair)
                yield pair


def create_git_megarepo(path, submodule_repo_ref_pairs):
    if os.path.exists(path):
        raise RuntimeError("Output directory %s already exists.", path)

    subprocess.check_call(['git', 'init', path])
    subprocess.check_call(['git', 'submodule', 'init'], cwd=path)

    for repo, ref in submodule_repo_ref_pairs:
        name = os.path.basename(repo)
        subprocess.check_call(
            ['git', 'submodule', 'add', '--name', name, repo], cwd=path)
        subprocess.check_call(
            ['git', 'checkout', ref], cwd=os.path.join(path, name))

    subprocess.check_call(
        ['git', 'commit', '--message', 'Add submodules.'])


def main():
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    args = argument_parser().parse_args()

    # Set up repo cache.
    resolver = morphlib.repoaliasresolver.RepoAliasResolver(
        aliases=DEFAULT_ALIASES)
    repo_cache = morphlib.repocache.RepoCache(
        args.git_cache_dir, resolver,
        git_resolve_cache_url=DEFAULT_REMOTE_CACHE)

    repo_ref_pairs = all_repos_and_refs_for_component(repo_cache,
                                                      args.definition_file)

    create_git_megarepo(args.output_dir, repo_ref_pairs)


main()
