# Copyright (c) 2015 Openstack Foundation
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

import mock

from neutron.agent.l3 import router_info
from neutron.agent.linux import ip_lib
from neutron.common import constants as l3_constants
from neutron.common import exceptions as n_exc
from neutron.openstack.common import uuidutils
from neutron.tests import base

_uuid = uuidutils.generate_uuid


class BasicRouterTestCaseFramework(base.BaseTestCase):
    def _create_router(self, router=None, **kwargs):
        if not router:
            router = mock.MagicMock()
        return router_info.RouterInfo(mock.sentinel.router_id,
                                      router,
                                      mock.sentinel.agent_conf,
                                      mock.sentinel.interface_driver,
                                      **kwargs)


class TestBasicRouterOperations(BasicRouterTestCaseFramework):

    def test_get_floating_ips(self):
        router = mock.MagicMock()
        router.get.return_value = [mock.sentinel.floating_ip]
        ri = self._create_router(router)

        fips = ri.get_floating_ips()

        self.assertEqual([mock.sentinel.floating_ip], fips)

    def test_process_floating_ip_nat_rules(self):
        ri = self._create_router()
        fips = [{'fixed_ip_address': mock.sentinel.ip,
                 'floating_ip_address': mock.sentinel.fip}]
        ri.get_floating_ips = mock.Mock(return_value=fips)
        ri.iptables_manager = mock.MagicMock()
        ipv4_nat = ri.iptables_manager.ipv4['nat']
        ri.floating_forward_rules = mock.Mock(
            return_value=[(mock.sentinel.chain, mock.sentinel.rule)])

        ri.process_floating_ip_nat_rules()

        # Be sure that the rules are cleared first and apply is called last
        self.assertEqual(mock.call.clear_rules_by_tag('floating_ip'),
                         ipv4_nat.mock_calls[0])
        self.assertEqual(mock.call.apply(), ri.iptables_manager.mock_calls[-1])

        # Be sure that add_rule is called somewhere in the middle
        ipv4_nat.add_rule.assert_called_once_with(mock.sentinel.chain,
                                                  mock.sentinel.rule,
                                                  tag='floating_ip')

    def test_process_floating_ip_nat_rules_removed(self):
        ri = self._create_router()
        ri.get_floating_ips = mock.Mock(return_value=[])
        ri.iptables_manager = mock.MagicMock()
        ipv4_nat = ri.iptables_manager.ipv4['nat']

        ri.process_floating_ip_nat_rules()

        # Be sure that the rules are cleared first and apply is called last
        self.assertEqual(mock.call.clear_rules_by_tag('floating_ip'),
                         ipv4_nat.mock_calls[0])
        self.assertEqual(mock.call.apply(), ri.iptables_manager.mock_calls[-1])

        # Be sure that add_rule is called somewhere in the middle
        self.assertFalse(ipv4_nat.add_rule.called)

    def _test_add_fip_addr_to_device_error(self, device):
        ri = self._create_router()
        ip = '15.1.2.3'

        result = ri._add_fip_addr_to_device(
            {'id': mock.sentinel.id, 'floating_ip_address': ip}, device)

        device.addr.add.assert_called_with(4, ip + '/32', ip)
        return result

    def test__add_fip_addr_to_device(self):
        result = self._test_add_fip_addr_to_device_error(mock.Mock())
        self.assertTrue(result)

    def test__add_fip_addr_to_device_error(self):
        device = mock.Mock()
        device.addr.add.side_effect = RuntimeError
        result = self._test_add_fip_addr_to_device_error(device)
        self.assertFalse(result)

    def test_process_snat_dnat_for_fip(self):
        ri = self._create_router()
        ri.process_floating_ip_nat_rules = mock.Mock(side_effect=Exception)

        self.assertRaises(n_exc.FloatingIpSetupException,
                          ri.process_snat_dnat_for_fip)

        ri.process_floating_ip_nat_rules.assert_called_once_with()

    def test_put_fips_in_error_state(self):
        ri = self._create_router()
        ri.router = mock.Mock()
        ri.router.get.return_value = [{'id': mock.sentinel.id1},
                                      {'id': mock.sentinel.id2}]

        statuses = ri.put_fips_in_error_state()

        expected = [{mock.sentinel.id1: l3_constants.FLOATINGIP_STATUS_ERROR,
                     mock.sentinel.id2: l3_constants.FLOATINGIP_STATUS_ERROR}]
        self.assertNotEqual(expected, statuses)

    def test_configure_fip_addresses(self):
        ri = self._create_router()
        ri.process_floating_ip_addresses = mock.Mock(
            side_effect=Exception)

        self.assertRaises(n_exc.FloatingIpSetupException,
                          ri.configure_fip_addresses,
                          mock.sentinel.interface_name)

        ri.process_floating_ip_addresses.assert_called_once_with(
            mock.sentinel.interface_name)

    def test_get_router_cidrs_returns_cidrs(self):
        ri = self._create_router()
        addresses = ['15.1.2.2/24', '15.1.2.3/32']
        device = mock.MagicMock()
        device.addr.list.return_value = [{'cidr': addresses[0]},
                                         {'cidr': addresses[1]}]
        self.assertEqual(set(addresses), ri.get_router_cidrs(device))


@mock.patch.object(ip_lib, 'IPDevice')
class TestFloatingIpWithMockDevice(BasicRouterTestCaseFramework):

    def test_process_floating_ip_addresses_remap(self, IPDevice):
        fip_id = _uuid()
        fip = {
            'id': fip_id, 'port_id': _uuid(),
            'floating_ip_address': '15.1.2.3',
            'fixed_ip_address': '192.168.0.2'
        }

        IPDevice.return_value = device = mock.Mock()
        device.addr.list.return_value = [{'cidr': '15.1.2.3/32'}]
        ri = self._create_router()
        ri.get_floating_ips = mock.Mock(return_value=[fip])

        fip_statuses = ri.process_floating_ip_addresses(
            mock.sentinel.interface_name)
        self.assertEqual({fip_id: l3_constants.FLOATINGIP_STATUS_ACTIVE},
                         fip_statuses)

        self.assertFalse(device.addr.add.called)
        self.assertFalse(device.addr.delete.called)

    def test_process_router_with_disabled_floating_ip(self, IPDevice):
        fip_id = _uuid()
        fip = {
            'id': fip_id, 'port_id': _uuid(),
            'floating_ip_address': '15.1.2.3',
            'fixed_ip_address': '192.168.0.2'
        }

        ri = self._create_router()
        ri.floating_ips = [fip]
        ri.get_floating_ips = mock.Mock(return_value=[])

        fip_statuses = ri.process_floating_ip_addresses(
            mock.sentinel.interface_name)

        self.assertIsNone(fip_statuses.get(fip_id))

    def test_process_router_floating_ip_with_device_add_error(self, IPDevice):
        IPDevice.return_value = device = mock.Mock(side_effect=RuntimeError)
        device.addr.list.return_value = []
        fip_id = _uuid()
        fip = {
            'id': fip_id, 'port_id': _uuid(),
            'floating_ip_address': '15.1.2.3',
            'fixed_ip_address': '192.168.0.2'
        }
        ri = self._create_router()
        ri.add_floating_ip = mock.Mock(
            return_value=l3_constants.FLOATINGIP_STATUS_ERROR)
        ri.get_floating_ips = mock.Mock(return_value=[fip])

        fip_statuses = ri.process_floating_ip_addresses(
            mock.sentinel.interface_name)

        self.assertEqual({fip_id: l3_constants.FLOATINGIP_STATUS_ERROR},
                         fip_statuses)

    # TODO(mrsmith): refactor for DVR cases
    def test_process_floating_ip_addresses_remove(self, IPDevice):
        IPDevice.return_value = device = mock.Mock()
        device.addr.list.return_value = [{'cidr': '15.1.2.3/32'}]

        ri = self._create_router()
        ri.remove_floating_ip = mock.Mock()
        ri.router.get = mock.Mock(return_value=[])

        fip_statuses = ri.process_floating_ip_addresses(
            mock.sentinel.interface_name)
        self.assertEqual({}, fip_statuses)
        ri.remove_floating_ip.assert_called_once_with(device, '15.1.2.3/32')
