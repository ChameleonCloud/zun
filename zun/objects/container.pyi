from datetime import datetime
from typing import Any

from oslo_versionedobjects import base
from zun.objects import fields as z_fields

class ContainerBase(base.VersionedObject):
    id: int
    container_id: str | None
    uuid: str | None
    name: str | None
    project_id: str | None
    user_id: str | None
    image: str | None
    cpu: float | None
    cpu_policy: str | None
    cpuset: object | None
    memory: str | None
    command: list[str] | None
    status: str | None
    status_reason: str | None
    task_state: str | None
    environment: dict[str, str] | None
    workdir: str | None
    auto_remove: bool | None
    ports: list[int] | None
    hostname: str | None
    labels: dict[str, str] | None
    addresses: dict[str, Any] | None
    image_pull_policy: str | None
    host: str | None
    restart_policy: dict[str, str] | None
    status_detail: str | None
    interactive: bool | None
    tty: bool | None
    image_driver: str | None
    websocket_url: str | None
    websocket_token: str | None
    security_groups: list[str] | None
    runtime: str | None
    pci_devices: list[object] | None
    disk: int | None
    auto_heal: bool | None
    started_at: datetime | None
    exposed_ports: dict[str, Any] | None
    exec_instances: list[object] | None
    privileged: bool | None
    healthcheck: dict[str, Any] | None
    registry_id: int | None
    registry: object | None
    annotations: dict[str, Any] | None
    cni_metadata: dict[str, Any] | None
    entrypoint: list[str] | None
