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
from zun.websocket import websocketclient


@mock.patch.object(websocketclient.websocket, "create_connection")
@mock.patch.object(websocketclient.ssl, "SSLContext")
class WebSocketClientConnectTestCase(base.TestCase):

    def setUp(self):
        super(WebSocketClientConnectTestCase, self).setUp()
        self.config(ca_file="/etc/zun/ca.pem", cert_file="/etc/zun/cert.pem",
                    key_file="/etc/zun/key.pem", group="docker")

    def test_k8s_sslopt_not_overridden_by_docker_tls(self, mock_ctx,
                                                     mock_create):
        """The k8s driver's exec/attach URLs need the k8s client certs."""
        sslopt = {"certfile": "/tmp/cert", "keyfile": "/tmp/key",
                  "ca_certs": "/tmp/ca"}
        websocketclient.WebSocketClient(
            "wss://k8s:6443/exec", sslopt=sslopt).connect()

        mock_ctx.assert_not_called()
        mock_create.assert_called_once_with(
            "wss://k8s:6443/exec", skip_utf8_validation=True, sslopt=sslopt)

    def test_docker_attach_uses_docker_tls(self, mock_ctx, mock_create):
        """Docker attach over wss needs the [docker] TLS files."""
        websocketclient.WebSocketClient("wss://docker:2376/attach").connect()

        ctx = mock_ctx.return_value
        ctx.load_verify_locations.assert_called_once_with("/etc/zun/ca.pem")
        ctx.load_cert_chain.assert_called_once_with("/etc/zun/cert.pem",
                                                    "/etc/zun/key.pem")
        mock_create.assert_called_once_with(
            "wss://docker:2376/attach", skip_utf8_validation=True,
            sslopt={"context": ctx})
