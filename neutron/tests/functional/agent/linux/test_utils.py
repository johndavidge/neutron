# Copyright 2015 Red Hat, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import eventlet
import testtools

from neutron.agent.linux import async_process
from neutron.agent.linux import utils
from neutron.tests.functional.agent.linux import test_async_process


class TestPIDHelpers(test_async_process.AsyncProcessTestFramework):
    def test_get_cmdline_from_pid_and_pid_invoked_with_cmdline(self):
        cmd = ['tail', '-f', self.test_file_path]
        proc = async_process.AsyncProcess(cmd)
        proc.start(blocking=True)
        self.addCleanup(proc.stop)

        pid = proc.pid
        self.assertEqual(cmd, utils.get_cmdline_from_pid(pid))
        self.assertTrue(utils.pid_invoked_with_cmdline(pid, cmd))
        self.assertEqual([], utils.get_cmdline_from_pid(-1))

    def test_wait_until_true_predicate_succeeds(self):
        utils.wait_until_true(lambda: True)

    def test_wait_until_true_predicate_fails(self):
        with testtools.ExpectedException(eventlet.timeout.Timeout):
            utils.wait_until_true(lambda: False, 2)
