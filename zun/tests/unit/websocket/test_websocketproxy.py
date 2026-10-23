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

from zun.common import exception
from zun.tests import base
from zun.websocket import k8s_remotecommand
from zun.websocket import relay
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


def _auth_handler():
    handler = object.__new__(websocketproxy.ZunProxyRequestHandlerBase)
    handler._verify_origin = mock.Mock()
    return handler


def _exec_instance(destroyed=True):
    exec_instance = mock.Mock(token="tok", exec_id="eid")
    exec_instance.destroy.return_value = destroyed
    return exec_instance


class ClaimExecTestCase(base.TestCase):

    def test_returns_claimed_exec_instance(self):
        handler = _auth_handler()
        exec_instance = _exec_instance()
        container = mock.Mock(exec_instances=[exec_instance])

        claimed = handler._claim_exec(container, "tok", "uuid", "eid")

        self.assertIs(exec_instance, claimed)
        exec_instance.destroy.assert_called_once()

    def test_wrong_token_is_refused_before_claiming(self):
        handler = _auth_handler()
        exec_instance = _exec_instance()
        container = mock.Mock(exec_instances=[exec_instance])

        self.assertRaises(exception.InvalidWebsocketToken,
                          handler._claim_exec,
                          container, "other", "uuid", "eid")
        exec_instance.destroy.assert_not_called()

    def test_bad_origin_is_refused_before_claiming(self):
        handler = _auth_handler()
        handler._verify_origin.side_effect = exception.ValidationError("x")
        exec_instance = _exec_instance()
        container = mock.Mock(exec_instances=[exec_instance])

        self.assertRaises(exception.ValidationError,
                          handler._claim_exec,
                          container, "tok", "uuid", "eid")
        exec_instance.destroy.assert_not_called()

    def test_exec_claimed_already_is_refused(self):
        handler = _auth_handler()
        container = mock.Mock(exec_instances=[_exec_instance(False)])

        self.assertRaises(exception.InvalidWebsocketToken,
                          handler._claim_exec,
                          container, "tok", "uuid", "eid")


class AuthorizeAttachTestCase(base.TestCase):

    def test_checks_origin_of_access_url(self):
        self.config(base_url="ws://proxy/", group="websocket_proxy")
        handler = _auth_handler()
        container = mock.Mock(websocket_token="tok", websocket_url="ws://c")

        handler._authorize_attach(container, "tok", "uuid")

        handler._verify_origin.assert_called_once_with(
            "ws://proxy/?token=tok&uuid=uuid")

    def test_wrong_token_is_refused(self):
        handler = _auth_handler()
        container = mock.Mock(websocket_token="tok", websocket_url="ws://c")

        self.assertRaises(exception.InvalidWebsocketToken,
                          handler._authorize_attach,
                          container, "other", "uuid")

    def test_container_without_websocket_url_is_refused(self):
        handler = _auth_handler()
        container = mock.Mock(websocket_token="tok", websocket_url=None)

        self.assertRaises(exception.InvalidWebsocketUrl,
                          handler._authorize_attach,
                          container, "tok", "uuid")


class ProxyNativeWebsocketTestCase(base.TestCase):

    def setUp(self):
        super(ProxyNativeWebsocketTestCase, self).setUp()
        self.handler = object.__new__(
            websocketproxy.ZunProxyRequestHandlerBase)
        self.handler.compute_api = mock.Mock()
        self.handler.request = mock.Mock(name="client",
                                         passthrough_protocol=None)
        self.handler.CClose = FakeCClose
        self.handler.do_websocket_proxy = mock.Mock()
        patcher = mock.patch.object(websocketproxy, "WebSocketClient")
        self.mock_client_cls = patcher.start()
        self.backend = self.mock_client_cls.return_value.ws
        self.addCleanup(patcher.stop)

    def test_options_failure_closes_client_with_1011(self):
        self.handler.compute_api.container_get_websocket_opts.side_effect = (
            Exception("rpc timeout"))

        exc = self.assertRaises(
            FakeCClose, self.handler._proxy_native_websocket,
            mock.Mock(), "wss://k8s/exec")

        self.assertEqual((1011, "Failed to get websocket options"), exc.args)

    @mock.patch.object(websocketproxy.relay, "Relay")
    def test_kubernetes_backend_is_relayed_merged(self, mock_relay):
        self.handler.compute_api.container_get_websocket_opts.return_value = {
            "channels": {"stdin": 0}}

        self.handler._proxy_native_websocket(mock.Mock(), "wss://k8s/exec")

        mock_relay.assert_called_once_with(
            self.handler.request, self.backend,
            to_backend=k8s_remotecommand.frame_from_client,
            to_client=k8s_remotecommand.payload_for_client)
        mock_relay.return_value.run.assert_called_once_with()
        self.handler.do_websocket_proxy.assert_not_called()

    @mock.patch.object(websocketproxy.relay, "Relay")
    def test_docker_backend_uses_the_select_loop(self, mock_relay):
        self.handler.compute_api.container_get_websocket_opts.return_value = {}

        self.handler._proxy_native_websocket(mock.Mock(), "ws://docker/attach")

        self.handler.do_websocket_proxy.assert_called_once_with(self.backend)
        mock_relay.assert_not_called()

    @mock.patch.object(websocketproxy.relay, "Relay")
    def test_passthrough_asks_kubernetes_for_the_same_protocol(
            self, mock_relay):
        self.handler.request.passthrough_protocol = "v5.channel.k8s.io"
        self.handler.compute_api.container_get_websocket_opts.return_value = {
            "channels": {"stdin": 0}}

        self.handler._proxy_native_websocket(mock.Mock(), "wss://k8s/exec")

        self.assertEqual(["v5.channel.k8s.io"],
                         self.mock_client_cls.call_args[1]["subprotocols"])
        mock_relay.assert_called_once_with(
            self.handler.request, self.backend,
            to_backend=relay.unchanged, to_client=relay.unchanged)

    def test_passthrough_to_docker_is_refused_before_connecting(self):
        self.handler.request.passthrough_protocol = "v5.channel.k8s.io"
        self.handler.compute_api.container_get_websocket_opts.return_value = {}

        exc = self.assertRaises(
            FakeCClose, self.handler._proxy_native_websocket,
            mock.Mock(), "ws://docker/attach")

        self.assertEqual(
            (1008, "v5.channel.k8s.io needs a Kubernetes backend"), exc.args)
        self.mock_client_cls.assert_not_called()


class DockerExecPassthroughTestCase(base.TestCase):

    @mock.patch.object(websocketproxy.docker, "APIClient")
    def test_passthrough_is_refused_before_starting_the_exec(
            self, mock_api_client):
        handler = object.__new__(websocketproxy.ZunProxyRequestHandlerBase)
        handler.request = mock.Mock(passthrough_protocol="v4.channel.k8s.io")
        handler.CClose = FakeCClose
        exec_instance = mock.Mock(url="tcp://docker:2375")

        exc = self.assertRaises(FakeCClose, handler._connect_exec,
                                mock.Mock(), exec_instance)

        self.assertEqual(
            (1008, "v4.channel.k8s.io needs a Kubernetes backend"), exc.args)
        mock_api_client.assert_not_called()
