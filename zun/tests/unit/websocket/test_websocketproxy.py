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
