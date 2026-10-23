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

"""Relay a client websocket to a Kubernetes exec websocket."""

import socket
import threading

from oslo_log import log as logging
import websocket
from websockify import websocket as websockify_websocket
from websockify import websockifyserver

LOG = logging.getLogger(__name__)

# How long to wait for the client to answer our close before cutting the
# connection.
CLOSE_WAIT = 2.0

# Kubernetes exec protocols a client can ask for, most preferred first.
PASSTHROUGH_PROTOCOLS = ("v5.channel.k8s.io", "v4.channel.k8s.io")


def unchanged(msg):
    return msg


class ZunWebSocket(websockifyserver.CompatibleWebSocket):
    """The client's websocket.

    In a relay, one thread reads from it and another writes to it.
    websockify also writes from inside recvmsg(): a pong for each ping, and
    the reply to a close. And recvmsg() closes the socket when the client
    goes away, or answers our close. So every write, and closing the
    socket, takes the same lock.

    Once relaying is set, a write that websockify couldn't finish in one
    try is retried before the lock is released. Otherwise the next write
    would find websockify still holding the unfinished message, and fail.
    """

    relaying = False

    # The Kubernetes exec protocol the client asked for, if any.
    passthrough_protocol = None

    def __init__(self, request_handler):
        super().__init__(request_handler)
        self._write_lock = threading.RLock()

    def select_subprotocol(self, protocols):
        """Pick a Kubernetes exec protocol if the client offers one.

        Names are matched without surrounding spaces, and the choice is
        returned exactly as it appears in protocols, because websockify
        requires one of those entries. Otherwise websockify's own choice is
        used.
        """
        offered = {protocol.strip(): protocol for protocol in protocols}
        for protocol in PASSTHROUGH_PROTOCOLS:
            if protocol in offered:
                self.passthrough_protocol = protocol
                return offered[protocol]
        return super().select_subprotocol(protocols)

    def _write(self, method, *args):
        with self._write_lock:
            while True:
                try:
                    return method(*args)
                except websockify_websocket.WebSocketWantWriteError:
                    if not self.relaying:
                        raise

    def sendmsg(self, msg):
        return self._write(super().sendmsg, msg)

    def ping(self, data=b''):
        return self._write(super().ping, data)

    def pong(self, data=b''):
        return self._write(super().pong, data)

    def shutdown(self, how, code=1000, reason=None):
        return self._write(super().shutdown, how, code, reason)

    def _flush(self):
        # recvmsg() calls this directly once the client has sent a close.
        with self._write_lock:
            return super()._flush()

    def _close(self):
        # recvmsg() calls this directly when the client goes away or answers
        # our close.
        with self._write_lock:
            return super()._close()


class Relay(object):
    """Relay messages between the client and a Kubernetes backend.

    Two pumps run at once, one for each direction. Each reads a message,
    passes it to its transform, and sends the result unless the transform
    returned None. A send blocks until the other side takes the message,
    so a slow reader slows down the sender instead of filling a queue.

    :param client: the client's ZunWebSocket.
    :param backend: the backend's websocket.WebSocket.
    :param to_backend: transform for messages from the client.
    :param to_client: transform for messages from the backend.
    """

    def __init__(self, client, backend, to_backend, to_client):
        self.client = client
        self.backend = backend
        self.to_backend = to_backend
        self.to_client = to_client
        self._client_closed = threading.Event()

    def run(self):
        """Relay until one side closes, then close the other side."""
        self.client.relaying = True
        up = threading.Thread(target=self._pump_to_backend)
        up.start()
        try:
            close = self._pump_to_client()
            if close:
                self._close_client(*close)
            up.join(CLOSE_WAIT)
            if up.is_alive():
                self._cut_client()
                up.join()
        finally:
            self.backend.shutdown()

    def _pump_to_backend(self):
        while True:
            try:
                msg = self.client.recvmsg()
            except websockify_websocket.WebSocketWantReadError:
                continue
            except Exception:
                LOG.debug("Lost the connection to the client", exc_info=True)
                msg = None
            if msg is None:
                self._client_closed.set()
                break
            data = self.to_backend(msg)
            if data is None:
                continue
            try:
                self.backend.send(data, websocket.ABNF.OPCODE_BINARY)
            except Exception:
                LOG.debug("Couldn't send to the backend", exc_info=True)
                break
        # Wakes up _pump_to_client if it is waiting on the backend.
        self.backend.abort()

    def _pump_to_client(self):
        """Relay messages from the backend until either side is gone.

        Returns the close code and reason to send to the client, or None if
        the client is gone.
        """
        while True:
            try:
                opcode, data = self.backend.recv_data()
            except Exception:
                if self._client_closed.is_set():
                    return None
                LOG.warning("Lost the connection to the backend",
                            exc_info=True)
                return 1011, "Backend connection failed"
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                return 1000, "Target closed"
            msg = self.to_client(data)
            if msg is None:
                continue
            try:
                self.client.sendmsg(msg)
            except Exception:
                LOG.debug("Stopped relaying to the client", exc_info=True)
                return None

    def _close_client(self, code, reason):
        try:
            self.client.shutdown(socket.SHUT_RDWR, code, reason)
        except Exception:
            LOG.debug("Couldn't send a close to the client", exc_info=True)

    def _cut_client(self):
        # Ends a recvmsg() that is waiting on a client that never closed.
        # recvmsg() may close the socket at any moment, so read it once.
        sock = self.client.socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
