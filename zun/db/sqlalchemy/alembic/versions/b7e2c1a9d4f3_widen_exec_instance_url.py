# Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

"""widen exec_instance url

The k8s backend encodes the exec command into the target url which easily 
exceeds 255 characters. Store it as TEXT instead.

Revision ID: b7e2c1a9d4f3
Revises: f979327df44b
Create Date: 2026-06-23 12:00:00.000000

"""

# revision identifiers, used by Alembic.
revision = 'b7e2c1a9d4f3'
down_revision = 'f979327df44b'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table('exec_instance', schema=None) as batch_op:
        batch_op.alter_column('url',
                              existing_type=sa.String(255),
                              type_=sa.Text(),
                              existing_nullable=True)
