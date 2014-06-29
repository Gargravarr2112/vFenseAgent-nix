#!/usr/bin/env python
import sys
import os
import time
import inspect
import urllib2

from src.utils import settings
from src.utils import logger

deps_dir = os.path.join(settings.AgentDirectory, 'deps')
sys.path.append(deps_dir)

from net.netmanager import NetManager
from serveroperation.operationmanager import OperationManager
from agentplugin import AgentPlugin


class MainCore():

    def __init__(self, app_name):

        if os.geteuid() != 0:
            sys.exit("vFense Agent must be run as root.")

        self.app_name = app_name
        self.registered_plugins = {}

        settings.initialize(self.app_name)

        self.found_plugins = self.load_plugins(settings.PluginDirectory)
        self.register_plugins()

    def run(self):

        logger.info("Starting %s." % self.app_name)

        while not self.internet_on():
            time.sleep(10)

        operation_manager = OperationManager(self.registered_plugins)

        net_manager = NetManager()
        net_manager.incoming_callback(
            operation_manager.server_response_processor
        )

        operation_manager.send_results_callback(
            net_manager.send_message_register_response
        )

        # Gives ability to get immediate results for a call to the server
        operation_manager.send_message_callback(net_manager.send_message)

        for plugin in self.registered_plugins.values():
            plugin.start()

        operation_manager.start()

        # Start the checkin timer after everything else is started
        net_manager.start()

        logger.info("Ready up.")

        while True:
            time.sleep(3)

    def load_plugins(self, plugin_dir):
        sys.path.append(plugin_dir)
        plugins = []

        packages = {}
        for name in os.listdir(plugin_dir):
            if os.path.isdir(os.path.join(plugin_dir, name)):
                packages[os.path.join(plugin_dir, name)] = name

        modules = []
        for package in packages:
            for _file in os.listdir(package):
                if _file[-3:] == '.py':
                    modules.append("%s.%s" % (packages[package], _file[:-3]))

        imported_packages = set([__import__(name) for name in modules
                                 if name not in sys.modules])

        for package in imported_packages:
            for module in package.__dict__.values():
                if inspect.ismodule(module):
                    for _class in module.__dict__.values():
                        if inspect.isclass(_class) and \
                           _class.__module__ != 'agentplugin':

                            try:

                                plug = _class()

                                if isinstance(plug, AgentPlugin):
                                    plugins.append(plug)

                            except Exception as e:

                                logger.debug(
                                    'Unable to import module %s. Skipping.'
                                    % _class.__module__
                                )
                                logger.exception(e)

        return plugins

    def register_plugins(self):
        for plugin in self.found_plugins:
            self.registered_plugins[plugin.name()] = plugin

    def internet_on(self):
        try:
            urllib2.urlopen('http://www.google.com', timeout=3)
            logger.debug('Internet connection detected.')
            return True

        except Exception as e:
            logger.debug('No internet connection detected.')
            logger.exception(e)
            return False
