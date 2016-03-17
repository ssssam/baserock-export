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

# Any repo that doesn't have a 'master' branch causes a stupid error when you
# add it as a submodule:
#
#    Cloning into 'perl'...
#    remote: Counting objects: 456112, done.
#    remote: Compressing objects: 100% (105066/105066), done.
#    remote: Total 456112 (delta 351917), reused 446068 (delta 342762)
#    Receiving objects: 100% (456112/456112), 115.35 MiB | 1.40 MiB/s, done.
#    Resolving deltas: 100% (351917/351917), done.
#    Checking connectivity... done.
#    fatal: You are on a branch yet to be born
#    Unable to checkout submodule 'perl'
#
# To work around this bug, we have to pass the name of a branch that does exist
# using the `--branch` argument of `git submodule add`. Note that we cannot
# pass a tag or commit SHA1, it must be a branch.
#
DEFAULT_BRANCHES = {
    'git://git.baserock.org/delta/intltool': 'baserock/morph',
    'git://git.baserock.org/delta/perl': 'baserock/morph',
}


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


def submodule_info(gitdir, submodule_dir):
    output = subprocess.check_output(
        ['git', 'submodule', 'status', submodule_dir],
        cwd=gitdir.dirname)
    logging.debug("Status of %s: %s" % (submodule_dir, output))

    if output[0] == '-':
        initialized = False
    elif output[0] in [' ', '+']:
        initialized = True
    else:
        raise RuntimeError(
            "Unexpected output for 'git submodule status': %s" % output)

    commit = output[1:41].decode('ascii')

    return initialized, commit


def create_or_update_git_megarepo(path, submodule_repo_ref_pairs):
    if os.path.exists(path):
        logging.info("Output directory already exists.")
        gitdir = morphlib.gitdir.GitDirectory(path)
    else:
        logging.info("Creating new git directory")
        gitdir = morphlib.gitdir.init(path)
        subprocess.check_call(['git', 'submodule', 'init'], cwd=path)

    for repo, ref in submodule_repo_ref_pairs:
        name = os.path.basename(repo)

        # `git submodule add --name` will strip the .git extension off,
        # so we need to do so too.
        if name.endswith('.git'):
            name = name[:-4]

        submodule_path = os.path.join(path, name)

        if os.path.exists(submodule_path):
            logging.info("%s: Submodule dir exists", name)

            # FIXME: We don't check that the repo URL is correct
            initialized, existing_commit = submodule_info(gitdir, name)

            if existing_commit == ref:
                logging.info("%s: Already at ref %s", name, existing_commit)
            else:
                logging.info("%s: At ref %s, wanted %s", name, existing_commit, ref)
                if not initialized:
                    # We need to clone the whole thing to check out a commit
                    logging.info("%s: Need to clone submodule", name, ref)
                    subprocess.check_call(
                        ['git', 'submodule', 'update', '--init', name], cwd=path)
                logging.info("%s: Checking out ref %s", name, ref)
                subprocess.check_call(
                    ['git', 'checkout', ref], cwd=submodule_path)
        else:
            logging.info("Submodule for %s not set up. Cloning...", repo)
            branch = DEFAULT_BRANCHES.get(repo, 'master')
            subprocess.check_call(
                ['git', 'submodule', 'add', '--branch', branch, '--name', name,
                 repo], cwd=path)

            logging.info("%s: Checking out ref %s", name, ref)
            subprocess.check_call(['git', 'checkout', ref], cwd=submodule_path)

    subprocess.check_call(
        ['git', 'commit', '--all', '--message', 'Add/update submodules.'])


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

    create_or_update_git_megarepo(args.output_dir, repo_ref_pairs)


main()
