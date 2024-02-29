# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg

nvidia_environment_group = cfg.OptGroup(name='nvidia_environment',
                                        title='Options for nvidia runtime in k8s')

# Define the options for environment variables
nvidia_environment_opts = [
    cfg.StrOpt('nvidia_require_jetpack',
               help=('this variable gets injected into containers started with'
                     'the nvidia runtime, instructs the host to mount all the nvidia libraries')),
    cfg.StrOpt('nvidia_visible_devices',
               help=('this variable gets injected into containers started with'
                     'the nvidia runtime, exposes all GPU devices on the host machine to the container')),
    cfg.StrOpt('nvidia_driver_capabilities',
               help=('this variable gets injected into containers started with'
                     'the nvidia runtime, allows all Nvidia GPU driver modules to be used by the container')),
]

ALL_OPTS = (nvidia_environment_group)


def register_opts(conf):
    conf.register_group(nvidia_environment_group)
    conf.register_opts(ALL_OPTS, nvidia_environment_group)


def list_opts():
    return {nvidia_environment_group: ALL_OPTS}
