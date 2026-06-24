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

"""Kubernetes remotecommand channel framing relayed by the wsproxy.

Each binary WebSocket message is one frame: the first byte is the channel
number, the rest is the payload.
"""

from enum import IntEnum


class Channel(IntEnum):
    # The single source of truth for the remotecommand channel-byte values.
    STDIN = 0
    STDOUT = 1
    STDERR = 2
    ERROR = 3
    RESIZE = 4


def frame_from_client(data):
    """Wrap client stdin bytes in a stdin-channel frame."""
    return bytes((Channel.STDIN,)) + data


def payload_for_client(frame):
    """Return the bytes to write to the client, or None to drop the frame.

    Forward only stdout/stderr; drop the channel byte, the error/status frame,
    resize, and empty frames so none of them reach the terminal.
    """
    if not frame:
        return None
    channel, payload = frame[0], frame[1:]
    if channel in (Channel.STDOUT, Channel.STDERR) and payload:
        return payload
    return None
