from oslo_log import log as logging
from kubernetes import client

LOG = logging.getLogger(__name__)


class CalicoV3Api(object):

    def __init__(self, api_client=None):
        if api_client is None:
            api_client = client.CustomObjectsApi()
        self.custom = api_client


    def create_namespaced_network_policy(self,namespace, body, **kwargs):
        self.custom.create_namespaced_custom_object(
            group="projectcalico.org",
            version="v3",
            namespace=namespace,
            plural="networkpolicies",
            body=body,
        )

    def replace_namespaced_network_policy(self,name, namespace, body, **kwargs):
        self.custom.replace_namespaced_custom_object(
            name=name,
            group="projectcalico.org",
            version="v3",
            namespace=namespace,
            plural="networkpolicies",
            body=body,
        )

    def patch_namespaced_network_policy(self,name, namespace, body, **kwargs):
        self.custom.patch_namespaced_custom_object(
            name=name,
            group="projectcalico.org",
            version="v3",
            namespace=namespace,
            plural="networkpolicies",
            body=body,
        )

    def delete_namespaced_network_policy(self,name, namespace, **kwargs):
        self.custom.delete_namespaced_custom_object(
            name=name,
            group="projectcalico.org",
            version="v3",
            namespace=namespace,
            plural="networkpolicies",
        )

