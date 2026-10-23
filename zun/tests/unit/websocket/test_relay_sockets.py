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

"""Relay tests over real sockets, with real websockify and websocket-client.

Each test connects three parties with socket.socketpair():

    user (websockify, client side) <-> proxy (ZunWebSocket)
    proxy (websocket-client)       <-> k8s (websockify, server side)
"""

import os
import socket
import struct
import threading
import time
from unittest import mock

import websocket
from websockify import websocket as websockify_websocket

from zun.tests import base
from zun.websocket import k8s_remotecommand
from zun.websocket import relay

HEADERS = {
    "upgrade": "websocket",
    "Sec-WebSocket-Version": "13",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
}
STATUS = b'\x03{"metadata":{},"status":"Success"}'


def _retry(method, *args):
    """Call a websockify method until it has sent everything."""
    while True:
        try:
            return method(*args)
        except websockify_websocket.WebSocketWantWriteError:
            continue


def _recv_all(ws, delay=0):
    """Read messages from a websockify WebSocket until it closes."""
    messages = []
    while True:
        try:
            msg = ws.recvmsg()
        except websockify_websocket.WebSocketWantReadError:
            continue
        if msg is None:
            return messages
        messages.append(msg)
        time.sleep(delay)


def _proxy_side(sock):
    """The client's websocket as zun-wsproxy has it after the handshake."""
    proxy = relay.ZunWebSocket(mock.Mock())
    proxy.accept(sock, HEADERS)
    return proxy


def _peer(sock, client):
    peer = websockify_websocket.WebSocket()
    peer.client = client
    peer.socket = sock
    return peer


def _backend(sock):
    """websocket-client's WebSocket, as WebSocketClient.connect() makes it."""
    backend = websocket.WebSocket(skip_utf8_validation=True)
    backend.sock = sock
    backend.connected = True
    return backend


def _serve_k8s(sock, messages):
    k8s = _peer(sock, client=False)
    for msg in messages:
        _retry(k8s.sendmsg, msg)
    _retry(k8s.shutdown, socket.SHUT_RDWR, 1000, None)
    _recv_all(k8s)


class SlowReaderTestCase(base.TestCase):
    """A user that sleeps after each message still gets every byte."""

    def _relay_to_slow_reader(self, messages, to_backend, to_client):
        user_sock, proxy_sock = socket.socketpair()
        backend_sock, k8s_sock = socket.socketpair()
        user = _peer(user_sock, client=True)
        received = []

        k8s = threading.Thread(target=_serve_k8s, args=(k8s_sock, messages))
        reader = threading.Thread(
            target=lambda: received.extend(_recv_all(user, delay=0.002)))
        k8s.start()
        reader.start()
        relay.Relay(_proxy_side(proxy_sock), _backend(backend_sock),
                    to_backend=to_backend, to_client=to_client).run()
        k8s.join(30)
        reader.join(30)

        return received, user.close_code

    def test_merged_session_delivers_all_output_in_order(self):
        output = os.urandom(4 * 1024 * 1024)
        chunks = [output[i:i + 32768] for i in range(0, len(output), 32768)]
        messages = [b"\x01"] + [b"\x01" + chunk for chunk in chunks]

        received, close_code = self._relay_to_slow_reader(
            messages + [STATUS],
            to_backend=k8s_remotecommand.frame_from_client,
            to_client=k8s_remotecommand.payload_for_client)

        self.assertEqual(output, b"".join(received))
        self.assertEqual(1000, close_code)

    def test_passthrough_session_delivers_every_message_in_order(self):
        output = os.urandom(4 * 1024 * 1024)
        messages = [b"\x01"] + [
            bytes((1 + i % 2,)) + output[i * 32768:(i + 1) * 32768]
            for i in range(len(output) // 32768)]

        received, close_code = self._relay_to_slow_reader(
            messages + [STATUS],
            to_backend=relay.unchanged, to_client=relay.unchanged)

        self.assertEqual(messages + [STATUS], received)
        self.assertEqual(1000, close_code)


class LockCheckingSocket(object):
    """A socket that records sends and closes made without the write lock."""

    def __init__(self, sock):
        self.sock = sock
        self.websocket = None
        self.sends = 0
        self.unlocked = []

    def _check(self, call):
        if not self.websocket._write_lock._is_owned():
            self.unlocked.append(call)

    def send(self, data):
        self._check("send")
        self.sends += 1
        return self.sock.send(data)

    def close(self):
        self._check("close")
        self.sock.close()

    def recv(self, size):
        return self.sock.recv(size)

    def shutdown(self, how):
        self.sock.shutdown(how)

    def fileno(self):
        return self.sock.fileno()


def _masked(opcode, payload=b""):
    """Encode a frame as a client sends it."""
    return websockify_websocket.WebSocket()._encode_hybi(
        opcode, payload, mask_key=b"\x01\x02\x03\x04")


class WriteLockTestCase(base.TestCase):
    """Every send and close on the client socket holds the write lock.

    While one thread writes messages, the reader thread answers pings and
    then sees the client close, answer our close, or go away.
    """

    def _check_writes(self, client_frames, close_first=False):
        user_sock, proxy_sock = socket.socketpair()
        checked = LockCheckingSocket(proxy_sock)
        proxy = relay.ZunWebSocket(mock.Mock())
        checked.websocket = proxy
        proxy.accept(checked, HEADERS)
        proxy.relaying = True
        done = threading.Event()

        def write():
            while not done.is_set():
                try:
                    proxy.sendmsg(b"x" * 1000)
                except Exception:
                    return

        def drain():
            while user_sock.recv(65536):
                pass

        writer = threading.Thread(target=write)
        drainer = threading.Thread(target=drain)
        writer.start()
        drainer.start()
        if close_first:
            proxy.shutdown(socket.SHUT_RDWR, 1000, "Target closed")
        user_sock.sendall(b"".join(client_frames))
        if client_frames[-1][0] != 0x88:
            user_sock.shutdown(socket.SHUT_WR)
        _recv_all(proxy)
        done.set()
        writer.join(10)
        if proxy.socket is not None:
            proxy.socket.close()
        drainer.join(10)
        user_sock.close()

        return checked

    def test_while_client_pings_then_closes(self):
        frames = [_masked(0x9, b"ping")] * 200
        frames.append(_masked(0x8, struct.pack(">H", 1000)))

        checked = self._check_writes(frames)

        self.assertEqual([], checked.unlocked)
        self.assertGreaterEqual(checked.sends, 200)

    def test_while_client_pings_then_answers_our_close(self):
        frames = [_masked(0x9, b"ping")] * 200
        frames.append(_masked(0x8, struct.pack(">H", 1000)))

        checked = self._check_writes(frames, close_first=True)

        self.assertEqual([], checked.unlocked)
        self.assertGreaterEqual(checked.sends, 200)

    def test_while_client_pings_then_goes_away(self):
        checked = self._check_writes([_masked(0x9, b"ping")] * 200)

        self.assertEqual([], checked.unlocked)
        self.assertGreaterEqual(checked.sends, 200)
