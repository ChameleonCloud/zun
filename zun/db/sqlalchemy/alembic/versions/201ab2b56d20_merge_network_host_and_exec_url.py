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

"""merge network host and exec_instance url branches

Both revisions descend from f979327df44b. b7e2c1a9d4f3 was deployed from
chameleoncloud/2023.1, so it must stay a separate branch rather than be
re-parented onto 3f2b36231bee.

Revision ID: 201ab2b56d20
Revises: 3f2b36231bee, b7e2c1a9d4f3
Create Date: 2026-10-04 12:00:00.000000

"""

# revision identifiers, used by Alembic.
revision = '201ab2b56d20'
down_revision = ('3f2b36231bee', 'b7e2c1a9d4f3')
branch_labels = None
depends_on = None


def upgrade():
    pass
