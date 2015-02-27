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
from neutron.i18n import _LE, _LI, _LW
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
        self._get_sync_data()

    def enable_subnet(self, router_id, subnet_id, prefix, ri_ifname, mac):
        router = self.routers.get(router_id)
        if router is not None:
            pdo = router['subnets'].get(subnet_id)
            if not pdo:
                pdo = {'prefix': None,
                       'old_prefix': prefix,
                       'ri_ifname': ri_ifname,
                       'mac': mac,
                       'bind_lla': None,
                       'bind_lla_with_mask': None,
                       'sync': False,
                       'client_started': False}
                router['subnets'][subnet_id] = pdo

            pdo['bind_lla'] = self._get_lla(mac)
            pdo['bind_lla_with_mask'] = '%s/64' % pdo['bind_lla']
            if pdo['sync']:
                # Although it's not possible for this to happen, log an error
                # to catch it in case it happens
                if ri_ifname != pdo['ri_ifname']:
                    LOG.error(_LE("Error enabling pd for router_id %s "
                                  "subnet_id %s ri_ifname %s prefix %s "
                                  "since router interface is out of sync "
                                  "with previous ri_ifname %s"),
                                  router_id, subnet_id, ri_ifname,
                                  prefix, pdo['ri_ifname'])
                else:
                    pdo['mac'] = mac
                    pdo['old_prefix'] = prefix
            else:
                self._add_lla_address(router, pdo['bind_lla_with_mask'])

    def _delete_pd(self, router_id, router, subnet_id, pdo):
        self._delete_lla_address(router, pdo['bind_lla_with_mask'])
        if pdo['client_started']:
            dibbler.disable_ipv6_pd(self.pmon, router_id, subnet_id,
                                    pdo['ri_ifname'],
                                    router['ns_name'])

    def disable_subnet(self, router_id, subnet_id):
        router = self.routers.get(router_id)
        if router is not None:
            pdo = router['subnets'].get(subnet_id)
            if pdo:
                self._delete_pd(router_id, router, subnet_id, pdo)
                del router['subnets'][subnet_id]

    def update_subnet(self, router_id, subnet_id, prefix):
        router = self.routers.get(router_id)
        old_prefix = None
        if router is not None:
            pdo = router['subnets'].get(subnet_id)
            if (pdo and
                pdo['old_prefix'] != prefix):
                old_prefix = pdo['old_prefix']
                pdo['old_prefix'] = prefix
        return old_prefix

    def add_gw_interface(self, router_id, gw_ifname):
        router = self.routers.get(router_id)
        prefix_update = {}
        if router is not None:
            router['gw_interface'] = gw_ifname
            for subnet_id, pdo in router['subnets'].iteritems():
                # gateway is added after internal router ports.
                # If a PD is being synced, and if the prefix is available,
                # send update if prefix out of sync; If not available,
                # start the PD client
                if pdo['sync']:
                    pdo['sync'] = False
                    if pdo['client_started']:
                        if pdo['prefix'] != pdo['old_prefix']:
                            prefix_update['subnet_id'] = pdo['prefix']
                    else:
                        self._delete_lla_address(router, pdo['bind_lla_with_mask'])
                        self._add_lla_address(router, pdo['bind_lla_with_mask'])
                else:
                    self._add_lla_address(router, pdo['bind_lla_with_mask'])
        if prefix_update:
            LOG.debug("Update server with prefixes: %s", prefix_update)
            self.notifier.send_prefix_update(self.context, prefix_update)

    def _delete_router_pd(self, router_id, router):
        prefix_update = {}
        for subnet_id, pdo in router['subnets'].iteritems():
            self._delete_lla_address(router, pdo['bind_lla_with_mask'])
            if pdo['client_started']:
                dibbler.disable_ipv6_pd(self.pmon, router_id, subnet_id,
                                        pdo['ri_ifname'],
                                        router['ns_name'])
                pdo['prefix'] = None
                pdo['client_started'] = False
                prefix_update[subnet_id] = l3_constants.TEMP_PD_PREFIX
        if prefix_update:
            LOG.debug("Update server with prefixes: %s", prefix_update)
            self.notifier.send_prefix_update(self.context, prefix_update)


    def remove_gw_interface(self, router_id):
        router = self.routers.get(router_id)
        if router is not None:
            router['gw_interface'] = None
            self._delete_router_pd(router_id, router)

    def sync_router(self, router_id):
        router = self.routers.get(router_id)
        if router is not None and router['gw_interface'] is None:
            self._delete_router_pd(router_id, router)

    def remove_stale_ri_ifname(self, router_id, stale_ifname):
        router = self.routers.get(router_id)
        if router is not None:
            for subnet_id, pdo in router['subnets'].iteritems():
                if pdo['ri_ifname'] == stale_ifname:
                    self._delete_pd(router_id, router, subnet_id, pdo)
                    break
            else:
                return
            del router['subnets'][subnet_id]

    def remove_router(self, router_id):
        router = self.routers.get(router_id)
        if router is not None:
            self._delete_router_pd(router_id, router)
            del self.routers[router_id]['subnets']
            del self.routers[router_id]

    def add_router(self, router_id, name_space):
        router = self.routers.get(router_id)
        if not router:
            self.routers[router_id] = {'gw_interface': None,
                                       'ns_name': name_space,
                                       'subnets': {}}
        else:
            # This will happen during l3 agent restart
            router['ns_name'] = name_space

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

    def _add_lla_address(self, router, lla_with_mask):
        if router['gw_interface']:
            self.intf_driver.add_v6addr(router['gw_interface'],
                                        lla_with_mask,
                                        router['ns_name'])
            # There is a delay before the LLA becomes active.
            # This is because the kernal runs DAD to make sure LLA uniqueness
            # Spawn a thread to wait for the interface to be ready
            eventlet.spawn_n(self._ensure_lla_task,
                             router['gw_interface'],
                             router['ns_name'],
                             lla_with_mask)

    def _delete_lla_address(self, router, lla_with_mask):
        if lla_with_mask:
            try:
                self.intf_driver.delete_v6addr(router['gw_interface'],
                                            lla_with_mask, router['ns_name'])
            except RuntimeError:
                # Ignore error if the lla doesn't exist
                pass

    def _ensure_lla_task(self, gw_ifname, ns_name, lla_with_mask):
        # It would be insane for taking so long unless DAD test failed
        # In that case, the subnet would never be assigned a prefix.
        # Therefore, it's very important to choose a base MAC that won't
        # cause conflict with the external network
        for attempt in range(20):
            try:
                llas = self.intf_driver.get_llas(gw_ifname, ns_name)
            except RuntimeError:
                # The error message was printed as part of the driver call
                # This could happen if the gw_ifname was removed
                # simply return and exit the thread
                return
            if self._ensure_lla(lla_with_mask, llas):
                LOG.debug("LLA %s is active now" % lla_with_mask)
                self.pd_update_cb()
                break
            else:
                eventlet.sleep(5)

    @staticmethod
    def _ensure_lla(lla_with_mask, llas):
        for lla in llas:
            if lla_with_mask == lla[0] and 'tentative' not in lla:
                return True
        return False

    def run_pd_client(self):
        LOG.debug("Starting run_pd_client")

        prefix_update = {}
        for router_id, router in self.routers.iteritems():
            if not router['gw_interface']:
                continue

            llas = None
            for subnet_id, pdo in router['subnets'].iteritems():
                if pdo['client_started']:
                    prefix = dibbler.get_prefix(router_id, subnet_id,
                                                pdo['ri_ifname'])
                    if prefix != pdo['prefix']:
                        pdo['prefix'] = prefix
                        prefix_update[subnet_id] = prefix
                else:
                    if not llas:
                        llas = self.intf_driver.get_llas(router['gw_interface'],
                                                         router['ns_name'])

                    if self._ensure_lla(pdo['bind_lla_with_mask'], llas):
                        dibbler.enable_ipv6_pd(self.pmon,
                                               router_id,
                                               subnet_id,
                                               pdo['ri_ifname'],
                                               router['ns_name'],
                                               router['gw_interface'],
                                               pdo['bind_lla'])
                        pdo['client_started'] = True

        if prefix_update:
            LOG.debug("Update server with prefixes: %s", prefix_update)
            self.notifier.send_prefix_update(self.context, prefix_update)

    def after_start(self):
        LOG.debug('SIGHUP signal handler set')
        signal.signal(signal.SIGHUP, self._handle_sighup)

    def _handle_sighup(self, signum, frame):
        LOG.debug('SIGHUP called')
        self.pd_update_cb()

    def _get_sync_data(self):
        sync_data = dibbler.get_sync_data()
        for requestor_info in sync_data:
            router_id = requestor_info['router_id']
            if not self.routers.get(router_id):
                self.routers[router_id] = {'gw_interface': None,
                                           'ns_name': None,
                                           'subnets': {}}
            pdo = {'prefix': requestor_info['prefix'],
                   'old_prefix': None,
                   'ri_ifname': requestor_info['ri_ifname'],
                   'mac': None,
                   'bind_lla': None,
                   'bind_lla_with_mask': None,
                   'sync': True,
                   'client_started': requestor_info['client_started']}
            subnets = self.routers[router_id]['subnets']
            subnets[requestor_info['subnet_id']] = pdo
