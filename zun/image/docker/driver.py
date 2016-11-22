# Copyright 2016 Intel.
#
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

from docker import errors
import json

from oslo_log import log as logging
from oslo_utils import excutils

from zun.common import exception
from zun.common.i18n import _
from zun.common import utils
from zun.container.docker import utils as docker_utils
from zun.image import driver


LOG = logging.getLogger(__name__)


class DockerDriver(driver.ContainerImageDriver):
    def __init__(self):
        super(DockerDriver, self).__init__()

    def _search_image_on_host(self, repo, tag):
        with docker_utils.docker_client() as docker:
            image = repo + ":" + tag
            LOG.debug('Inspecting image locally %s' % image)
            try:
                image_dict = docker.inspect_image(image)
                if image_dict:
                    return {'image': repo, 'path': None}
            except errors.NotFound:
                LOG.debug('Image %s not found locally' % image)
                return None

    def _pull_image(self, repo, tag):
        with docker_utils.docker_client() as docker:
            for line in docker.pull(repo, tag=tag, stream=True):
                error = json.loads(line).get('errorDetail')
                if error:
                    if "not found" in error['message']:
                        raise exception.ImageNotFound(error['message'])
                    else:
                        raise exception.DockerError(error['message'])

    def pull_image(self, context, repo, tag, image_pull_policy):
        image = self._search_image_on_host(repo, tag)
        if not utils.should_pull_image(image_pull_policy, bool(image)):
            if image:
                LOG.debug('Image  %s present locally' % repo)
                return image
            else:
                message = _('Image %s not present with pull policy of Never'
                            ) % repo
                raise exception.ImageNotFound(message)

        try:
            LOG.debug('Pulling image from docker %s,'
                      ' context %s' % (repo, context))
            self._pull_image(repo, tag)
            return {'image': repo, 'path': None}
        except exception.ImageNotFound:
            with excutils.save_and_reraise_exception():
                LOG.error(
                    'Image %s was not found in docker repo' % repo)
        except exception.DockerError:
            with excutils.save_and_reraise_exception():
                LOG.error(
                    'Docker API error occurred during downloading\
                    image %s' % repo)
        except errors.APIError as api_error:
            raise exception.ZunException(str(api_error))
        except Exception as e:
            msg = _('Cannot download image from docker: {0}')
            raise exception.ZunException(msg.format(e))