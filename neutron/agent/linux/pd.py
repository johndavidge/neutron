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

execute(neutron-pd-notify, {{ prefix }}, {{ pid }}, BOUND, leased-address);

""")

# The first line must be #!/bin/bash
SCRIPT_TEMPLATE = jinja2.Template("""#!/bin/bash

neutron-pd-notify {{ prefix_path }} {{ l3_agent_pid }} $reason $new_ip6_address
""")

PID_TEMPLATE = jinja2.Template("""#ISC DHCP Client PID File""")

LEASE_TEMPLATE = jinja2.Template("""#ISC DHCP Client Lease File""")


def _get_isc_dhcp_client_working_area(subnet_id):
    return "%s/%s" % (cfg.CONF.pd_confs, subnet_id)


def _convert_subnet_id(subnet_id):
    return ''.join(subnet_id.split('-'))


def _get_prefix_path(subnet_id):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    return "%s/prefix" % dcwa


def _get_pid_path(subnet_id):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    pid_path = "%s/client.pid" % dcwa
    #pid_path = utils.get_conf_file_name(dcwa, 'client', 'pid', True)
    #buf = six.StringIO()
    #buf.write('%s' % PID_TEMPLATE.render())
    #utils.replace_file(pid_path, buf.getvalue())
    return pid_path


def _get_lease_path(subnet_id):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)    
    lease_path = "%s/client.lease" % dcwa
    #lease_path = utils.get_conf_file_name(dcwa, 'client', 'lease', True)
    #buf = six.StringIO()
    #buf.write('%s' % LEASE_TEMPLATE.render())
    #utils.replace_file(lease_path, buf.getvalue())
    return lease_path


def _generate_isc_dhcp_conf(router_id, subnet_id, ex_gw_ifname):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    script_path = utils.get_conf_file_name(dcwa, 'notify', 'sh', True)
    buf = six.StringIO()
    buf.write('%s' % SCRIPT_TEMPLATE.render(
                         prefix_path=_get_prefix_path(subnet_id),
                         l3_agent_pid=os.getpid()))
    utils.replace_file(script_path, buf.getvalue())
    os.chmod(script_path, 0o744)

    isc_dhcp_conf = utils.get_conf_file_name(dcwa, 'client', 'conf', False)
    buf = six.StringIO()
    buf.write('%s' % CONFIG_TEMPLATE.render(
                         prefix=_get_prefix_path(subnet_id),
                         pid=os.getpid()))

    utils.replace_file(isc_dhcp_conf, buf.getvalue())
    return isc_dhcp_conf


def _spawn_isc_dhcp(router_id, subnet_id, lla, isc_dhcp_conf,
                    ex_gw_ifname, router_ns, root_helper):
    dcwa = _get_isc_dhcp_client_working_area(subnet_id)
    pid_file = _get_pid_path(subnet_id)
    lease_file = _get_lease_path(subnet_id)

    def callback(pid_file):
        isc_dhcp_cmd = ['dhclient',
                       '-P',
                       '-d',
                       '-pf', '%s' % pid_file,
                       '-lf', '%s' % lease_file,
                       '-cf', '%s' % isc_dhcp_conf,
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
        isc_dhcp_conf = _generate_isc_dhcp_conf(router_id,
                                              subnet_id, ex_gw_ifname)
        _spawn_isc_dhcp(router_id, subnet_id, lla, isc_dhcp_conf,
                        ex_gw_ifname, router_ns, root_helper)


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
