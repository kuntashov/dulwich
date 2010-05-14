# test_client.py -- Compatibilty tests for git client.
# Copyright (C) 2010 Google, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) any later version of
# the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.

"""Compatibilty tests between the Dulwich client and the cgit server."""

import os
import shutil
import signal
import tempfile

from dulwich import client
from dulwich import errors
from dulwich import file
from dulwich import index
from dulwich import protocol
from dulwich import object_store
from dulwich import objects
from dulwich import repo
from dulwich.tests import (
    TestSkipped,
    )

from utils import (
    CompatTestCase,
    check_for_daemon,
    import_repo_to_dir,
    run_git,
    )

class DulwichClientTest(CompatTestCase):
    """Tests for client/server compatibility."""

    def setUp(self):
        if check_for_daemon(limit=1):
            raise TestSkipped('git-daemon was already running on port %s' %
                              protocol.TCP_GIT_PORT)
        CompatTestCase.setUp(self)
        fd, self.pidfile = tempfile.mkstemp(prefix='dulwich-test-git-client',
                                            suffix=".pid")
        os.fdopen(fd).close()
        self.gitroot = os.path.dirname(import_repo_to_dir('server_new.export'))
        dest = os.path.join(self.gitroot, 'dest')
        file.ensure_dir_exists(dest)
        run_git(['init', '--bare'], cwd=dest)
        run_git(
            ['daemon', '--verbose', '--export-all',
             '--pid-file=%s' % self.pidfile, '--base-path=%s' % self.gitroot,
             '--detach', '--reuseaddr', '--enable=receive-pack',
             '--listen=localhost', self.gitroot], cwd=self.gitroot)
        if not check_for_daemon():
            raise TestSkipped('git-daemon failed to start')

    def tearDown(self):
        CompatTestCase.tearDown(self)
        try:
            os.kill(int(open(self.pidfile).read().strip()), signal.SIGKILL)
            os.unlink(self.pidfile)
        except (OSError, IOError):
            pass
        shutil.rmtree(self.gitroot)

    def assertDestEqualsSrc(self):
        src = repo.Repo(os.path.join(self.gitroot, 'server_new.export'))
        dest = repo.Repo(os.path.join(self.gitroot, 'dest'))
        self.assertReposEqual(src, dest)

    def test_send_pack(self):
        c = client.TCPGitClient('localhost')
        srcpath = os.path.join(self.gitroot, 'server_new.export')
        src = repo.Repo(srcpath)
        sendrefs = dict(src.get_refs())
        del sendrefs['HEAD']
        c.send_pack('/dest', lambda _: sendrefs,
                    src.object_store.generate_pack_contents)
        dest = repo.Repo(os.path.join(self.gitroot, 'dest'))
        self.assertReposEqual(src, dest)

    def test_send_without_report_status(self):
        c = client.TCPGitClient('localhost')
        c._send_capabilities.remove('report-status')
        srcpath = os.path.join(self.gitroot, 'server_new.export')
        src = repo.Repo(srcpath)
        sendrefs = dict(src.get_refs())
        del sendrefs['HEAD']
        c.send_pack('/dest', lambda _: sendrefs,
                    src.object_store.generate_pack_contents)
        self.assertDestEqualsSrc()

    def disable_ff_and_make_dummy_commit(self):
        # disable non-fast-forward pushes to the server
        dest = repo.Repo(os.path.join(self.gitroot, 'dest'))
        run_git(['config', 'receive.denyNonFastForwards', 'true'], cwd=dest.path)
        b = objects.Blob.from_string('hi')
        dest.object_store.add_object(b)
        t = index.commit_tree(dest.object_store, [('hi', b.id, 0100644)])
        c = objects.Commit()
        c.author = c.committer = 'Foo Bar <foo@example.com>'
        c.author_time = c.commit_time = 0
        c.author_timezone = c.commit_timezone = 0
        c.message = 'hi'
        c.tree = t
        dest.object_store.add_object(c)
        return dest, c.id

    def compute_send(self):
        srcpath = os.path.join(self.gitroot, 'server_new.export')
        src = repo.Repo(srcpath)
        sendrefs = dict(src.get_refs())
        del sendrefs['HEAD']
        return sendrefs, src.object_store.generate_pack_contents

    def test_send_pack_one_error(self):
        dest, dummy_commit = self.disable_ff_and_make_dummy_commit()
        dest.refs['refs/heads/master'] = dummy_commit
        sendrefs, gen_pack = self.compute_send()
        c = client.TCPGitClient('localhost')
        try:
            c.send_pack('/dest', lambda _: sendrefs, gen_pack)
        except errors.UpdateRefsError, e:
            self.assertEqual('refs/heads/master failed to update', str(e))
            self.assertEqual({'refs/heads/branch': 'ok',
                              'refs/heads/master': 'non-fast-forward'},
                             e.ref_status)

    def test_send_pack_multiple_errors(self):
        dest, dummy = self.disable_ff_and_make_dummy_commit()
        # set up for two non-ff errors
        dest.refs['refs/heads/branch'] = dest.refs['refs/heads/master'] = dummy
        sendrefs, gen_pack = self.compute_send()
        c = client.TCPGitClient('localhost')
        try:
            c.send_pack('/dest', lambda _: sendrefs, gen_pack)
        except errors.UpdateRefsError, e:
            self.assertEqual('refs/heads/branch, refs/heads/master failed to '
                             'update', str(e))
            self.assertEqual({'refs/heads/branch': 'non-fast-forward',
                              'refs/heads/master': 'non-fast-forward'},
                             e.ref_status)

    def test_fetch_pack(self):
        c = client.TCPGitClient('localhost')
        dest = repo.Repo(os.path.join(self.gitroot, 'dest'))
        refs = c.fetch('/server_new.export', dest)
        map(lambda r: dest.refs.set_if_equals(r[0], None, r[1]), refs.items())
        self.assertDestEqualsSrc()

    def test_incremental_fetch_pack(self):
        self.test_fetch_pack()
        dest, dummy = self.disable_ff_and_make_dummy_commit()
        dest.refs['refs/heads/master'] = dummy
        c = client.TCPGitClient('localhost')
        dest = repo.Repo(os.path.join(self.gitroot, 'server_new.export'))
        refs = c.fetch('/dest', dest)
        map(lambda r: dest.refs.set_if_equals(r[0], None, r[1]), refs.items())
        self.assertDestEqualsSrc()
