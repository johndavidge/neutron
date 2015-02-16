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
import netaddr
import os
from oslo.config import cfg
import shutil
import six

from neutron.agent.linux import external_process
from neutron.agent.linux import utils
from neutron.common import constants
from neutron.openstack.common import log as logging


LOG = logging.getLogger(__name__)

OPTS = [
    cfg.StrOpt('pd_confs',
               default='$state_path/pd',
               help=_('Location to store IPv6 PD config files')),
]

cfg.CONF.register_opts(OPTS)

CONFIG_TEMPLATE = jinja2.Template("""
# Config for isc-dhcp-client.

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

neutron-pd-notify $reason {{ prefix_path }} {{ l3_agent_pid }}
""")

def _get_isc_dhcp_client_working_area(subnet_id):
    return "%s/%s" % (cfg.CONF.pd_confs, subnet_id)


def _convert_subnet_id(subnet_id):
    return ''.join(subnet_id.split('-'))


def _get_prefix_path(subnet_id):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    return "%s/prefix" % dcwa


def _get_pid_path(subnet_id):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    return "%s/client.pid" % dcwa


def _generate_isc_dhcp_conf(router_id, subnet_id, ex_gw_ifname):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    script_path = utils.get_conf_file_name(dcwa, 'notify', 'sh', True)
    buf = six.StringIO()
    buf.write('%s' % SCRIPT_TEMPLATE.render(
                         prefix_path=_get_prefix_path(subnet_id),
                         l3_agent_pid=os.getpid()))
    utils.replace_file(script_path, buf.getvalue())
    os.chmod(script_path, 0o744)

    #isc_dhcp_conf = utils.get_conf_file_name(dcwa, 'client', 'conf', False)
    #buf = six.StringIO()
    #buf.write('%s' % CONFIG_TEMPLATE.render(
                         #enterprise_number=8888,
                         #va_id='0x%s' % _convert_subnet_id(subnet_id),
                         #script_path='"%s/notify.sh"' % dcwa,
                         #interface_name='"%s"' % ex_gw_ifname))

    #utils.replace_file(isc_dhcp_conf, buf.getvalue())
    return dcwa


def _spawn_isc_dhcp(router_id, subnet_id, lla,
                    router_ns, root_helper, ex_gw_ifname):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    script_path = utils.get_conf_file_name(dcwa, 'notify', 'sh', True)
    pid_file = _get_pid_path(subnet_id)
    buf = six.StringIO()
    buf.write('%s' % SCRIPT_TEMPLATE.render(
                         prefix_path=_get_prefix_path(subnet_id),
                         l3_agent_pid=os.getpid()))
    utils.replace_file(script_path, buf.getvalue())
    os.chmod(script_path, 0o744)

    def callback(pid_file):
        isc_dhcp_cmd = ['dhclient',
                       '-P',
                       '-pf', '%s' % pid_file,
                       '-sf', '%s' % script_path,
                       ex_gw_ifname]
        return isc_dhcp_cmd

    pid_file = _get_pid_path(subnet_id)
    isc_dhcp = external_process.ProcessManager(
                                   cfg.CONF,
                                   subnet_id,
                                   root_helper,
                                   router_ns,
                                   'isc_dhcp',
                                   pid_file=pid_file)

    isc_dhcp.enable(callback, True)
    LOG.debug("isc_dhcp client enabled for router %s subnet %s",
              router_id, subnet_id)


def _is_isc_dhcp_client_running(subnet_id):
    return utils.get_value_from_file(_get_pid_path(subnet_id))


def enable_ipv6_pd(router_id, router_ns, subnet_id,
                   root_helper, ex_gw_ifname, lla):
    LOG.debug("Enable IPv6 PD for router %s subnet %s", router_id, subnet_id)
    if not _is_isc_dhcp_client_running(subnet_id):
        #isc_dhcp_conf = _generate_isc_dhcp_conf(router_id,
                                              #subnet_id, ex_gw_ifname)
        _spawn_isc_dhcp(router_id, subnet_id, lla,
                        router_ns, root_helper, ex_gw_ifname)


def disable_ipv6_pd(router_id, router_ns, subnet_id, root_helper):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    isc_dhcp = external_process.ProcessManager(
                                   cfg.CONF,
                                   subnet_id,
                                   root_helper,
                                   router_ns,
                                   'isc_dhcp',
                                   pid_file=_get_pid_path(subnet_id))
    isc_dhcp.disable()
    shutil.rmtree(dcwa, ignore_errors=True)
    LOG.debug("isc_dhcp client disabled for router %s subnet %s",
              router_id, subnet_id)


def get_prefix(subnet_id):
    prefix_fname = _get_prefix_path(subnet_id)
    prefix = utils.get_value_from_file(prefix_fname)
    if not prefix:
        prefix = constants.TEMP_PD_PREFIX
    return prefix
