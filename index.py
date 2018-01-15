#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import logging
import os

from sanji.core import Sanji
from sanji.core import Route
from sanji.connection.mqtt import Mqtt
from voluptuous import Schema
from voluptuous import Any, Required, Optional, Length, REMOVE_EXTRA
from route import IPRoute


class Index(Sanji):
    _logger = logging.getLogger("sanji.route.index")

    GET_DEFAULT_SCHEMA = Schema({
        Optional("interface"): Any(str, unicode, Length(1, 255)),
        Optional("gateway"): Any(str, unicode, Length(1, 255)),
        Required("priorityList"): [Any(str, unicode, Length(1, 255))]
    }, extra=REMOVE_EXTRA)

    PUT_DEFAULT_SCHEMA = Schema({
        Required("priorityList"): [Any(str, unicode, Length(1, 255))]
    }, extra=REMOVE_EXTRA)

    def init(self, *args, **kwargs):
        path_root = os.path.abspath(os.path.dirname(__file__))

        self.route = IPRoute(
            name="route",
            path=path_root)
        self.route.set_wan_event_cb(self.update_wan_info)

    def update_wan_info(self, interface):
        """
        Update WAN interface to default gateway's interface.

        Args:
            default: interface name
        """
        self.publish.event.put("/network/wan", data={"interface": interface})

    @Route(methods="get", resource="/network/routes/default")
    def get_default(self, message, response):
        data = self.route.get_default()
        if data is None:
            data = {}
        data["priorityList"] = self.route.get_priority_list()
        return response(data=Index.GET_DEFAULT_SCHEMA(data))

    @Route(methods="put", resource="/network/routes/default")
    def put_default(self, message, response,
                    schema=PUT_DEFAULT_SCHEMA):
        """
        Update the default gateway, delete default gateway if data is None or
        empty.
        """
        data = {}
        try:
            data["priorityList"] = \
                self.route.set_priority_list(message.data["priorityList"])
        except Exception as e:
            return response(code=404,
                            data={"message": e})

        return response(data=data)

    @Route(methods="put", resource="/network/routes/db")
    def _update_db(self, message, response):
        """
        Update router database batch or by interface.
        """
        if type(message.data) is list:
            for iface in message.data:
                self.route.update_iface_db(iface)
            return response(data=self.interfaces)
        elif type(message.data) is dict:
            self.route.update_iface_db(message.data)
            return response(data=message.data)
        return response(code=400,
                        data={"message": "Wrong type of router database."})

    @Route(methods="get", resource="/network/routes/db")
    def _get_db(self, message, response):
        return response(data=self.route.get_iface_db())

    @Route(methods="put", resource="/network/interfaces/:name")
    def _event_update_db(self, message):
        message.data["name"] = message.param["name"]
        self.route.update_router_db(message.data)


if __name__ == "__main__":
    FORMAT = '%(asctime)s - %(levelname)s - %(lineno)s - %(message)s'
    logging.basicConfig(level=0, format=FORMAT)
    logging.getLogger("sh").setLevel(logging.WARN)
    index = Index(connection=Mqtt())
    index.start()

    '''
    path_root = os.path.abspath(os.path.dirname(__file__))
    route = IPRoute(name="route", path=path_root)

    '''
