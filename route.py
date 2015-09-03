#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import netifaces
import logging
from sanji.core import Sanji
from sanji.core import Route
from sanji.connection.mqtt import Mqtt
from sanji.model_initiator import ModelInitiator
from voluptuous import Schema
from voluptuous import Any, Extra, Optional

import ip


_logger = logging.getLogger("sanji.route")


class IPRoute(Sanji):
    """
    A model to handle IP Route configuration.

    Attributes:
        model: database with json format.
    """
    def init(self, *args, **kwargs):
        try:  # pragma: no cover
            self.bundle_env = kwargs["bundle_env"]
        except KeyError:
            self.bundle_env = os.getenv("BUNDLE_ENV", "debug")

        path_root = os.path.abspath(os.path.dirname(__file__))
        if self.bundle_env == "debug":  # pragma: no cover
            path_root = "%s/tests" % path_root

        try:
            self.load(path_root)
        except:
            self.stop()
            raise IOError("Cannot load any configuration.")

        self.cellular = None
        self.interfaces = []

        try:
            self.update_default(self.model.db)
        except:
            pass

    def load(self, path):
        """
        Load the configuration. If configuration is not installed yet,
        initialise them with default value.

        Args:
            path: Path for the bundle, the configuration should be located
                under "data" directory.
        """
        self.model = ModelInitiator("route", path, backup_interval=-1)
        if self.model.db is None:
            raise IOError("Cannot load any configuration.")
        self.save()

    def save(self):
        """
        Save and backup the configuration.
        """
        self.model.save_db()
        self.model.backup_db()

    def cellular_connected(self, name, up=True):
        """
        If cellular is connected, the default gateway should be set to
        cellular interface.

        Args:
            name: cellular's interface name
        """
        default = {}
        if up:
            self.cellular = name
            default["interface"] = self.cellular
            self.update_default(default)
        else:
            self.cellular = None
            if name == self.model.db["default"]:
                self.update_default(default)

    def list_interfaces(self):
        """
        List available interfaces.
        """
        # retrieve all interfaces
        try:
            ifaces = ip.addr.interfaces()
        except:
            return {}

        # list connected interfaces
        data = []
        for iface in ifaces:
            try:
                iface_info = ip.addr.ifaddresses(iface)
            except:
                continue
            if 1 == iface_info["link"]:
                data.append(iface)
        return data

    def list_default(self):
        """
        Retrieve the default gateway

        Return:
            default: dict format with "interface" and/or "gateway"
        """
        gws = netifaces.gateways()
        default = {}
        if gws['default'] != {}:
            gw = gws['default'][netifaces.AF_INET]
        else:
            return default

        default["gateway"] = gw[0]
        default["interface"] = gw[1]
        return default

    def update_default(self, default):
        """
        Update default gateway. If updated failed, should recover to previous
        one.

        Args:
            default: dict format with "interface" required and "gateway"
                     optional.
        """
        # change the default gateway
        if "interface" in default and default["interface"]:
            ifaces = self.list_interfaces()
            if not ifaces or default["interface"] not in ifaces:
                raise ValueError("Interface should be UP.")
            # FIXME: how to determine a interface is produced by cellular
            # elif any("ppp" in s for s in ifaces):
            elif self.cellular:
                raise ValueError("Cellular is connected, the default gateway"
                                 "cannot be changed.")

            # retrieve the default gateway
            for iface in self.interfaces:
                if iface["interface"] == default["interface"]:
                    default = iface
                    break

            try:
                ip.route.delete("default")
                if "gateway" in default:
                    ip.route.add("default", default["interface"],
                                 default["gateway"])
                else:
                    ip.route.add("default", default["interface"])
            except Exception as e:
                raise e
            self.model.db["default"] = default["interface"]

        # delete the default gateway
        else:
            try:
                ip.route.delete("default")
            except Exception as e:
                raise e
            if "default" in self.model.db:
                self.model.db.pop("default")

        self.save()

    def update_interface_router(self, interface):
        """
        Save the interface name with its gateway and update the default
        gateway if needed.

        If gateway is not specified, use the previous value. Only delete the
        gateway when gateway attribute is empty.

        Args:
            interface: dict format with interface "name" and/or "gateway".
        """
        # update the router information
        for iface in self.interfaces:
            if iface["interface"] == interface["name"]:
                if "gateway" in interface:
                    iface["gateway"] = interface["gateway"]
                break
        else:
            iface = {}
            iface["interface"] = interface["name"]
            if "gateway" in interface:
                iface["gateway"] = interface["gateway"]
            self.interfaces.append(iface)

        # check if the default gateway need to be modified
        if iface["interface"] == self.model.db["default"]:
            try:
                self.update_default(iface)
            except:
                pass

    @Route(methods="get", resource="/network/routes/interfaces")
    def _get_interfaces(self, message, response):
        """
        Get available interfaces.
        """
        data = self.list_interfaces()
        return response(data=data)

    @Route(methods="get", resource="/network/routes/default")
    def _get_default(self, message, response):
        """
        Get default gateway.
        """
        default = self.list_default()
        if self.model.db and "default" in self.model.db and default and \
                self.model.db["default"] == default["interface"]:
            return response(data=default)
        return response(data=self.model.db)

    put_default_schema = Schema({
        Optional("interface"): Any(str, unicode),
        Extra: object})

    @Route(methods="put", resource="/network/routes/default")
    def _put_default(self, message, response, schema=put_default_schema):
        """
        Update the default gateway, delete default gateway if data is None or
        empty.
        """
        # TODO: should be removed when schema worked for unittest
        try:
            IPRoute.put_default_schema(message.data)
        except Exception as e:
            return response(code=400,
                            data={"message": "Invalid input: %s." % e})

        # retrieve the default gateway
        default = self.list_default()
        try:
            self.update_default(message.data)
            return response(data=self.model.db)
        except Exception as e:
            # recover the previous default gateway if any
            try:
                if default:
                    self.update_default(default)
            except:
                _logger.info("Failed to recover the default gateway.")
            _logger.info("Update default gateway failed: %s" % e)
            return response(code=404,
                            data={"message":
                                  "Update default gateway failed: %s"
                                  % e})

    def set_router_db(self, message, response):
        """
        Update router database batch or by interface.
        """
        if type(message.data) is list:
            for iface in message.data:
                self.update_interface_router(iface)
            return response(data=self.interfaces)
        elif type(message.data) is dict:
            self.update_interface_router(message.data)
            return response(data=message.data)
        return response(code=400,
                            data={"message": "Wrong type of router database."})

    @Route(methods="put", resource="/network/routes/db")
    def _set_router_db(self, message, response):
        return self.set_router_db(message, response)

    @Route(methods="get", resource="/network/routes/db")
    def _get_router_db(self, message, response):
        return response(data=self.interfaces)

    @Route(methods="put", resource="/network/interface")
    def _event_router_db(self, message):
        self.update_interface_router(message.data)

    '''
    @Route(methods="put", resource="/network/ethernets/:id")
    def _hook_put_ethernet_by_id(self, message, response):
        """
        Save the interface name with its gateway and update the default
        gateway if needed.
        """
        self.update_interface_router(message.data)
        return response(data=self.model.db)

    @Route(methods="put", resource="/network/ethernets")
    def _hook_put_ethernets(self, message, response):
        """
        Save the interface name with its gateway and update the default
        gateway if needed.
        """
        for iface in message.data:
            self.update_interface_router(iface)
        return response(data=self.model.db)
    '''


if __name__ == "__main__":
    FORMAT = "%(asctime)s - %(levelname)s - %(lineno)s - %(message)s"
    logging.basicConfig(level=0, format=FORMAT)
    _logger = logging.getLogger("sanji.route")

    route = IPRoute(connection=Mqtt())
    route.start()
