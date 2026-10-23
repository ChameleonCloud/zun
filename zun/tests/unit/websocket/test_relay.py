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

import queue
import threading
from unittest import mock

import websocket
from websockify import websocket as websockify_websocket
from websockify import websockifyserver

from zun.tests import base
from zun.websocket import k8s_remotecommand
from zun.websocket import relay

BINARY = websocket.ABNF.OPCODE_BINARY
CLOSE = (websocket.ABNF.OPCODE_CLOSE, b"")


class FakeClient(object):
    """Stands in for the client's ZunWebSocket.

    recvmsg() returns the queued messages, then waits. None means the
    client closed. By default the client answers our close with its own.
    """

    def __init__(self, messages=(), answers_close=True):
        self.inbox = queue.Queue()
        for msg in messages:
            self.inbox.put(msg)
        self.answers_close = answers_close
        self.sent = []
        self.pings = 0
        self.close = None
        self.socket = mock.Mock()
        self.socket.shutdown.side_effect = lambda how: self.inbox.put(None)

    def recvmsg(self):
        return self.inbox.get(timeout=5)

    def sendmsg(self, msg):
        self.sent.append(msg)
        return len(msg)

    def ping(self):
        self.pings += 1

    def shutdown(self, how, code=1000, reason=None):
        self.close = (code, reason)
        if self.answers_close:
            self.inbox.put(None)


class FakeBackend(object):
    """Stands in for the backend's websocket.WebSocket.

    recv_data() returns the queued (opcode, data) pairs, then waits. A
    queued exception is raised instead.
    """

    def __init__(self, messages=()):
        self.inbox = queue.Queue()
        for msg in messages:
            self.inbox.put(msg)
        self.sent = []
        self.closed = False

    def recv_data(self):
        item = self.inbox.get(timeout=5)
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, data, opcode):
        self.sent.append((data, opcode))

    def abort(self):
        self.inbox.put(ConnectionAbortedError())

    def shutdown(self):
        self.closed = True


def _merged_relay(client, backend, heartbeat=None):
    return relay.Relay(client, backend,
                       to_backend=k8s_remotecommand.frame_from_client,
                       to_client=k8s_remotecommand.payload_for_client,
                       heartbeat=heartbeat)


class MergedRelayTestCase(base.TestCase):

    def test_client_messages_go_to_backend_on_stdin_as_binary(self):
        client = FakeClient([b"ls\n", None])
        backend = FakeBackend()

        _merged_relay(client, backend).run()

        self.assertEqual([(b"\x00ls\n", BINARY)], backend.sent)

    def test_only_stdout_and_stderr_payloads_reach_the_client(self):
        client = FakeClient()
        backend = FakeBackend([
            (BINARY, b"\x01"),
            (BINARY, b"\x01out"),
            (BINARY, b"\x02err"),
            (BINARY, b'\x03{"status":"Success"}'),
            CLOSE,
        ])

        _merged_relay(client, backend).run()

        self.assertEqual([b"out", b"err"], client.sent)


class PassthroughRelayTestCase(base.TestCase):

    def test_messages_pass_unchanged_both_ways(self):
        client = FakeClient([b"\x00ls\n", b"\xff\x00"])
        backend = FakeBackend([
            (BINARY, b"\x01out"),
            (BINARY, b'\x03{"status":"Success"}'),
            CLOSE,
        ])
        session = relay.Relay(client, backend, to_backend=relay.unchanged,
                              to_client=relay.unchanged)

        session.run()

        self.assertEqual([b"\x01out", b'\x03{"status":"Success"}'],
                         client.sent)
        self.assertEqual((1000, "Target closed"), client.close)


class TeardownTestCase(base.TestCase):

    def test_backend_close_closes_client(self):
        client = FakeClient()
        backend = FakeBackend([CLOSE])

        _merged_relay(client, backend).run()

        self.assertEqual((1000, "Target closed"), client.close)
        self.assertTrue(backend.closed)

    def test_backend_failure_closes_client_with_1011(self):
        client = FakeClient()
        backend = FakeBackend([ConnectionResetError()])

        _merged_relay(client, backend).run()

        self.assertEqual((1011, "Backend connection failed"), client.close)
        self.assertTrue(backend.closed)

    def test_client_close_ends_session_without_closing_client_again(self):
        client = FakeClient([None])
        backend = FakeBackend()

        _merged_relay(client, backend).run()

        self.assertIsNone(client.close)
        self.assertTrue(backend.closed)

    @mock.patch.object(relay, "CLOSE_WAIT", 0.01)
    def test_client_that_never_answers_close_is_cut(self):
        client = FakeClient(answers_close=False)
        backend = FakeBackend([CLOSE])

        _merged_relay(client, backend).run()

        client.socket.shutdown.assert_called_once()
        self.assertTrue(backend.closed)


class BlockedClientTestCase(base.TestCase):
    """The client stops reading: its first sendmsg() doesn't return."""

    def setUp(self):
        super(BlockedClientTestCase, self).setUp()
        self.client = FakeClient()
        self.sending = threading.Event()
        self.release = threading.Event()

        def blocked_sendmsg(msg):
            self.sending.set()
            self.release.wait(5)
            return len(msg)

        self.client.sendmsg = blocked_sendmsg
        self.backend = FakeBackend(
            [(BINARY, b"\x01one"), (BINARY, b"\x01two")])
        self.backend.recv_data = mock.Mock(wraps=self.backend.recv_data)
        self.thread = threading.Thread(
            target=_merged_relay(self.client, self.backend).run)
        self.thread.start()
        self.assertTrue(self.sending.wait(5))

    def tearDown(self):
        self.release.set()
        self.client.inbox.put(None)
        self.thread.join(5)
        super(BlockedClientTestCase, self).tearDown()

    def test_backend_is_not_read_while_client_is_blocked(self):
        self.assertEqual(1, self.backend.recv_data.call_count)

    def test_stdin_keeps_flowing_while_client_is_blocked(self):
        self.client.inbox.put(b"more input")

        for _ in range(500):
            if self.backend.sent:
                break
            self.release.wait(0.01)

        self.assertEqual([(b"\x00more input", BINARY)], self.backend.sent)


class HeartbeatTestCase(base.TestCase):

    def test_client_is_pinged_while_session_is_idle(self):
        client = FakeClient()
        session = _merged_relay(client, FakeBackend(), heartbeat=0.01)
        thread = threading.Thread(target=session.run)
        thread.start()

        for _ in range(500):
            if client.pings >= 2:
                break
            threading.Event().wait(0.01)
        client.inbox.put(None)
        thread.join(5)

        self.assertGreaterEqual(client.pings, 2)

    def test_no_heartbeat_means_no_pings(self):
        client = FakeClient([None])

        _merged_relay(client, FakeBackend()).run()

        self.assertEqual(0, client.pings)


class ZunWebSocketTestCase(base.TestCase):

    def _socket(self, relaying):
        sock = relay.ZunWebSocket(mock.Mock())
        sock.relaying = relaying
        return sock

    @mock.patch.object(websockifyserver.CompatibleWebSocket, "sendmsg")
    def test_relaying_retries_unfinished_writes(self, mock_sendmsg):
        mock_sendmsg.side_effect = [
            websockify_websocket.WebSocketWantWriteError(), 3]

        self.assertEqual(3, self._socket(True).sendmsg(b"abc"))
        self.assertEqual(2, mock_sendmsg.call_count)

    @mock.patch.object(websockifyserver.CompatibleWebSocket, "sendmsg")
    def test_unfinished_writes_raise_when_not_relaying(self, mock_sendmsg):
        mock_sendmsg.side_effect = (
            websockify_websocket.WebSocketWantWriteError())

        self.assertRaises(websockify_websocket.WebSocketWantWriteError,
                          self._socket(False).sendmsg, b"abc")


class SelectSubprotocolTestCase(base.TestCase):
    """The names arrive as websockify splits the header: on ",", unstripped."""

    def _select(self, header):
        sock = relay.ZunWebSocket(mock.Mock())
        return sock.select_subprotocol(header.split(",")), sock

    def test_released_zunclient_gets_binary(self):
        chosen, sock = self._select("binary,base64")

        self.assertEqual(("binary", None), (chosen, sock.passthrough_protocol))

    def test_no_offer_gets_no_protocol(self):
        chosen, sock = self._select("")

        self.assertEqual(("", None), (chosen, sock.passthrough_protocol))

    def test_v5_is_chosen(self):
        chosen, sock = self._select("v5.channel.k8s.io")

        self.assertEqual(("v5.channel.k8s.io", "v5.channel.k8s.io"),
                         (chosen, sock.passthrough_protocol))

    def test_choice_keeps_the_clients_spelling(self):
        chosen, sock = self._select("foo, v5.channel.k8s.io")

        self.assertEqual((" v5.channel.k8s.io", "v5.channel.k8s.io"),
                         (chosen, sock.passthrough_protocol))

    def test_choice_is_an_entry_of_an_already_stripped_list(self):
        sock = relay.ZunWebSocket(mock.Mock())

        chosen = sock.select_subprotocol(["foo", "v5.channel.k8s.io"])

        self.assertEqual("v5.channel.k8s.io", chosen)

    def test_v5_is_preferred_to_v4(self):
        chosen, sock = self._select("v4.channel.k8s.io,v5.channel.k8s.io")

        self.assertEqual(("v5.channel.k8s.io", "v5.channel.k8s.io"),
                         (chosen, sock.passthrough_protocol))
