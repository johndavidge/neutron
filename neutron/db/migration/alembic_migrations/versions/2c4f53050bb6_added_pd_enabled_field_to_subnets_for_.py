# Copyright 2015 OpenStack Foundation
#
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
#

"""Added pd_enabled field to Subnets for Prefix Delegation

Revision ID: 2c4f53050bb6
Revises: 2d2a8a565438
Create Date: 2015-02-23 17:29:33.474792

"""

# revision identifiers, used by Alembic.
revision = '2c4f53050bb6'
down_revision = '2d2a8a565438'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('subnets',
                  sa.Column('pd_enabled',
                            sa.Boolean(),
                            nullable=True))


def downgrade():
    op.drop_column('subnets', 'pd_enabled')
