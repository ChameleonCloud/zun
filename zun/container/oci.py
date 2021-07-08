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
import copy


def merge_oci_runtime_config(parent, *cfgs):
    merged = copy.deepcopy(parent)
    env = merged.setdefault('process', {}).setdefault('env', {})
    mounts = merged.setdefault('mounts', [])
    devices = merged.setdefault('linux', {}).setdefault('devices', [])
    for cfg in cfgs:
        cfg_env = cfg.get('process', {}).get('env')
        if cfg_env is not None:
            env.update(cfg_env)
        cfg_mounts = cfg.get('mounts')
        if cfg_mounts is not None:
            mounts.extend(cfg_mounts)
        cfg_devices = cfg.get('linux', {}).get('devices')
        if cfg_devices is not None:
            devices.extend(cfg_devices)
    return merged
