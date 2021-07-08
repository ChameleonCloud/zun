# Copyright 2021 University of Chicago
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from re import I
from oslo_log import log as logging
from oslo_middleware import request_id

from zun.common import clients
from zun.common import context as zun_context
from zun.common import exception

LOG = logging.getLogger(__name__)


class CyborgClient(object):
    """Client class for updating the scheduler."""

    def __init__(self, context=None, adapter=None):
        """Initialize the report client.

        :param context: Security context
        :param adapter: A prepared keystoneauth1 Adapter for API communication.
                If unspecified, one is created based on config options in the
                [cyborg_client] section.
        """
        self._context = context or zun_context.get_admin_context()
        self._client, self._ks_filter = self._create_client()

    def _create_client(self):
        """Create the HTTP session accessing the cyborg service."""
        client, ks_filter = clients.OpenStackClients(self._context).cyborg()

        # Set accept header on every request to ensure we notify cyborg
        # service of our response body media type preferences.
        client.additional_headers = {'accept': 'application/json'}
        return client, ks_filter

    def get(self, url, version=None, global_request_id=None):
        headers = ({request_id.INBOUND_HEADER: global_request_id}
                   if global_request_id else {})
        return self._client.get(url, endpoint_filter=self._ks_filter,
                                microversion=version, headers=headers,
                                logger=LOG)

    def post(self, url, data, version=None, global_request_id=None):
        headers = ({request_id.INBOUND_HEADER: global_request_id}
                   if global_request_id else {})
        # NOTE(sdague): using json= instead of data= sets the
        # media type to application/json for us.
        return self._client.post(url, endpoint_filter=self._ks_filter,
                                 json=data, microversion=version,
                                 headers=headers, logger=LOG)

    def put(self, url, data, version=None, global_request_id=None):
        # NOTE(sdague): using json= instead of data= sets the
        # media type to application/json for us.
        kwargs = {'microversion': version,
                  'endpoint_filter': self._ks_filter,
                  'headers': {request_id.INBOUND_HEADER:
                              global_request_id} if global_request_id else {}}
        if data is not None:
            kwargs['json'] = data
        return self._client.put(url, logger=LOG, **kwargs)

    def delete(self, url, version=None, global_request_id=None):
        headers = ({request_id.INBOUND_HEADER: global_request_id}
                   if global_request_id else {})
        return self._client.delete(url, endpoint_filter=self._ks_filter,
                                   microversion=version, headers=headers,
                                   logger=LOG)

    def get_request_groups(self, device_profiles):
        resp = self.get("/v2/device_profiles")
        if resp.status_code != 200:
            raise exception.DeviceRequestFailed(
                "Failed to fetch device profiles: %(error)s",
                error=resp.text
            )
        request_groups = {}
        for dp in resp.json()["device_profiles"]:
            for group_id, group in enumerate(dp["groups"]):
                request_groups[f"device_profile:{dp['name']}:{group_id}"] = group
        return request_groups

    def create_and_bind_arqs(self, container, host_state):
        patch_list = {}
        # Look up device RPs in placement
        self.put(f"/v2/accelerator_requests/{arq_uuid}", [])