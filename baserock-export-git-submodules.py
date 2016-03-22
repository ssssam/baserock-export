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
import re
import subprocess
import sys
import xml.etree.cElementTree as ET
import xml.dom.minidom as minidom

DEFAULT_MODE = 'submodule'
MODES = ['submodule', 'subtree', 'subrepo', 'repo']

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
    parser.add_argument(
        '--mode', '-m', type=str, metavar='MODE',
        default=DEFAULT_MODE)
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


def create_or_update_repo(path, repo, ref, gitdir, xmlroot):
    name = os.path.basename(repo)

    # `git submodule add --name` will strip the .git extension off,
    # so we need to do so too.
    if name.endswith('.git'):
        name = name[:-4]

    # Hack to strip the beggining of the url
    repo = re.sub('^git://git.baserock.org', '', repo)
    repo = re.sub('^ssh://git@git.baserock.org', '', repo)
    repo = repo[1:]
    ET.SubElement(xmlroot, "project", name=repo, path=name,
                  remote='baserock', revision=ref)

def create_or_update_subrepo(path, repo, ref, gitdir):
    name = os.path.basename(repo)

    # `git submodule add --name` will strip the .git extension off,
    # so we need to do so too.
    if name.endswith('.git'):
        name = name[:-4]

    subrepo_path = os.path.join(path, name)
    branch = DEFAULT_BRANCHES.get(repo, 'master')
    # FIXME: seems it doesn' have support for refs
    if os.path.exists(subrepo_path):
        logging.info("%s: Subrepo dir exists", name)
        subprocess.check_call(
            ['git', 'subrepo', 'pull', name, '-b', branch, '-r', repo], cwd=path)
    else:
        logging.info("Subrepo for %s not set up. Adding...", repo)
        subprocess.check_call(
            ['git', 'subrepo', 'clone', repo, name, '-b', branch], cwd=path)

def create_or_update_subtree(path, repo, ref, gitdir):
    name = os.path.basename(repo)

    # `git submodule add --name` will strip the .git extension off,
    # so we need to do so too.
    if name.endswith('.git'):
        name = name[:-4]

    subtree_path = os.path.join(path, name)
    branch = DEFAULT_BRANCHES.get(repo, 'master')
    # FIXME: seems it doesn' have support for refs
    if os.path.exists(subtree_path):
        logging.info("%s: Subtree dir exists", name)
        # FIXME: subtree pull doesn't seem to have support for --force, this might
        # ask for user input
        subprocess.check_call(
            ['git', 'subtree', 'pull', '--prefix', name, repo, branch], cwd=path)
    else:
        logging.info("Subtree for %s not set up. Adding...", repo)
        subprocess.check_call(
            ['git', 'subtree', 'add', '--prefix', name, repo, branch], cwd=path)

def create_or_update_submodule(path, repo, ref, gitdir):
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

def create_or_update_git_megarepo(path, repo_ref_pairs, mode):
    if os.path.exists(path):
        logging.info("Output directory already exists.")
        gitdir = morphlib.gitdir.GitDirectory(path)
    else:
        logging.info("Creating new git directory")
        gitdir = morphlib.gitdir.init(path)
        subprocess.check_call(['git', 'submodule', 'init'], cwd=path)
    # Intialization needed if any
    if mode == 'repo':
        xmlroot = ET.Element('manifest')
        ET.SubElement(xmlroot, "remote", name="baserock", fetch="git://git.baserock.org")

    for repo, ref in repo_ref_pairs:
        if mode == 'submodule':
            create_or_update_submodule(path, repo, ref, gitdir)
        elif mode == 'subtree':
            create_or_update_subtree(path, repo, ref, gitdir)
        elif mode == 'subrepo':
            create_or_update_subrepo(path, repo, ref, gitdir)
        elif mode == 'repo':
            create_or_update_repo(path, repo, ref, gitdir, xmlroot)
        else:
            logging.error("Mode %s will be supported, but not yet")
            exit()

    if mode == 'repo':
        tree = ET.ElementTree(xmlroot)
        xml_file = os.path.join(path, 'manifest.xml')
        with open (xml_file, "w") as f:
            f.write(minidom.parseString(ET.tostring(xmlroot, 'utf-8')).toprettyxml(indent="  "))
        subprocess.check_call(
            ['git', 'add', 'manifest.xml'], cwd=path)

    subprocess.check_call(
        ['git', 'commit', '--all', '--message', 'Add/update ' + mode + 's'],
        cwd=path)


def main():
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    args = argument_parser().parse_args()
    mode = args.mode
    if mode not in MODES:
        logging.error("Mode %s is not supported, available modes: %s" %(mode, MODES))
        exit()

    # Set up repo cache.
    resolver = morphlib.repoaliasresolver.RepoAliasResolver(
        aliases=DEFAULT_ALIASES)
    repo_cache = morphlib.repocache.RepoCache(
        args.git_cache_dir, resolver,
        git_resolve_cache_url=DEFAULT_REMOTE_CACHE)

    repo_ref_pairs = all_repos_and_refs_for_component(repo_cache,
                                                      args.definition_file)

    create_or_update_git_megarepo(args.output_dir, repo_ref_pairs, mode)


main()
