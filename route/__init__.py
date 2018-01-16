#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import logging
from threading import Lock
from time import sleep
from sanji.model import Model
import json
import re
import sh

import ip


_logger = logging.getLogger("sanji.route")
_update_default_lock = Lock()


class IPRouteError(Exception):
    pass


class IPRoute(Model):
    """
    A model to handle IP Route configuration.

    Attributes:
        model: database with json format.
    """

    UPDATE_INTERVAL = 60

    def __init__(self, *args, **kwargs):
        super(IPRoute, self).__init__(*args, **kwargs)

        self._path = kwargs["path"]

        """interface info. database
        {
          "interface": "wwan0",
          "actualIface": "ppp0",
          "gateway": "192.168.7.254",
          "status": true,
          "wan": true
        }
        """
        self._interfaces = {}

        # alias and real name mappings for interfaces
        # { "ppp0": "wwan0" }
        self._alias = {}

        # find correct interface if shell command is required
        self._load_mappings(self._path)
        self._cmd_regex = re.compile(r"\$\(([\S\s]+)\)")
        self._routes = self._get_priority_list()

    def set_wan_event_cb(self, cb):
        self._wan_event_cb = cb

    def _load_mappings(self, path):
        with open(os.path.join(path, "config", "mapping.json")) as f:
            self._mappings = json.load(f)

        for mapping in self._mappings:
            mapping["regex"] = re.compile(mapping["pattern"])

    def _get_iface_name(self, name):
        for mapping in self._mappings:
            match = mapping["regex"].match(name)
            if not match:
                continue
            _iface = mapping["name"].format(*match.groups())

            match = self._cmd_regex.match(_iface)
            if not match:
                return _iface

            try:
                with open("{}/iface_cmd.sh".format(
                        os.path.join(self._path, "config")), "w") as f:
                    f.write(match.group(1))
                _iface = str(sh.sh("{}/iface_cmd.sh".format(
                    os.path.join(self._path, "config")))).rstrip()
                if _iface == "":
                    return None
                return _iface
            except Exception as e:
                _logger.debug(e)
                return None
        return name

    def _get_priority_list(self):
        """Get priority list with real interface name for default route
        """
        routes = []
        for iface in self.model.db:
            name = self._get_iface_name(iface)
            if name and name != "":
                if name != iface:
                    self._alias[name] = iface
                routes.append(name)
                continue
        return routes

    def run(self):
        while True:
            sleep(self.UPDATE_INTERVAL)
            try:
                self.try_update_default(self._routes)
            except Exception as e:
                _logger.debug(e)

    def save(self):
        """
        Save and backup the configuration.
        """
        self.model.save_db()
        self.model.backup_db()

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
            if iface_info["link"] is True:
                inet_ip = [inet["ip"]
                           for inet in iface_info["inet"]
                           if "" != inet["ip"]]
                if len(inet_ip) and \
                        (iface in self._interfaces and
                         self._interfaces[iface]["status"] is True and
                         self._interfaces[iface]["wan"] is True):
                    data.append(iface)
        return data

    def get_priority_list(self):
        """Get priority list settings for default route
        """
        return self.model.db

    def set_priority_list(self, priority_list):
        """Get priority list settings for default route
        """
        self.model.db = priority_list
        self.save()

        self._routes = self._get_priority_list()
        self.try_update_default(self._routes)
        return self.model.db

    def _get_default(self):
        """
        Retrieve current default gateway

        Return:
            default: dict format with "interface" and/or "gateway"
        """
        gws = ip.route.show()
        default = {}
        for gw in gws:
            if "default" in gw:
                break
        else:
            return default

        default["wan"] = True
        default["status"] = True
        default["gateway"] = gw["default"]
        default["interface"] = gw["dev"]
        return default

    def get_default(self):
        default = self._get_default()
        _iface = default.get("interface")
        if _iface and _iface in self._alias:
            default["interface"] = self._alias[_iface]
            default["actualIface"] = _iface
        return default

    def _update_default(self, default):
        """
        Update default gateway. If updated failed, should recover to previous
        one.

        Args:
            default: dict format, require at least one of "interface" and
                     "gateway"
                example:
                {
                  "interface": "ppp0",
                  "alias": "wwan0",
                  "gateway": "192.168.7.254",
                  "status": true,
                  "wan": true
                }
        """
        ip.route.delete("default")

        iface = None
        gateway = None
        if "interface" in default and default["interface"]:
            iface = default["interface"]
        if "gateway" in default and default["gateway"]:
            gateway = default["gateway"]

        if not iface and not gateway:
            raise IPRouteError("Invalid default route.")

        # add the default gateway
        # FIXME: only "gateway" without interface is also available
        # FIXME: add "secondary" default route rule
        if iface:
            if gateway:
                ip.route.add("default", iface, gateway)
            else:
                ip.route.add("default", iface)

            if iface and self._wan_event_cb:
                if iface not in self._alias:
                    self._wan_event_cb(iface)
                else:
                    self._wan_event_cb(self._alias[iface], iface)
        elif gateway:
            ip.route.add("default", "", gateway)
        else:
            raise IPRouteError("Invalid default route.")

    def _try_update_default(self, routes):
        """
        Try to update the default gateway.

        Args:
            routes: array format of default gateway list with priority.
                    For example:
                    ["wwan0", "eth0"]
        """
        ifaces = self.list_interfaces()
        if not ifaces:
            # FIXME: keep or clean?
            # self._update_default({})
            raise IPRouteError("Interfaces should be UP.")

        default = {}
        for iface in routes:
            if iface in ifaces:
                default["interface"] = iface
                break
        else:
            self._update_default({})
            return

        # find gateway by interface
        default.update(self._interfaces[default["interface"]])

        current = self._get_default()
        if current.get("interface", "") != default.get("interface", "") or \
                current.get("gateway", "") != default.get("gateway", ""):
            self._update_default(default)

    def try_update_default(self, routes):
        with _update_default_lock:
            try:
                self._try_update_default(routes)
            except IPRouteError as e:
                _logger.debug(e)

    def _set_default(self, default):
        """
        Update default gateway by given info.
        """
        # TODO: unused, remove or keep?
        try:
            self._update_default(default)
        except Exception as e:
            # try database if failed
            try:
                self.try_update_default(self._routes)
            except IPRouteError as e2:
                _logger.debug(
                    "Failed to recover the default gateway: {}".format(e2))
            error = "Update default gateway failed: {}".format(e)
            _logger.error(error)
            raise IPRouteError(error)

    def update_iface_db(self, iface):
        """
        Save the interface name with its gateway and update the default
        gateway if needed.

        If gateway is not specified, use the previous value. Only delete the
        gateway when gateway attribute is empty.

        Args:
            interface: dict format with interface "name" and/or "gateway".
        """
        iface["status"] = iface.get("status", True)
        iface["wan"] = iface.get("wan", True)

        if "actualIface" in iface:
            name = iface.get("actualIface")
            alias = iface.get("name", name)
        else:
            name = iface.get("name")
            alias = None
        if not name:
            error = "Interface name not given."
            _logger.error(error)
            raise IPRouteError(error)

        # update the router information
        if name not in self._interfaces:
            self._interfaces[name] = {}
        self._interfaces[name]["status"] = iface["status"]
        self._interfaces[name]["wan"] = iface["wan"]
        if "gateway" in iface:
            self._interfaces[name]["gateway"] = iface["gateway"]
        if alias:
            self._interfaces[name]["alias"] = alias

        # update interface list
        self._routes = self._get_priority_list()

        # check if the default gateway need to be modified
        self.try_update_default(self._routes)

    def get_iface_db(self):
        db = {}
        for iface in self._interfaces.keys():
            item = self._interfaces[iface]
            if "alias" not in item:
                db[iface] = item.copy()
            else:
                db[item["alias"]] = {
                    "status": item.get("status", True),
                    "wan": item.get("wan", True),
                    "gateway": item.get("gateway", ""),
                    "actualIface": iface
                }
        return db


if __name__ == "__main__":
    FORMAT = "%(asctime)s - %(levelname)s - %(lineno)s - %(message)s"
    logging.basicConfig(level=0, format=FORMAT)
    _logger = logging.getLogger("sanji.route")

    path = "/usr/lib/sanji-1.0.bak/route"
    route = IPRoute(name="route", path=path)
