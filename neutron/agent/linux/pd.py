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
from oslo.config import cfg
import six

from neutron.agent.linux import external_process
from neutron.agent.linux import utils
from neutron.common import constants
from neutron.openstack.common import log as logging


LOG = logging.getLogger(__name__)

OPTS = [
    cfg.StrOpt('pd_confs',
               default='/etc/dibbler/client.conf',
               help=_('Location to store IPv6 PD config files')),
]

cfg.CONF.register_opts(OPTS)

CONFIG_TEMPLATE = jinja2.Template("""
# Config for dibbler-client.

# 8 (Debug) is most verbose. 7 (Info) is usually the best option
log-level 7

{% if ra_mode == constants.DHCPV6_STATELESS %}
stateless
{% endif %}

iface {{ interface_name }} {
# ask for address
    ia
    pd
}
""")


def _generate_dibbler_conf(router_id, router_ports, dev_name_helper):
    dibbler_conf = utils.get_conf_file_name(cfg.CONF.ra_confs,
                                            router_id,
                                            'client.conf',
                                            True)
    buf = six.StringIO()
    for p in router_ports:
        if netaddr.IPNetwork(p['subnet']['cidr']).version == 6:
            interface_name = dev_name_helper(p['id'])
            ra_mode = p['subnet']['ipv6_ra_mode']
            buf.write('%s' % CONFIG_TEMPLATE.render(
                ra_mode=ra_mode,
                interface_name=interface_name,
                constants=constants))

    utils.replace_file(dibbler_conf, buf.getvalue())
    return dibbler_conf


def _spawn_dibbler(router_id, dibbler_conf, router_ns, root_helper):
    def callback(pid_file):
        dibbler_cmd = ['dibbler-client',
                       'start']
        return dibbler_cmd

    dibbler = external_process.ProcessManager(cfg.CONF,
                                              router_id,
                                              root_helper,
                                              router_ns,
                                              'dibbler')
    dibbler.enable(callback, True)
    LOG.debug("dibbler enabled for router %s", router_id)


def enable_ipv6_pd(router_id, router_ns, router_ports,
                   dev_name_helper, root_helper):
    for p in router_ports:
        if netaddr.IPNetwork(p['subnet']['cidr']).version == 6:
            break
    else:
        # Kill the daemon if it's running
        disable_ipv6_pd(router_id, router_ns, root_helper)
        return

    LOG.debug("Enable IPv6 PD for router %s", router_id)
    dibbler_conf = _generate_dibbler_conf(router_id,
                                          router_ports,
                                          dev_name_helper)
    _spawn_dibbler(router_id, dibbler_conf, router_ns, root_helper)


def disable_ipv6_pd(router_id, router_ns, root_helper):
    dibbler = external_process.ProcessManager(cfg.CONF,
                                              router_id,
                                              root_helper,
                                              router_ns,
                                              'dibbler')
    dibbler.disable()
    utils.remove_conf_files(cfg.CONF.pd_confs, router_id)
    LOG.debug("dibbler disabled for router %s", router_id)
