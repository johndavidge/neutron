# Copyright 2015 Cisco Systems
# All Rights Reserved.
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

import jinja2
import os
from oslo_config import cfg
import shutil
import six

from neutron.agent.linux import utils
from neutron.common import constants
from neutron.openstack.common import log as logging


LOG = logging.getLogger(__name__)

OPTS = [
    cfg.StrOpt('pd_confs',
               default='$state_path/pd',
               help=_('Location to store IPv6 PD config files')),
    cfg.StrOpt('vrpen',
               default='8888',
               help=_("A decimal value as Vendor's Registered Private "
                      "Enterprise Number as required by RFC3315 DUID-EN")),
]

cfg.CONF.register_opts(OPTS)

CONFIG_TEMPLATE = jinja2.Template("""
# Config for dibbler-client.

# Use enterprise number based duid
duid-type duid-en {{ enterprise_number }} {{ va_id }}

# 8 (Debug) is most verbose. 7 (Info) is usually the best option
log-level 8

# No automatic downlink address assignment
downlink-prefix-ifaces "none"

# Use script to notify l3_agent of assigned prefix
script {{ script_path }}

# Ask for prefix over the external gateway interface
iface {{ interface_name }} {
# ask for address
    pd 1
}
""")

# The first line must be #!/bin/bash
SCRIPT_TEMPLATE = jinja2.Template("""#!/bin/bash

neutron-pd-notify $1 {{ prefix_path }} {{ l3_agent_pid }}
""")

def _get_requestor_id(router_id, subnet_id, ri_ifname):
    return "%s:%s:%s" % (router_id, subnet_id, ri_ifname)


def _get_dibbler_client_working_area(requestor_id):
    return "%s/%s" % (cfg.CONF.pd_confs, requestor_id)


def _convert_subnet_id(subnet_id):
    return ''.join(subnet_id.split('-'))


def _get_prefix_path(requestor_id):
    dcwa = _get_dibbler_client_working_area(requestor_id)
    return "%s/prefix" % dcwa


def _get_pid_path(requestor_id):
    dcwa = _get_dibbler_client_working_area(requestor_id)
    return "%s/client.pid" % dcwa


def _is_dibbler_client_running(requestor_id):
    return utils.get_value_from_file(_get_pid_path(requestor_id))


def _generate_dibbler_conf(requestor_id, subnet_id, ex_gw_ifname):
    dcwa = _get_dibbler_client_working_area(requestor_id)
    script_path = utils.get_conf_file_name(dcwa, 'notify', 'sh', True)
    buf = six.StringIO()
    buf.write('%s' % SCRIPT_TEMPLATE.render(
                         prefix_path=_get_prefix_path(requestor_id),
                         l3_agent_pid=os.getpid()))
    utils.replace_file(script_path, buf.getvalue())
    os.chmod(script_path, 0o744)

    dibbler_conf = utils.get_conf_file_name(dcwa, 'client', 'conf', False)
    buf = six.StringIO()
    buf.write('%s' % CONFIG_TEMPLATE.render(
                         enterprise_number=cfg.CONF.vrpen,
                         va_id='0x%s' % _convert_subnet_id(subnet_id),
                         script_path='"%s/notify.sh"' % dcwa,
                         interface_name='"%s"' % ex_gw_ifname))

    utils.replace_file(dibbler_conf, buf.getvalue())
    return dcwa


def _spawn_dibbler(pmon, requestor_id, lla,
                   dibbler_conf, router_ns):
    def callback(pid_file):
        dibbler_cmd = ['dibbler-client',
                       'start',
                       '-W', '%s' % dibbler_conf,
                       '-A', '%s' % lla]
        return dibbler_cmd

    pmon.enable(requestor_id,
                cmd_callback=callback,
                namespace=router_ns,
                service='dibbler',
                pid_file=_get_pid_path(requestor_id))


def enable_ipv6_pd(pmon, router_id, subnet_id, ri_ifname, router_ns,
                   ex_gw_ifname, lla):
    LOG.debug("Enable IPv6 PD for router %s subnet %s ri_ifname %s",
              router_id, subnet_id, ri_ifname)
    requestor_id = _get_requestor_id(router_id, subnet_id, ri_ifname)
    if not _is_dibbler_client_running(requestor_id):
        dibbler_conf = _generate_dibbler_conf(requestor_id,
                                              subnet_id, ex_gw_ifname)
        _spawn_dibbler(pmon, requestor_id, lla, dibbler_conf, router_ns)
        LOG.debug("dibbler client enabled for router %s subnet %s"
                  " ri_ifname %s", router_id, subnet_id, ri_ifname)


def disable_ipv6_pd(pmon, router_id, subnet_id, ri_ifname, router_ns):
    LOG.debug("Disable IPv6 PD for router %s subnet %s ri_ifname %s",
              router_id, subnet_id, ri_ifname)
    requestor_id = _get_requestor_id(router_id, subnet_id, ri_ifname)
    dcwa = _get_dibbler_client_working_area(requestor_id)

    def callback(pid_file):
        dibbler_cmd = ['dibbler-client',
                       'stop',
                       '-W', '%s' % dcwa]
        return dibbler_cmd

    pmon.disable(requestor_id,
                 cmd_callback=callback,
                 namespace=router_ns,
                 service='dibbler',
                 pid_file=_get_pid_path(requestor_id))
    shutil.rmtree(dcwa, ignore_errors=True)
    LOG.debug("dibbler client disabled for router %s subnet %s ri_ifname %s",
              router_id, subnet_id, ri_ifname)


def get_prefix(router_id, subnet_id, ri_ifname):
    requestor_id = _get_requestor_id(router_id, subnet_id, ri_ifname)
    prefix_fname = _get_prefix_path(requestor_id)
    prefix = utils.get_value_from_file(prefix_fname)
    if not prefix:
        prefix = constants.TEMP_PD_PREFIX
    return prefix

def get_sync_data():
    sync_data = []
    try:
        requestor_ids = os.listdir(cfg.CONF.pd_confs)
    except OSError:
        pass

    for requestor_id in requestor_ids:
        requestor_info = {}
        router_id, subnet_id, ri_ifname = requestor_id.split(":")
        requestor_info['router_id'] = router_id
        requestor_info['subnet_id'] = subnet_id
        requestor_info['ri_ifname'] = ri_ifname
        requestor_info['client_started'] = _is_dibbler_client_running(
                                               requestor_id)
        requestor_info['prefix'] = get_prefix(router_id,
                                              subnet_id, ri_ifname)
        sync_data.append(requestor_info)
    return sync_data
