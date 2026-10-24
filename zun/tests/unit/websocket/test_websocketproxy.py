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


class SelectSubprotocolTestCase(base.TestCase):

    def _select(self, header):
        ws = websocketproxy.ZunWebSocket(mock.Mock(name="request_handler"))
        return ws.select_subprotocol(header.split(','))

    def test_v5_is_chosen(self):
        self.assertEqual('v5.channel.k8s.io',
                         self._select('v5.channel.k8s.io'))

    def test_v5_is_preferred_to_v4_and_returned_with_its_space(self):
        self.assertEqual(' v5.channel.k8s.io',
                         self._select('v4.channel.k8s.io, v5.channel.k8s.io'))

    def test_released_zunclient_gets_binary(self):
        self.assertEqual('binary', self._select('binary, base64'))

    def test_no_offer_gets_no_protocol(self):
        self.assertEqual('', self._select(''))


@mock.patch.object(websocketproxy, "WebSocketClient")
class ProxyNativeWebsocketTestCase(base.TestCase):

    def _handler(self, protocol, ws_opts):
        handler = object.__new__(websocketproxy.ZunProxyRequestHandlerBase)
        handler.compute_api = mock.Mock()
        handler.compute_api.container_get_websocket_opts.return_value = (
            ws_opts)
        handler.request = mock.Mock(protocol=protocol)
        handler.CClose = FakeCClose
        handler.do_websocket_proxy = mock.Mock()
        return handler

    @mock.patch.object(websocketproxy, "_admin_context")
    def test_k8s_passthrough_asks_for_the_same_protocol(self, mock_ctx,
                                                        mock_client):
        handler = self._handler(' v5.channel.k8s.io', {"channels": True})

        handler._proxy_native_websocket(mock.Mock(), "wss://k8s/exec")

        mock_client.assert_called_once_with(
            host_url="wss://k8s/exec", escape="~", close_wait=0.5,
            subprotocols=['v5.channel.k8s.io'])
        handler.do_websocket_proxy.assert_called_once_with(
            mock_client.return_value.ws, framed=False, binary=True)

    @mock.patch.object(websocketproxy, "_admin_context")
    def test_docker_passthrough_is_refused_before_connecting(self, mock_ctx,
                                                             mock_client):
        handler = self._handler('v5.channel.k8s.io', {})

        e = self.assertRaises(FakeCClose, handler._proxy_native_websocket,
                              mock.Mock(), "ws://docker/attach")

        self.assertEqual(
            (1008, "Exec protocol needs a Kubernetes container"), e.args)
        mock_client.assert_not_called()
        handler.do_websocket_proxy.assert_not_called()

    @mock.patch.object(websocketproxy, "_admin_context")
    def test_old_client_on_k8s_is_merged(self, mock_ctx, mock_client):
        handler = self._handler('binary', {"channels": True})

        handler._proxy_native_websocket(mock.Mock(), "wss://k8s/exec")

        mock_client.assert_called_once_with(
            host_url="wss://k8s/exec", escape="~", close_wait=0.5)
        handler.do_websocket_proxy.assert_called_once_with(
            mock_client.return_value.ws, framed=True, binary=True)

    @mock.patch.object(websocketproxy, "_admin_context")
    def test_docker_session_is_unchanged(self, mock_ctx, mock_client):
        handler = self._handler('binary', {})

        handler._proxy_native_websocket(mock.Mock(), "ws://docker/attach")

        mock_client.assert_called_once_with(
            host_url="ws://docker/attach", escape="~", close_wait=0.5)
        handler.do_websocket_proxy.assert_called_once_with(
            mock_client.return_value.ws, framed=False, binary=False)


class SendBufferTestCase(base.TestCase):

    def test_sends_with_the_sessions_opcode(self):
        handler = _make_handler()
        handler.opcode = websocketproxy.websocket.ABNF.OPCODE_BINARY
        target = mock.Mock(name="target")
        target.send.return_value = 4

        self.assertIsNone(handler._send_buffer(b"\x00abc", target))

        target.send.assert_called_once_with(
            b"\x00abc", websocketproxy.websocket.ABNF.OPCODE_BINARY)
