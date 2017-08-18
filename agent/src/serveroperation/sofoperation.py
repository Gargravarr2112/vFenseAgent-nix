import time
import uuid

from src.utils import settings
try:
    import simplejson as json
except ImportError:
    import json


class OperationValue():
    # The corresponding SOF equivalent. (Case sensitive)
    NewAgent = 'new_agent'
    NewAgentId = 'new_agent_id'
    Startup = 'startup'
    Reboot = 'reboot'
    Shutdown = 'shutdown'
    InvalidAgentId = 'invalid_agent_id'
    CheckIn = 'check_in'
    Login = 'login'
    Logout = 'logout'
    RefreshResponseUris = 'refresh_response_uris'

    SystemInfo = 'system_info'
    HardwareInfo = 'hardware'

    Result = 'result'


class OperationKey():

    Operation = 'operation'
    OperationId = 'operation_id'
    Plugin = 'plugin'
    Data = 'data'
    AgentId = 'agent_id'
    Success = 'success'
    Error = 'error'
    Reboot = 'reboot'
    Rebooted = 'rebooted'
    VendorId = 'vendor_id'
    CustomerName = 'customer_name'
    Message = 'message'

    Core = 'core'
    Plugins = 'plugins'

    ResponseUri = 'response_uri'
    RequestMethod = 'request_method'


class OperationError():
    pass


class RequestMethod():
    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'


class ResponseUris():

    # This dictionary is refreshed on a refresh response uri operation
    ResponseDict = {
        OperationValue.NewAgent: {
            OperationKey.ResponseUri: 'rvl/v1/core/newagent',
            OperationKey.RequestMethod: RequestMethod.POST
        },
        OperationValue.Startup: {
            OperationKey.ResponseUri: 'rvl/v1/{0}/core/startup',
            OperationKey.RequestMethod: RequestMethod.PUT
        },
        OperationValue.Login: {
            OperationKey.ResponseUri: 'rvl/login',
            OperationKey.RequestMethod: RequestMethod.POST
        }
    }

    @staticmethod
    def get_response_uri(operation_type):
        response_uri = ResponseUris.ResponseDict \
                       .get(operation_type, {}) \
                       .get(OperationKey.ResponseUri, '')

        # TODO: remove once uris are placed in database
        if operation_type == OperationValue.Startup:
            return response_uri.format(settings.AgentId)

        return response_uri

    @staticmethod
    def get_request_method(operation_type):
        return ResponseUris.ResponseDict \
            .get(operation_type, {}) \
            .get(OperationKey.RequestMethod, '')


SelfGeneratedOpId = '-agent'


class SofOperation(object):

    def __init__(self, message=None):

        self.data = []
        self.raw_result = settings.EmptyValue
        self.core_data = {}
        self.plugin_data = {}
        self.reboot_delay_seconds = 90
        self.shutdown_delay_seconds = 90
        self.error = settings.EmptyValue

        if message:
            self._load_message(message)

        else:
            self.json_message = settings.EmptyValue

            self.id = self.self_assigned_id()
            self.plugin = settings.EmptyValue
            self.type = settings.EmptyValue
            self.raw_operation = settings.EmptyValue

    def _load_message(self, message):
        self.json_message = json.loads(message)

        self.id = self.json_message.get(
            OperationKey.OperationId, self.self_assigned_id()
        )

        self.plugin = self.json_message.get(
            OperationKey.Plugin, settings.EmptyValue
        )

        self.type = self.json_message[OperationKey.Operation]
        self.data = self.json_message.get(OperationKey.Data, {})

        self.raw_operation = message

    def is_savable(self):
        non_savable = [OperationValue.Reboot, OperationValue.Shutdown,
                       OperationValue.Startup, OperationValue.NewAgent]

        if self.type in non_savable:
            return False

        return True

    def self_assigned_id(self):
        return str(uuid.uuid4()) + SelfGeneratedOpId

    def to_json(self):
        """Converts operation to a JSON formatted string.

        The string has the following keys:

        - Operation type
        - Operation ID
        - Plugin name if available. Empty string otherwise.

        Returns:

            - Returns the basic properties of an operation in JSON string.
        """

        json_dict = {
            OperationKey.Operation: self.type,
            OperationKey.OperationId: self.id,
            OperationKey.Plugin: self.plugin
        }

        return json.dumps(json_dict)


class ResultOperation():
    """ Allows you to set a timeout for sending results back.

        Presumably this result cannot be sent to server unless the current
        time since epoch is greater than that set in wait_until.
    """

    def __init__(self, operation, retry):
        """
        Arguments:

        operation
            An object which must contain a raw_result.

        retry
            Tells the result loop whether or not to retry sending results
            if a failure occurs on an attempt.

        timeout
        """

        self.id = self.self_assigned_id()
        self.type = OperationValue.Result
        self.retry = retry

        #self.operation = operation
        self.operation_type = operation.type
        self.operation_result = operation.raw_result

        # Sets current time, no timeout
        self.wait_until = self._time_in_seconds()

    def _time_in_seconds(self):
        """ Returns current time in seconds since epoch. """

        return int(time.time())

    def timeout(self, seconds=60):
        """
        Timeout this result by the amount specified in seconds.
        """

        self.wait_until = self._time_in_seconds() + seconds

    def should_be_sent(self):
        """ Says whether this result should be sent to server. """

        if self._time_in_seconds() > self.wait_until:
            return True

        return False

    def is_savable(self):
        non_savable = [OperationValue.Startup, OperationValue.NewAgent]

        #if self.operation.type in non_savable:
        if self.operation_type in non_savable:
            return False

        return True

    def self_assigned_id(self):
        return str(uuid.uuid4()) + SelfGeneratedOpId


class SofResult():
    """
    Data structure to pass around operation results.
    """
    def __init__(self, operation_id='', successful=False, restart=False,
                 message=''):

        self.id = operation_id
        self.successful = successful
        self.restart = restart
        self.specific_message = message
