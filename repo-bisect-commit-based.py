#!/usr/local/bin/python3

# While this software is considered public domain, please continue
# to keep this attribution to the author, Tom Marshall, in the code.
# https://github.com/tdm/

# This is free and unencumbered software released into the public domain.

# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.

# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

# For more information, please refer to <https://unlicense.org>

import os
import sys
import getopt
import pickle
import subprocess
import urllib
import json
import re
import datetime
import xml.etree.ElementTree as ElementTree

from operator import itemgetter

cwd = os.getcwd()
if not os.path.exists('.repo'):
    sys.stderr.write("Not a repo\n")
    sys.exit(1)

print_all = False

def git_config(key):
    args = ['git', 'config', key]
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None)
    out, err = child.communicate()
    if child.returncode != 0:
        sys.stderr.write('Failed to read git config\n')
        sys.exit(1)
    return out.strip().decode()

def git_reset_hard(rev):
    args = ['git', 'reset', '--hard', rev]
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None)
    out, err = child.communicate()
    if child.returncode != 0:
        sys.stderr.write('Failed to reset git tree\n')
        sys.exit(1)

def git_checkout(rev):
    args = ['git', 'checkout', rev]
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None)
    out, err = child.communicate()
    if child.returncode != 0:
        sys.stderr.write('Failed to checkout git tree\n')
        sys.exit(1)

# returns something like "Fri May 19 14:31:20 2023 -0700 [mcs] Fix bug in stopCurrentCaptureFlow"
def git_show(c_hash):
    args = ['git', 'show', '--no-patch', '--no-notes', '--pretty=%ci|%s', '%s' % (c_hash)]
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = child.communicate()
    if child.returncode != 0:
        return [None, None]

    show_info = out.strip().decode()
    [date_str, log_txt] = show_info.split('|')
    [ymd_str, hhmmss_str, gmt_offset] = date_str.split(' ')
    [y, m, d] = ymd_str.split('-')
    [hh, mm, ss] = hhmmss_str.split(':')

    time = datetime.datetime(int(y), int(m), int(d),
                             int(hh), int(mm), int(ss))
    return [time, log_txt]

def git_info_by_commit_hash(c_hash):

    # Fetch the manifest
    manifest = repo_manifest()

    # get rid of any strings before the hash
    pattern = '[a-z0-9]+$'
    s_c_hash_indices = re.search(pattern, c_hash) # stripped hash
    if s_c_hash_indices is None:
        return None
    s_c_hash = c_hash[s_c_hash_indices.start():s_c_hash_indices.end()]

    time = None
    log_txt = None
    project_path = None
    for elem in manifest.findall('project'):
        project_name = elem.get('name')
        project_path = elem.get('path', project_name)
        os.chdir(".repo/projects/%s.git" % (project_path))
        [time, log_txt] = git_show(s_c_hash)
        os.chdir(cwd)
        if time is not None:
            break

    if time is None:
        print("Could not find %s commit hash" % (c_hash))
    else:
        print("Found commit hash %s at time %s" % (c_hash, time.strftime("%Y-%m-%dT%H:%M:%S")))
    return [time, log_txt, project_path]

def git_read_file_revisions():
    f = open('.repo/rev_data', 'rb')
    rev_info = pickle.load(f)
    f.close()
    return rev_info

def git_write_file_revisions(rev_info):
    f = open('.repo/rev_data', 'wb')
    pickle.dump(rev_info, f)
    f.close()
    return rev_info

def git_get_and_write_sorted_revisions(start, end):
    manifest = repo_manifest()

    for elem in manifest.findall('default'):
        def_remote = elem.get('remote')
        def_revision = elem.get('revision')
        if def_revision.startswith('refs/heads/'):
            # refs/heads/branch => branch
            def_revision = def_revision.split('/')[2]

    rev_info = []
    for elem in manifest.findall('project'):
        project_name = elem.get('name')
        project_path = elem.get('path', project_name)
        project_remote = elem.get('remote', def_remote)
        project_revision = elem.get('revision', def_revision)
        if project_revision.startswith('refs/tags'):
            manifest_revision = project_revision.split('/')[2]
        else:
            manifest_revision = "%s/%s" % (project_remote, project_revision)
        os.chdir(".repo/projects/%s.git" % (project_path))
        out = git_rev_between_dates(start, end, manifest_revision)
        if out is not None:
            l_revs = out.split()
            for r in l_revs:
                [time, log_text] = git_show(r.strip())
                rev_info += [['', r, time, log_text]]
        os.chdir(cwd)

    rev_info = sorted(rev_info, key=lambda x:x[2], reverse=False)
    if len(rev_info) < 3:
        print("Less than 3 revisions between dates, not useful data, exiting")
        sys.exit(1)
    # mark last commit as bad
    rev_info[len(rev_info)-1][0] = 'bad'
    f = open('.repo/rev_data', 'wb')
    pickle.dump(rev_info, f)
    f.close()
    return rev_info

def is_found(rev_info):
    last_good = -1
    i = 0
    for r in rev_info:
        if r[0] == 'good':
            last_good = i
        elif r[0] == 'bad':
            if last_good == i - 1:
                return True
        i += 1
    return False

def set_next_commit_date(rev_info, c_hash):
    last_good = find_last_good(rev_info)
    count = 0
    time = None
    for r in rev_info:
        count += 1
        if count <= last_good:
            continue
        elif r[0] == 'bad':
            break
        elif r[1] == c_hash:
            r[0] = 'current'
            time = r[2]
            break

    if time is None:
        print("Failed: Could not find commit hash between last good and first bad index")
        return None

    for r in rev_info:
        if r[0] == 'current' and r[1] != c_hash:
            r[0] = ''

    git_write_file_revisions(rev_info)

    return time

def get_next_commit_date(rev_info, good):
    if is_found(rev_info):
        return None

    last_good = -1
    next_bad = -1
    current = -1
    length = len(rev_info)
    i = 0
    for r in rev_info:
        if r[0] == 'good':
            last_good = i
        elif r[0] == 'current':
            current = i
        elif r[0] == 'bad' and current != -1:
            next_bad = i
            break
        i += 1

    if current == -1:
        next_current = int(length / 2)
    else:
        if good:
            rev_info[current][0] = 'good'
            if next_bad == -1:
                next_current = current + int((length - current) / 2)
            else:
                next_current = current + int((next_bad - current) / 2)
        else:
            rev_info[current][0] = 'bad'
            if last_good == -1:
                next_current = int(current / 2)
            else:
                next_current = current - int((current - last_good) / 2)

    if rev_info[next_current][0] != '':
        git_write_file_revisions(rev_info)
        return None # done, found it

    rev_info[next_current][0] = 'current'
    git_write_file_revisions(rev_info)
    return rev_info[next_current]


def git_check_and_convert_hash(date):
    try:
        d = datetime.datetime.strptime(date, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        d = None
    if d is None:
        [time, log_txt, project_path] = git_info_by_commit_hash(date)
        if time is None:
            return None
        date = time.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        date = d.strftime("%Y-%m-%dT%H:%M:%S")
    return date

def git_rev_between_dates(start, end, branch):
    args = ['git', 'rev-list']
    args.append('--after=%s' % (start))
    args.append('--before=%s' % (end))
    args.append(branch)
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = child.communicate()
    if child.returncode != 0 or not out:
        return None
    return out.strip().decode()

def git_rev_by_date(date, branch):
    args = ['git', 'rev-list', '--max-count=1']
    args.append('--until=%s' % (date))
    args.append(branch)
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None)
    out, err = child.communicate()
    if child.returncode != 0:
        sys.stderr.write('Failed to read git log\n')
        sys.exit(1)
    if not out:
        sys.stderr.write('Failed to find rev')
        sys.exit(1)
    return out.strip().decode()

def repo_manifest():
    args = []
    args.append('repo')
    args.append('manifest')
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None)
    out, err = child.communicate()
    if child.returncode != 0:
        sys.stderr.write('Failed to read manifest\n')
        sys.exit(1)
    return ElementTree.fromstring(out)

def repo_sync_to_date(date, c_hash):
    print("bisect: sync to %s (%s)" % (date, c_hash))

    # Set manifest revision
    os.chdir('.repo/manifests')
    remote = git_config('branch.default.remote')
    branch = os.path.basename(git_config('branch.default.merge'))
    manifest_rev = git_rev_by_date(date, '%s/%s' % (remote, branch))
    git_reset_hard(manifest_rev)
    os.chdir(cwd)

    # Fetch the manifest
    manifest = repo_manifest()

    # Find default remote and revision
    for elem in manifest.findall('default'):
        def_remote = elem.get('remote')
        def_revision = elem.get('revision')
        if def_revision.startswith('refs/heads/'):
            # refs/heads/branch => branch
            def_revision = def_revision.split('/')[2]

    # Update manifest revisions
    for elem in manifest.findall('project'):
        project_name = elem.get('name')
        project_path = elem.get('path', project_name)
        project_remote = elem.get('remote', def_remote)
        project_revision = elem.get('revision', def_revision)
        if project_revision.startswith('refs/tags'):
            manifest_revision = project_revision.split('/')[2]
        else:
            manifest_revision = "%s/%s" % (project_remote, project_revision)
        os.chdir(".repo/projects/%s.git" % (project_path))
        rev = git_rev_by_date(date, manifest_revision)
        os.chdir(cwd)
        elem.set('revision', rev)

    # Write new manifest
    pathname = "%s/.repo/manifests/bisect-%s.xml" % (cwd, date)

    ElementTree.ElementTree(manifest).write(pathname)

    # Sync the working tree.  Note:
    #
    #  - Both "repo manifest" and "repo sync -m" include local manifests,
    #    so we must move the local manifests out of the way temporarily
    #    to avoid duplicate project errors.
    #
    #  - "repo sync" always syncs up the main manifest (even with -m), so
    #    we must reset the main manifest after we sync.

    have_local = os.path.exists('.repo/local_manifests')
    if have_local:
        os.rename('.repo/local_manifests', '.repo/local_manifests.hide')

    args = ['repo', 'sync', '-l', '-m', pathname]
    child = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None)
    out, err = child.communicate()
    if child.returncode != 0:
        sys.stderr.write('Failed to sync\n')

    if have_local:
        os.rename('.repo/local_manifests.hide', '.repo/local_manifests')

    os.chdir('.repo/manifests')
    git_reset_hard(manifest_rev)
    os.chdir(cwd)

def find_last_good(rev_info):
    global print_all
    count = 0
    last_good = 1
    for r in rev_info:
        count += 1
        if r[0] == 'good':
            last_good = count

    return last_good


def print_rev_info(rev_info):
    global print_all

    if print_all:
        last_good = 1
    else:
        last_good = find_last_good(rev_info)

    if last_good > 1:
        print("Skipping print of all commits before last good")

    found = is_found(rev_info)
    if found:
        print("*" * 160)
        print("*" * 77 + " Done " + "*" * 77)
        print("*" * 160)
    print("%s %s %s %s" % ('\nStatus'.center(10), 'Hash'.center(45), 'Time'.center(25), 'Log'))
    count = 0
    current = -1
    for r in rev_info:
        count += 1
        if count < last_good:
            continue
        date = r[2].strftime("%Y-%m-%dT%H:%M:%S")
        if r[0] == 'current':
            current = count
        print("%s %s %s %s" % (r[0].center(10), r[1].center(45), date.center(25), r[3]))
        if not print_all and r[0] == 'bad':
            print("Skipping print of all commits after first bad")
            break
    if not found:
        print("%d commits, current is %d" % (count-last_good+1, current-last_good+1))

def usage():
    print("Usage:")
    print("  repo-bisect start yyyy-mm-ddThh:mm:ss yyyy-mm-ddThh:mm:ss")
    print("  repo-bisect start commit_hash_start commit_hash_end")
    print("  repo-bisect set_current commit_hash")
    print("  repo-bisect <good|bad|status|status_all>")
    sys.exit(1)

### Main code ###

argv = sys.argv[1:]
if len(argv) < 1:
    usage()

action = argv[0]
rc = -1
if action == 'start':
    print_all = True
    if len(argv) < 3:
        usage()
    start = git_check_and_convert_hash(argv[1])
    end = git_check_and_convert_hash(argv[2])
    if start is None or end is None:
        print("Could not find one of the start/end commit hashes")
        print("Try running 'repo sync'")
    else:
        rev_info = git_get_and_write_sorted_revisions(start, end)
        r = get_next_commit_date(rev_info, True)
        date = r[2].strftime("%Y-%m-%dT%H:%M:%S")
        repo_sync_to_date(date, r[1])
        print_rev_info(rev_info)
        rc = 0
elif action == 'good':
    rev_info = git_read_file_revisions()
    r = get_next_commit_date(rev_info, True)
    if r is None:
        print_rev_info(rev_info)
    else:
        date = r[2].strftime("%Y-%m-%dT%H:%M:%S")
        repo_sync_to_date(date, r[1])
        print_rev_info(rev_info)
    rc = 0
elif action == 'bad':
    rev_info = git_read_file_revisions()
    r = get_next_commit_date(rev_info, False)
    if r is None:
        print_rev_info(rev_info)
    else:
        date = r[2].strftime("%Y-%m-%dT%H:%M:%S")
        repo_sync_to_date(date, r[1])
        print_rev_info(rev_info)
    rc = 0
elif action == 'status':
    rev_info = git_read_file_revisions()
    print_rev_info(rev_info)
    rc = 0
elif action == 'status_all':
    print_all = True
    rev_info = git_read_file_revisions()
    print_rev_info(rev_info)
    rc = 0
elif action == 'set_current':
    c_hash = argv[1]
    rev_info = git_read_file_revisions()
    time = set_next_commit_date(rev_info, c_hash)
    if time is not None:
        date = time.strftime("%Y-%m-%dT%H:%M:%S")
        repo_sync_to_date(date, c_hash)
        rc = 0
        print_rev_info(rev_info)
else:
    usage()

sys.exit(rc)
