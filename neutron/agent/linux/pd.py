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

import eventlet
import signal

from neutron.agent.linux import dibbler
from neutron.common import constants as l3_constants
from neutron.common import ipv6_utils
from neutron.openstack.common import log as logging


LOG = logging.getLogger(__name__)


class PrefixDelegation(object):
    def __init__(self, context, pmon, intf_driver, notifier, pd_update_cb):
        self.context = context
        self.pmon = pmon
        self.intf_driver = intf_driver
        self.notifier = notifier
        self.routers = {}
        self.pd_update_cb = pd_update_cb

    def enable_subnet(self, router_id, subnet_id, ri_ifname, mac):
        pdo = {'prefix': l3_constants.TEMP_PD_PREFIX,
               'ri_ifname': ri_ifname,
               'mac': mac,
               'bind_lla': None,
               'port_assigned': False,
               'client_started': False}
        router = self.routers.get(router_id)
        if router is not None:
            pdo['bind_lla'] = self._add_lla_address(router, mac)
            router['subnets'][subnet_id] = pdo

    def disable_subnet(self, router_id, subnet_id):
        router = self.routers.get(router_id)
        if router is not None:
            pdo = router['subnets'].get(subnet_id)
            self._delete_lla_address(router, '%s/64' % pdo['bind_lla'])
            if pdo and pdo['client_started']:
                dibbler.disable_ipv6_pd(self.pmon, router_id,
                                        router['ns_name'], subnet_id)
            del router['subnets'][subnet_id]

    def update_subnet(self, router_id, subnet_id):
        router = self.routers.get(router_id)
        if router is not None:
            pdo = router['subnets'].get(subnet_id)
            if pdo and not pdo['port_assigned']:
                pdo['port_assigned'] = True
                return True
        return False

    def add_gw_interface(self, router_id, gw_ifname):
        router = self.routers.get(router_id)
        if router is not None:
            router['gw_interface'] = gw_ifname

    def remove_gw_interface(self, router_id):
        pass

    def add_router(self, router_id, name_space):
        if not self.routers.get(router_id):
            self.routers[router_id] = {'gw_interface': None,
                                       'ns_name': name_space,
                                       'subnets': {}}

    def remove_router(self, router_id):
        pass

    @staticmethod
    def _get_lla(mac):
        new_mac = mac.split(':')
        byte0 = int(new_mac[0], 16)
        if byte0 > 0x80:
            new_mac[0] = "%02x" % (byte0 - 1)
        else:
            new_mac[0] = "%02x" % (byte0 + 1)
        lla = ipv6_utils.get_ipv6_addr_by_EUI64("fe80::/64", ':'.join(new_mac))
        return lla

    def _add_lla_address(self, router, mac):
        if router['gw_interface']:
            lla = self._get_lla(mac)
            self.intf_driver.add_v6addr(router['gw_interface'],
                                        '%s/64' % lla,
                                        router['ns_name'])
            eventlet.spawn_n(self._ensure_lla_task, router, '%s/64' % lla)
            return lla

    def _delete_lla_address(self, router, lla):
        if lla:
            self.intf_driver.delete_lla(router['gw_interface'],
                                        lla, router['ns_name'])

    def _ensure_lla_task(self, router, pd_lla):
        while True:
            llas = self.intf_driver.get_llas(router['gw_interface'],
                                             router['ns_name'])
            if self._ensure_lla(pd_lla, llas):
                self.pd_update_cb()
                break
            else:
                eventlet.sleep(2)
        LOG.debug("LLA %s is active now" % pd_lla)

    @staticmethod
    def _ensure_lla(pd_lla, llas):
        for lla in llas:
            if pd_lla == lla[0] and 'tentative' not in lla:
                return True
        return False

    def run_pd_client(self, context):
        LOG.debug("Starting run_pd_client")

        prefix_update = {}
        for router_id, router in self.routers.iteritems():
            if not router['gw_interface']:
                continue

            lla = None

            for subnet_id, pdo in router['subnets'].iteritems():
                if pdo['client_started']:
                    prefix = dibbler.get_prefix(subnet_id)
                    if prefix != pdo['prefix']:
                        pdo['prefix'] = prefix
                        prefix_update[subnet_id] = prefix
                else:
                    if not lla:
                        lla = self.intf_driver.get_llas(router['gw_interface'],
                                                        router['ns_name'])

                    if self._ensure_lla('%s/64' % pdo['bind_lla'], lla):
                        dibbler.enable_ipv6_pd(self.pmon,
                                               router_id,
                                               router['ns_name'],
                                               subnet_id,
                                               router['gw_interface'],
                                               pdo['bind_lla'])
                        pdo['client_started'] = True

        if prefix_update:
            LOG.debug("Update server with prefixes: %s", prefix_update)
            self.notifier.send_prefix_update(context, prefix_update)

    def after_start(self):
        LOG.debug('SIGHUP signal handler set')
        signal.signal(signal.SIGHUP, self._handle_sighup)

    def _handle_sighup(self, signum, frame):
        LOG.debug('SIGHUP called')
        self.pd_update_cb()
