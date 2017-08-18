import os
import json
import requests

from src.utils import settings, logger, RepeatTimer
from serveroperation.sofoperation import OperationKey, OperationValue, \
    RequestMethod, ResponseUris


_message_delimiter = '<EOF>'

allow_checkin = True


class NetManager():
    """Used to talk to the server."""

    def __init__(self, seconds_to_checkin=60):
        """
        Args:
            seconds_to_checkin (int): Defines the interval, in seconds, to
            check-in to the server. Defaults to 60 seconds.
        """

        self._server_url = 'https://{0}:{1}/'.format(
            settings.ServerAddress, settings.ServerPort
        )

        self._timer = RepeatTimer(seconds_to_checkin, self._agent_checkin)

    def start(self):
        """Starts the repeating timer that checks-in to the server at
        set intervals.
        """
        self._timer.start()

    def incoming_callback(self, callback):
        """Sets the callback to be used when operations were received during
        agent check-in.

        Args:
            callback (function): The function which will be called back on
                server response. This function must accept one dictionary
                argument.

        """
        self._incoming_callback = callback

    def _agent_checkin(self):
        """Checks in to the server to retrieve all pending operations."""

        if allow_checkin:
            root = {
                OperationKey.Operation: OperationValue.CheckIn,
                OperationKey.OperationId: '',
                OperationKey.AgentId: settings.AgentId
            }

            success = self.send_message(
                json.dumps(root),
                ResponseUris.get_response_uri(OperationValue.CheckIn),
                ResponseUris.get_request_method(OperationValue.CheckIn)
            )

            if not success:
                logger.error(
                    "Could not check-in to server. See logs for details."
                )

        else:
            logger.info("Checkin set to false.")

    def _get_request_method(self, req_method):
        """Use to get the appropriate request method to talk to the server.

        Args:
            (str) req_method: The request method that is wanted
                (Ex: POST, PUT, GET)

        Returns:
            (method) The corresponding requests method that matches what was
            passed in arguments.
        """
        if req_method == RequestMethod.POST:
            return self.http_session.post
        if req_method == RequestMethod.PUT:
            return self.http_session.put
        if req_method == RequestMethod.GET:
            return self.http_session.get

    def login(self):
        """Logs in to the vFense server with the required credentials using
        a requests session which stores all cookies."""

        try:
            url = os.path.join(
                self._server_url,
                ResponseUris.get_response_uri(OperationValue.Login)
            )

            logger.debug("Logging into: {0}".format(url))

            self.http_session = requests.session()

            headers = {'content-type': 'application/json'}
            payload = {
                'name': settings.Username,
                'password': settings.Password
            }

            request_method = self._get_request_method(RequestMethod.POST)

            response = request_method(
                url,
                data=json.dumps(payload),
                headers=headers,
                verify=False,
                timeout=30
            )

            logger.debug("Login status code: %s " % response.status_code)
            logger.debug("Login server text: %s " % response.text)

            if response.status_code == 200:

                return True

        except Exception as e:

            logger.error("Agent was unable to login.")
            logger.exception(e)

        return False

    def send_message(self, data, uri, req_method):
        """Sends a message to the server and waits for data in return. If
        successful data retrieval then it calls the callback method with
        the data.

        Args:
            data (str): JSON formatted str to send the server.
            uri (str): RESTful uri to send the data.
            req_method (str): HTTP Request Method

        Returns:
            (bool) True if message was sent successfully, False otherwise.

        """

        logger.debug('Sending message to server')

        url = os.path.join(self._server_url, uri)
        headers = {'content-type': 'application/json'}
        payload = data
        sent = False

        logger.debug("Sending message to: {0}".format(url))

        try:

            if not self.login():
                logger.error("Agent was unable to login.")
                return False

            request_method = self._get_request_method(req_method)

            response = request_method(
                url,
                data=payload,
                headers=headers,
                verify=False,
                timeout=30
            )

            logger.debug("Url: %s " % url)
            logger.debug("Status code: %s " % response.status_code)
            logger.debug("Server text: %s " % response.text)

            if response.status_code == 200:

                sent = True

            received_data = []
            try:
                received_data = response.json()

            except Exception as e:
                logger.error("Unable to read data from server. Invalid JSON?")
                logger.exception(e)

            self._incoming_callback(received_data)

        except Exception as e:

            logger.error("Unable to send data to server.")
            logger.exception(e)

        return sent
