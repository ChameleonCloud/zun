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

from unittest import mock
from urllib import parse as urlparse

from zun.common import exception
from zun.tests import base
from zun.websocket import websocketproxy


class FakeCClose(Exception):
    """Stand-in for websockify's CClose so tests don't need the real server."""


def _make_handler(cqueue=None, c_pend=False):
    """Build a bare handler without running websockify's socket __init__.

    _handle_ins_outs / _drain_cqueue_to_client live on the plain base class
    and only touch instance attributes we set here, so we can exercise them
    without a real socket.
    """
    handler = object.__new__(websocketproxy.ZunProxyRequestHandlerBase)
    handler.cqueue = [] if cqueue is None else cqueue
    handler.tqueue = []
    handler.c_pend = c_pend
    handler.framed = False
    handler.request = mock.Mock(name="client_request")
    handler.server = mock.Mock(target_host="host", target_port=1234)
    handler.send_frames = mock.Mock(return_value=False)
    handler.recv_frames = mock.Mock(return_value=([], False))
    handler.msg = mock.Mock()
    handler.CClose = FakeCClose
    return handler


class DrainCqueueTestCase(base.TestCase):

    def test_flushes_queued_tail(self):
        # The bug: queued output must be sent before close, not discarded.
        handler = _make_handler(cqueue=[b"tail"])

        handler._drain_cqueue_to_client()

        handler.send_frames.assert_called_once_with([b"tail"])
        self.assertEqual([], handler.cqueue)
        self.assertFalse(handler.c_pend)

    def test_nothing_queued_does_not_call_send(self):
        handler = _make_handler(cqueue=[], c_pend=False)

        handler._drain_cqueue_to_client()

        handler.send_frames.assert_not_called()

    @mock.patch.object(websocketproxy, "select")
    def test_retries_on_backpressure_then_succeeds(self, mock_select):
        # First send is backpressured (True), client then becomes writable,
        # second send drains fully (False).
        handler = _make_handler(cqueue=[b"a"])
        handler.send_frames.side_effect = [True, False]
        mock_select.select.return_value = ([], [handler.request], [])

        handler._drain_cqueue_to_client()

        self.assertEqual(2, handler.send_frames.call_count)
        self.assertFalse(handler.c_pend)
        mock_select.select.assert_called_once()

    @mock.patch.object(websocketproxy, "time")
    @mock.patch.object(websocketproxy, "select")
    def test_dead_client_does_not_hang(self, mock_select, mock_time):
        # send_frames never drains and the client never becomes writable;
        # the loop must give up rather than spin forever.
        handler = _make_handler(cqueue=[b"a"])
        handler.send_frames.return_value = True
        mock_time.time.return_value = 1000.0  # constant clock -> bounded loop
        mock_select.select.return_value = ([], [], [])  # not writable

        handler._drain_cqueue_to_client(timeout=2.0)

        # One send attempt, one select that reports not-writable, then break.
        handler.send_frames.assert_called_once()
        mock_select.select.assert_called_once()
        self.assertTrue(handler.c_pend)


class HandleInsOutsEofTestCase(base.TestCase):

    def _eof_handler(self, cqueue):
        handler = _make_handler(cqueue=cqueue)
        target = mock.Mock(name="target")
        target.recv.return_value = b""  # EOF
        return handler, target

    def test_eof_flushes_then_closes(self):
        handler, target = self._eof_handler(cqueue=[b"final"])

        self.assertRaises(
            FakeCClose,
            handler._handle_ins_outs,
            target, [target], [],
        )

        # Tail was flushed to the client before the close was raised.
        handler.send_frames.assert_called_once_with([b"final"])
        self.assertEqual([], handler.cqueue)

    def test_eof_logs_target_closed_not_client(self):
        handler, target = self._eof_handler(cqueue=[])

        self.assertRaises(
            FakeCClose,
            handler._handle_ins_outs,
            target, [target], [],
        )

        # Regression: the EOF branch used to log "Client closed connection".
        logged = handler.msg.call_args[0][0]
        self.assertIn("Target closed connection", logged)
        self.assertNotIn("Client closed", logged)


V5 = "v5.channel.k8s.io"
V4 = "v4.channel.k8s.io"
# A non-tty exec URL as the k8s driver builds it.
EXEC_URL = ("wss://k8s:6443/api/v1/namespaces/p/pods/pod/exec?"
            "command=sh&command=-c&command=tar+xf+-+-C+%2Ftmp"
            "&tty=false&stdin=false&stdout=true&stderr=true")


class PassthroughProtocolTestCase(base.TestCase):

    def test_prefers_v5(self):
        self.assertEqual(
            V5, websocketproxy._passthrough_protocol([V4, V5]))

    def test_falls_back_to_v4(self):
        self.assertEqual(
            V4, websocketproxy._passthrough_protocol(["binary", V4]))

    def test_keeps_the_clients_spelling(self):
        # websockify splits "v4..., v5..." into "v4..." and " v5...".
        self.assertEqual(
            " " + V5, websocketproxy._passthrough_protocol([V4, " " + V5]))

    def test_merged_mode_for_other_protocols(self):
        for offered in (["binary", " base64"], [""],
                        ["v3.channel.k8s.io"], ["base64.channel.k8s.io"]):
            self.assertIsNone(websocketproxy._passthrough_protocol(offered),
                              offered)


class ZunWebSocketTestCase(base.TestCase):

    def _socket(self):
        sock = object.__new__(websocketproxy.ZunWebSocket)
        sock.request_handler = mock.Mock(passthrough_protocol=None)
        return sock

    def test_answers_with_the_relayed_version(self):
        sock = self._socket()

        chosen = sock.select_subprotocol([V4, " " + V5])

        self.assertEqual(" " + V5, chosen)
        self.assertEqual(V5, sock.request_handler.passthrough_protocol)

    def test_merged_mode_answers_as_websockify(self):
        sock = self._socket()

        self.assertEqual("binary",
                         sock.select_subprotocol(["binary", " base64"]))
        self.assertIsNone(sock.request_handler.passthrough_protocol)

    def test_installed_on_the_request_handler(self):
        self.assertIs(websocketproxy.ZunWebSocket,
                      websocketproxy.ZunProxyRequestHandler.SocketClass)


class WithStdinTestCase(base.TestCase):

    def test_turns_on_stdin(self):
        url = websocketproxy._with_stdin(EXEC_URL)

        parts = urlparse.urlsplit(url)
        params = urlparse.parse_qsl(parts.query)
        self.assertEqual(urlparse.urlsplit(EXEC_URL).path, parts.path)
        self.assertEqual(
            [("command", "sh"), ("command", "-c"),
             ("command", "tar xf - -C /tmp")],
            [p for p in params if p[0] == "command"])
        self.assertIn(("stdin", "true"), params)
        self.assertNotIn(("stdin", "false"), params)
        self.assertIn(("tty", "false"), params)

    def test_leaves_a_terminal_alone(self):
        url = ("wss://k8s:6443/api/v1/namespaces/p/pods/pod/exec?"
               "command=/bin/sh&stderr=true&stdout=true&stdin=true&tty=true")

        self.assertEqual(url, websocketproxy._with_stdin(url))


class ProxyNativeWebsocketTestCase(base.TestCase):

    def setUp(self):
        super(ProxyNativeWebsocketTestCase, self).setUp()
        self.handler = _make_handler()
        self.handler.compute_api = mock.Mock()
        self.ws_opts = {"channels": True}
        self.handler.compute_api.container_get_websocket_opts.return_value = (
            self.ws_opts)
        self.handler.do_websocket_proxy = mock.Mock()
        self.handler.vmsg = mock.Mock()
        p = mock.patch.object(websocketproxy, "WebSocketClient")
        self.client_cls = p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(websocketproxy, "_admin_context")
        p.start()
        self.addCleanup(p.stop)

    def _client_kwargs(self):
        self.client_cls.assert_called_once()
        return self.client_cls.call_args[1]

    def test_passthrough_asks_the_backend_for_the_same_version(self):
        self.handler.passthrough_protocol = V5

        self.handler._proxy_native_websocket(mock.Mock(), EXEC_URL)

        kwargs = self._client_kwargs()
        self.assertEqual([V5], kwargs["subprotocols"])
        self.assertEqual(EXEC_URL, kwargs["host_url"])
        self.handler.do_websocket_proxy.assert_called_once_with(
            self.client_cls.return_value.ws, framed=True, passthrough=True)

    def test_passthrough_turns_on_stdin(self):
        self.handler.passthrough_protocol = V5

        self.handler._proxy_native_websocket(mock.Mock(), EXEC_URL,
                                             stdin=True)

        query = urlparse.urlsplit(self._client_kwargs()["host_url"]).query
        self.assertIn(("stdin", "true"), urlparse.parse_qsl(query))

    def test_merged_mode_is_unchanged(self):
        # stdin is ignored, and no protocol is requested from the backend.
        self.handler._proxy_native_websocket(mock.Mock(), EXEC_URL,
                                             stdin=True)

        kwargs = self._client_kwargs()
        self.assertEqual(EXEC_URL, kwargs["host_url"])
        self.assertNotIn("subprotocols", kwargs)
        self.handler.do_websocket_proxy.assert_called_once_with(
            self.client_cls.return_value.ws, framed=True, passthrough=False)

    def test_unframed_backend_refused_before_connecting(self):
        self.handler.passthrough_protocol = V5
        self.ws_opts.clear()

        self.assertRaises(exception.ConnectionFailed,
                          self.handler._proxy_native_websocket,
                          mock.Mock(), EXEC_URL)
        self.client_cls.assert_not_called()


class DockerExecPassthroughTestCase(base.TestCase):

    @mock.patch.object(websocketproxy, "docker")
    @mock.patch.object(websocketproxy, "_admin_context")
    def test_refused_before_the_exec_starts(self, mock_ctx, mock_docker):
        handler = _make_handler()
        handler.passthrough_protocol = V5
        handler.headers = {"Host": "proxy"}
        exec_instance = mock.Mock(token="t", exec_id="e",
                                  url="tcp://docker:2375")
        exec_instance.destroy.return_value = True
        container = mock.Mock(exec_instances=[exec_instance])

        self.assertRaises(exception.ConnectionFailed,
                          handler._new_exec_client,
                          container, "t", "uuid", "e")
        mock_docker.APIClient.assert_not_called()


class PassthroughRelayTestCase(base.TestCase):

    def _handler(self, passthrough):
        handler = _make_handler()
        handler.framed = True
        handler.passthrough = passthrough
        return handler

    def test_client_messages_sent_unchanged_as_binary(self):
        handler = self._handler(passthrough=True)
        handler.tqueue = [b"\x00tar bytes", b"\xff\x00"]
        target = mock.Mock(name="target")

        handler._handle_ins_outs(target, [], [target])

        self.assertEqual([mock.call(b"\x00tar bytes"), mock.call(b"\xff\x00")],
                         target.send_binary.call_args_list)
        target.send.assert_not_called()
        self.assertEqual([], handler.tqueue)

    def test_backend_messages_reach_the_client_unchanged(self):
        handler = self._handler(passthrough=True)
        target = mock.Mock(name="target")
        status = b'\x03{"status":"Success"}'
        target.recv.return_value = status

        handler._handle_ins_outs(target, [target], [])

        self.assertEqual([status], handler.cqueue)

    def test_merged_mode_still_flattens_channels(self):
        handler = self._handler(passthrough=False)
        target = mock.Mock(name="target")
        target.recv.side_effect = [b"\x01out", b'\x03{"status":"Success"}']

        handler._handle_ins_outs(target, [target], [])
        handler._handle_ins_outs(target, [target], [])

        self.assertEqual([b"out"], handler.cqueue)


class NewWebsocketClientStdinTestCase(base.TestCase):

    UUID = "6f1c2a9e-1111-2222-3333-444455556666"

    def _connect(self, path):
        handler = _make_handler()
        handler._new_exec_client = mock.Mock()
        handler.path = path
        with mock.patch("eventlet.hubs.use_hub"), \
                mock.patch.object(websocketproxy.objects.Container,
                                  "get_by_uuid") as get_by_uuid, \
                mock.patch.object(websocketproxy, "_admin_context"):
            handler.new_websocket_client()
        return handler, get_by_uuid.return_value

    def test_stdin_flag_reaches_the_exec(self):
        handler, container = self._connect(
            "/?token=t&uuid=%s&exec_id=e&stdin=1" % self.UUID)

        handler._new_exec_client.assert_called_once_with(
            container, "t", self.UUID, "e", stdin=True)

    def test_no_stdin_by_default(self):
        handler, container = self._connect(
            "/?token=t&uuid=%s&exec_id=e" % self.UUID)

        handler._new_exec_client.assert_called_once_with(
            container, "t", self.UUID, "e", stdin=False)
