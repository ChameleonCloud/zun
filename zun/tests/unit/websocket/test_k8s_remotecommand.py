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

from zun.tests import base
from zun.websocket import k8s_remotecommand as rc


class K8sRemoteCommandTestCase(base.TestCase):

    def test_frame_from_client_prepends_stdin_channel(self):
        self.assertEqual(b"\x00hello", rc.frame_from_client(b"hello"))

    def test_frame_from_client_empty(self):
        self.assertEqual(b"\x00", rc.frame_from_client(b""))

    def test_payload_for_client_stdout(self):
        self.assertEqual(b"out", rc.payload_for_client(b"\x01out"))

    def test_payload_for_client_stderr(self):
        self.assertEqual(b"err", rc.payload_for_client(b"\x02err"))

    def test_payload_for_client_error_channel_dropped(self):
        self.assertIsNone(rc.payload_for_client(b'\x03{"status":"Success"}'))

    def test_payload_for_client_resize_channel_dropped(self):
        self.assertIsNone(rc.payload_for_client(b"\x04stuff"))

    def test_payload_for_client_empty_frame_dropped(self):
        self.assertIsNone(rc.payload_for_client(b""))

    def test_payload_for_client_channel_byte_only_dropped(self):
        # stdout channel but no payload -> nothing to forward
        self.assertIsNone(rc.payload_for_client(b"\x01"))
