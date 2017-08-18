import time
import os
import subprocess
import datetime
import json

from net import netmanager
from threading import Thread
from data.sqlitemanager import SqliteManager
from src.utils import systeminfo, settings, logger, queuesave
from serveroperation.sofoperation import SofOperation, OperationKey, \
    OperationValue, ResultOperation, ResponseUris


class OperationManager():

    def __init__(self, plugins):
        # Must be called first! Especially before any sqlite stuff.
        self._sqlite = SqliteManager()

        self._plugins = plugins
        self._load_plugin_handlers()

        self._operation_queue = queuesave.load_operation_queue()
        self._result_queue = queuesave.load_result_queue()

        self._operation_queue_thread = \
            Thread(target=self._operation_queue_loop)
        self._operation_queue_thread.daemon = True
        self._operation_queue_thread.start()

        self._result_queue_thread = Thread(target=self._result_queue_loop)
        self._result_queue_thread.daemon = True
        self._result_queue_thread.start()

        # Callback function for results set by core
        self._send_results = None

    def _load_plugin_handlers(self):
        for plugin in self._plugins.values():
            plugin.send_results_callback(self.add_to_result_queue)
            plugin.register_operation_callback(self.register_plugin_operation)

    def _save_and_send_results(self, operation_type, operation_result):

        # Check for self assigned operation IDs and send emtpy string
        # to server if present.
        #op_id = operation.id
        #if SelfGeneratedOpId in operation.id:
        #    operation.id = ""

        response_uri = ResponseUris.get_response_uri(operation_type)
        request_method = ResponseUris.get_request_method(operation_type)

        if not response_uri or not request_method:
            logger.debug(
                ("Could not find response uri or request method for '{0}'; "
                 "response_uri = {1}, request_method = {2}")
                .format(operation_type, response_uri, request_method)
            )

            return False

        result = self._send_results(
            operation_result,
            response_uri,
            request_method
        )

        #operation.id = op_id

        # Result was actually sent, write to db
        #if result:
        #    self._sqlite.add_result(operation, result, datetime.datetime.now())

        return result

    def register_plugin_operation(self, message):
        """ Provides a way for plugins to store their custom made
        operations with the agent core.

        Args:

            - message: Operation as a JSON formatted string.

        Returns:

            - True if operation was added successfully. False otherwise.
        """

        self.add_to_operation_queue(message)

        return True

    def _major_failure(self, operation, exception):

        if operation:
            root = {}
            root[OperationKey.Operation] = operation.type
            root[OperationKey.OperationId] = operation.id
            root[OperationKey.AgentId] = settings.AgentId

            root['error'] = str(exception)

            operation.raw_result = json.dumps(root)

            self.add_to_result_queue(operation)

        else:
            # TODO: Should we send something back to server?
            logger.critical("Operation is empty in _major_failure")

    ################# THE BIG OPERATION PROCESSOR!! ###########################
    ###########################################################################
    def process_operation(self, operation):

        try:
            if not isinstance(operation, SofOperation):
                operation = SofOperation(operation)

        except Exception as e:
            logger.error(
                "Failed to convert str to operation: {0}".format(operation)
            )
            logger.exception(e)
            self._major_failure(operation, e)
            return

        try:
            logger.info(
                "Process the following operation: {0}"
                .format(operation.__dict__)
            )

            self._sqlite.add_operation(operation, datetime.datetime.now())

            operation_methods = {
                OperationValue.SystemInfo: self.system_info_op,
                OperationValue.NewAgent: self.new_agent_op,
                OperationValue.Startup: self.startup_op,
                OperationValue.NewAgentId: self.new_agent_id_op,
                OperationValue.Reboot: self.reboot_op,
                OperationValue.Shutdown: self.shutdown_op,
                OperationValue.RefreshResponseUris: self.refresh_response_uris,
            }

            if operation.type in operation_methods:
                # Call method
                operation_methods[operation.type](operation)

            elif operation.plugin in self._plugins:
                self.plugin_op(operation)

            else:
                raise Exception(
                    'Operation/Plugin {0} was not found.'
                    .format(operation.__dict__)
                )

        except Exception as e:
            logger.error(
                "Error while processing operation: {0}"
                .format(operation.__dict__)
            )
            logger.exception(e)
            self._major_failure(operation, e)

    ###########################################################################
    ###########################################################################

    def system_info_op(self, operation):
        self.add_to_result_queue(self.system_info(operation))

    def new_agent_op(self, operation):
        operation = self._initial_data(operation)
        operation.raw_result = self._initial_formatter(operation)

        try:
            modified_json = json.loads(operation.raw_result)

            # Removing unnecessary keys from JSON
            del modified_json[OperationKey.OperationId]
            del modified_json[OperationKey.AgentId]

            operation.raw_result = json.dumps(modified_json)
        except Exception as e:
            logger.error("Failed to modify new agent operation JSON.")
            logger.exception(e)

        self.add_to_result_queue(operation)

    def startup_op(self, operation):
        operation = self._initial_data(operation)
        operation.raw_result = self._initial_formatter(operation)

        self.add_to_result_queue(operation)

    def new_agent_id_op(self, operation):
        self._new_agent_id(operation)

    def reboot_op(self, operation):
        netmanager.allow_checkin = False
        logger.info("Checkin set to: {0}".format(netmanager.allow_checkin))

        # Read by _check_for_reboot
        with open(settings.reboot_file, 'w') as _file:
            _file.write(operation.id)

        self._system_reboot(operation.reboot_delay_seconds / 60)

        reboot_failure_thread = Thread(
            target=self._reboot_failure, args=(operation,)
        )
        reboot_failure_thread.daemon = True
        reboot_failure_thread.start()

    def _reboot_failure(self, operation):
        """
        This method is run on a separate thread, it is only called after
        running the reboot operation. If it completely finishes, it means
        the computer was never rebooted, therefore the operation failed.
        """

        time.sleep(operation.reboot_delay_seconds + 120)

        ###############################################################
        #             *** Reboot should have occured ***              #
        ###############################################################

        self._reboot_result('false', operation.id, "Reboot was cancelled.")

        if os.path.exists(settings.reboot_file):
            os.remove(settings.reboot_file)

        netmanager.allow_checkin = True
        logger.debug("Checkin set to: {0}".format(netmanager.allow_checkin))

    def shutdown_op(self, operation):
        netmanager.allow_checkin = False
        logger.info("Checkin set to: {0}".format(netmanager.allow_checkin))

        # Read by _check_for_shutdown
        with open(settings.shutdown_file, 'w') as _file:
            _file.write(operation.id)

        # This offsets the shutdown by 15 seconds so that the agent check in
        # can occur and dump the operations to file.
        time.sleep(15)

        self._system_shutdown(operation.shutdown_delay_seconds / 60)

        shutdown_failure_thread = Thread(
            target=self._shutdown_failure, args=(operation,)
        )
        shutdown_failure_thread.daemon = True
        shutdown_failure_thread.start()

    def _shutdown_failure(self, operation):
        """
        This method is run on a separate thread, it is only called after
        running the shutdown operation. If it completely finishes, it means
        the computer was never shutdown, therefore the operation failed.
        """

        time.sleep(operation.reboot_delay_seconds + 120)

        ###############################################################
        #            *** Shutdown should have occured ***             #
        ###############################################################

        self._shutdown_result('false', operation.id, "Shutdown was cancelled.")

        if os.path.exists(settings.shutdown_file):
            os.remove(settings.shutdown_file)

        netmanager.allow_checkin = True
        logger.debug("Checkin set to: {0}".format(netmanager.allow_checkin))

    def refresh_response_uris(self, operation):
        if operation.data:
            ResponseUris.ResponseDict = operation.data
            logger.debug("Refreshed response uris.")

    def plugin_op(self, operation):
        self._plugins[operation.plugin].run_operation(operation)

    def _initial_data(self, operation):
        operation.core_data[OperationValue.SystemInfo] = self.system_info()
        operation.core_data[OperationValue.HardwareInfo] = self.hardware_info()

        for plugin in self._plugins.values():
            try:

                plugin_data = plugin.initial_data(operation.type)

                if plugin_data is not None:
                    operation.plugin_data[plugin.name()] = plugin_data

            except Exception as e:

                logger.error(
                    "Could not collect initial data for plugin %s." %
                    plugin.name()
                )
                logger.exception(e)

        return operation

    def _initial_formatter(self, operation):

        root = {}
        root[OperationKey.Operation] = operation.type
        root[OperationKey.Rebooted] = self._is_boot_up()
        root[OperationKey.CustomerName] = settings.Customer

        root[OperationKey.OperationId] = operation.id
        root[OperationKey.AgentId] = settings.AgentId

        #root[OperationKey.Core] = operation.core_data
        root.update(operation.core_data)

        root[OperationKey.Plugins] = operation.plugin_data

        return json.dumps(root)

    def _new_agent_id(self, operation):
        """ This will assign a new agent ID coming from the server.
        @return: Nothing
        """

        _id = operation.json_message[OperationKey.AgentId]
        settings.AgentId = _id
        settings.save_settings()

    def _reboot_result(self, success, operation_id, message=''):
        result_dict = {
            OperationKey.Operation: OperationValue.Reboot,
            OperationKey.OperationId: operation_id,
            OperationKey.Success: success,
            OperationKey.Message: message
        }

        operation = SofOperation()
        operation.type = OperationValue.Reboot
        operation.raw_result = json.dumps(result_dict)

        self.add_to_result_queue(operation)

    def _check_for_reboot(self):
        operation_id = ''

        if os.path.exists(settings.reboot_file):

            with open(settings.reboot_file, 'r') as _file:
                operation_id = _file.read()
                operation_id = operation_id.strip()

            # Clear the file in case the agent fails to delete it
            open(settings.reboot_file, 'w').close()

            try:
                os.remove(settings.reboot_file)
            except Exception as e:
                logger.error("Failed to remove reboot file.")
                logger.exception(e)

        if operation_id:
            self._reboot_result('true', operation_id)

    def _shutdown_result(self, success, operation_id, message=''):
        result_dict = {
            OperationKey.Operation: OperationValue.Shutdown,
            OperationKey.OperationId: operation_id,
            OperationKey.Success: success,
            OperationKey.Message: message
        }

        operation = SofOperation()
        operation.type = OperationValue.Shutdown
        operation.raw_result = json.dumps(result_dict)

        self.add_to_result_queue(operation)

    def _check_for_shutdown(self):
        operation_id = ''

        if os.path.exists(settings.shutdown_file):

            with open(settings.shutdown_file, 'r') as _file:
                operation_id = _file.read()
                operation_id = operation_id.strip()

            # Clear the file in case the agent fails to delete it
            open(settings.shutdown_file, 'w').close()

            try:
                os.remove(settings.shutdown_file)
            except Exception as e:
                logger.error("Failed to remove shutdown file.")
                logger.exception(e)

        if operation_id:
            self._shutdown_result('true', operation_id)

    def initial_data_sender(self):

        logger.info("Sending initial data.")

        operation = SofOperation()

        if settings.AgentId != "":
            self._check_for_reboot()
            self._check_for_shutdown()

            operation.type = OperationValue.Startup

        else:
            operation.type = OperationValue.NewAgent

        self.process_operation(operation)

    def send_results_callback(self, callback):
        self._send_results = callback

    def _plugin_not_found(self, operation):
        """
        Used when an operation needs a specific plugin which is not
        on the current machine. Notifies the server as well.
        """

        logger.error("No plugin support found")
        self._major_failure(operation, Exception("No plugin support found"))

    def operation_queue_file_dump(self):
        try:
            queuesave.save_operation_queue(self._operation_queue)
        except Exception as e:
            logger.error("Failed to save operation queue to file.")
            logger.exception(e)

    def add_to_operation_queue(self, operation):
        """
        Put the operation to file.

        Args:
            operation - The actual operation.

            no_duplicate - Will not put the operation in the queue if there
                           already exists an operation of the same type in
                           queue.

        Returns:
            (bool) True if able to put the operation in queue, False otherwise.

        """

        #if no_duplicate:
        #    return self._operation_queue.put_non_duplicate(operation)

        return self._operation_queue.put(operation)

    def _operation_queue_loop(self):

        while True:
            self.operation_queue_file_dump()

            try:
                operation = self._operation_queue.get()
                if operation:
                    self.process_operation(operation)
                    self._operation_queue.done()

                else:
                    # Only sleep if there is nothing in the queue.
                    # Keep banging (pause) them out!
                    time.sleep(4)

            except Exception as e:
                logger.error("Failure in operation queue loop.")
                logger.exception(e)

    def result_queue_file_dump(self):
        try:
            queuesave.save_result_queue(self._result_queue)

        except Exception as e:
            logger.error("Failed to save result queue to file.")
            logger.exception(e)

    def _result_queue_loop(self):

        while True:
            self.result_queue_file_dump()

            queue_dump = self._result_queue.queue_dump()

            should_send = [result_op for result_op in queue_dump
                           if result_op.should_be_sent()]

            if should_send:

                logger.debug("Results to be sent: {0}".format(should_send))

                for result_op in should_send:
                    # TODO: what should be done if fails to remove?
                    self._result_queue.remove(result_op)
                    self.process_result_operation(result_op)

                self._result_queue.done()

            else:
                #logger.debug(
                #    "Results in queue: {0}".format(queue_dump)
                #)
                time.sleep(4)

    def process_result_operation(self, result_op):
        """ Attempts to send the results in the result queue. """

        #operation = result_op.operation

        if result_op.should_be_sent():
            # No raw_result means it hasn't been processed
            if (result_op.operation_result != settings.EmptyValue):

                # Operation has been processed, send results to server
                send_result = self._save_and_send_results(
                    result_op.operation_type, result_op.operation_result
                )

                if (not send_result and result_op.retry):
                    # Time this out for a few
                    result_op.timeout()

                    # Failed to send result, place back in queue
                    self.add_to_result_queue(result_op)
            else:
                logger.debug(("Operation has not been processed, or"
                              " unknown operation was received."))
        else:
            self.add_to_result_queue(result_op)

    def add_to_result_queue(self, result_operation, retry=True):
        """
        Adds an operation to the result queue which sends it off to the server.

        Arguments:

        result_operation
            An operation which must have an operation type and raw_result
            attribute.

        retry
            Determines if the result queue should continue attempting to send
            the operation to the server in case of a non 200 response.

        """

        try:
            if not isinstance(result_operation, ResultOperation):
                result_operation = ResultOperation(result_operation, retry)

            return self._result_queue.put(result_operation)

        except Exception as e:
            logger.error("Failed to add result to queue.")
            logger.exception(e)

    def server_response_processor(self, message):

        if message:
            for op in message.get(OperationKey.Data, []):

                # Loading operation for server in order for the queue
                # dump to know if an operation is savable to file.

                try:
                    operation = SofOperation(json.dumps(op))
                    self.add_to_operation_queue(operation)

                except Exception as e:
                    logger.debug(
                        "Failed to create operation from: {0}".format(op)
                    )
                    logger.exception(e)

        self._save_uptime()

    def system_info(self):

        sys_info = {
            'os_code': systeminfo.get_os_code(),
            'os_string': systeminfo.get_os_string(),
            'version': systeminfo.get_version(),
            'bit_type': systeminfo.get_bit_type(),
            'computer_name': systeminfo.get_computer_name(),
            'machine_type': systeminfo.MachineType().get_machine_type(),
            'host_name': ''  # TODO: Implement
        }

        logger.debug(
            "System info sent: {0}".format(
                json.dumps(sys_info, indent=4)
            )
        )

        return sys_info

    def hardware_info(self):
        hardware_info = systeminfo.get_hardware_info()

        logger.debug("Hardware info sent: {0}".format(hardware_info))

        return hardware_info

    def _system_reboot(self, delay_minutes):

        self._save_uptime()

        warning = "In %s minute(s), this computer will be restarted " \
                  "on behalf of the vFense Server." % delay_minutes

        subprocess.call(
            ['/sbin/shutdown', '-r', '+%s' % delay_minutes, warning]
        )

    def _system_shutdown(self, delay_minutes):
        self._save_uptime()

        warning = "In %s minute(s), this computer will be shutdown " \
                  "on behalf of the vFense Server." % delay_minutes

        subprocess.call(
            ['/sbin/shutdown', '-h', '+%s' % delay_minutes, warning]
        )

    def _save_uptime(self):
        """Saves the current uptime to a simple text file in seconds.

        Returns:
            Nothing
        """

        uptime = systeminfo.uptime()

        if os.path.exists(settings.uptime_file):
            os.remove(settings.uptime_file)

        with open(settings.uptime_file, 'w') as f:
            f.write(str(uptime))

    def _is_boot_up(self):
        """ Checks if the agent is coming up because of a reboot. """

        current_uptime = systeminfo.uptime()
        boot_up = 'no'

        try:
            if os.path.exists(settings.uptime_file):

                with open(settings.uptime_file, 'r') as f:
                    file_uptime = f.read()

                    if current_uptime < float(file_uptime):
                        boot_up = 'yes'

        except Exception as e:
            logger.error("Could not verify system bootup.")
            logger.exception(e)

        return boot_up
