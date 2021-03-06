import shutil
import os
import platform
import glob
import json
import urllib2

from agentplugin import AgentPlugin
from src.utils import RepeatTimer, settings, logger, systeminfo, uninstaller, \
    throd
from src.serveroperation.sofoperation import SofOperation, OperationKey, \
    OperationValue

from patching.data.application import AppUtils
from patching.agent_update_retriever import AgentUpdateRetriever
from patching.patchingsofoperation import PatchingSofOperation, \
    PatchingError, PatchingOperationValue, PatchingOperationKey, \
    PatchingSofResult

import patchingformatter


class PatchingPlugin(AgentPlugin):

    # TODO: rename to patching
    Name = 'rv'

    def __init__(self):

        self._name = PatchingPlugin.Name
        self._update_directory = settings.UpdatesDirectory
        self._operation_handler = self._get_op_handler()
        self.uninstaller = uninstaller.Uninstaller()
        self.throd = throd.ThrottleDownload()

    def _get_op_handler(self):

        plat = systeminfo.get_os_code()

        if plat == 'darwin':
            from operationhandler.machandler import MacOpHandler
            return MacOpHandler()

        elif plat == 'linux':
            distro = platform.linux_distribution()[0].lower()

            # List to check RedHat derived distros that use yum.
            _redhat = 'red hat enterprise linux server'
            _rpm_distros = ['fedora', 'centos', 'centos linux']
            _debian_distros = ['debian', 'ubuntu', 'linuxmint', 'devuan']

            if distro == _redhat:
                from operationhandler.rhelhandler import RhelOpHandler
                logger.debug('Using RhelOpHandler.')
                return RhelOpHandler()

            if distro in _rpm_distros:
                from operationhandler.rpmhandler import RpmOpHandler
                logger.debug('Using RpmOpHandler.')
                return RpmOpHandler()

            elif distro in _debian_distros:
                from operationhandler.debhandler import DebianHandler
                logger.debug('Using DebianHandler.')
                return DebianHandler()
        else:
            logger.critical(
                "Current platform '%s' isn't supported. Ignoring operations." %
                plat
            )
            return None

    def start(self):
        """ Runs once the agent core is initialized.
        @return: Nothing
        """

        # 43200 seconds == 12 hours
        self._timer = RepeatTimer(43200, self.run_refresh_apps_operation)
        self._timer.start()

    def stop(self):
        """ Runs once the agent core is shutting down.
        @return: Nothing
        """
        logger.error("stop() method not implemented.")

    def run_operation(self, operation):
        """ Executes an operation given to it. """

        if not isinstance(operation, PatchingSofOperation):
            operation = PatchingSofOperation(operation.raw_operation)

        try:

            operation_methods = {

                PatchingOperationValue.InstallUpdate:
                    self._install_operation,
                PatchingOperationValue.InstallSupportedApps:
                    self._install_operation,
                PatchingOperationValue.InstallCustomApps:
                    self._install_operation,
                PatchingOperationValue.InstallAgentUpdate:
                    self._install_operation,
                PatchingOperationValue.Uninstall:
                    self._uninstall_operation,
                PatchingOperationValue.UninstallAgent:
                    self._uninstall_agent_operation,
                PatchingOperationValue.UpdatesAvailable:
                    self.available_updates_operation,
                PatchingOperationValue.ApplicationsInstalled:
                    self.installed_applications_operation,
                PatchingOperationValue.RefreshApps:
                    self.refresh_apps_operation,
                PatchingOperationValue.AvailableAgentUpdate:
                    self.available_agent_update_operation,
                PatchingOperationValue.AgentLogRetrieval:
                    self.retrieve_agent_log,
                PatchingOperationValue.ExecuteCommand:
                    self.execute_command

            }

            # Calling method
            operation_methods[operation.type](operation)

        except KeyError as ke:
            logging_message = (
                "Received unrecognized operation: {0}"
                .format(operation.__dict__)
            )
            logger.error(logging_message)
            logger.exception(ke)

            raise Exception(logging_message)

    def _get_install_method(self, operation_type):
        installation_methods = {

            PatchingOperationValue.InstallUpdate:
                self._operation_handler.install_update,
            PatchingOperationValue.InstallSupportedApps:
                self._operation_handler.install_supported_apps,
            PatchingOperationValue.InstallCustomApps:
                self._operation_handler.install_custom_apps,
            PatchingOperationValue.InstallAgentUpdate:
                self._operation_handler.install_agent_update

        }

        return installation_methods[operation_type]

    def _restart_if_needed(self, operation_restart, restart_needed):
        restart = False

        if operation_restart == PatchingOperationValue.ForcedRestart:
            restart = True

        elif (operation_restart == PatchingOperationValue.OptionalRestart and
              restart_needed):
            restart = True

        if restart:
            restart_op = SofOperation()
            restart_op.type = OperationValue.Reboot
            self._register_operation(restart_op)

    def _install_operation(self, operation):

        # TODO: if operation specifies update directory, change to that
        update_dir = settings.UpdatesDirectory
        failed_to_download = False

        try:

            self._download_packages(operation)

        except Exception as e:
            logger.error("Error occured while downloading updates.")
            logger.exception(e)

            failed_to_download = True

        # TODO: rework this, the server must take a failure of
        # the operation as a whole.
        if not operation.install_data_list or failed_to_download:
            error = PatchingError.UpdatesNotFound

            if failed_to_download:
                error = 'Failed to download packages.'

            patchingsof_result = PatchingSofResult(
                operation.id,
                operation.type,
                '',  # app id
                [],  # apps_to_delete
                [],  # apps_to_add
                'false',  # success
                'false',  # restart
                error,  # error
                AppUtils.null_application().to_dict()  # app json
            )

            self._send_results(patchingsof_result)

        else:

            if operation.type == PatchingOperationValue.InstallAgentUpdate:
                self._agent_update(operation, update_dir)
            else:
                self._regular_update(operation, update_dir)

    def _regular_update(self, operation, update_dir):
        install_method = self._get_install_method(operation.type)

        restart_needed = False

        for install_data in operation.install_data_list:

            install_result = install_method(install_data, update_dir)

            if install_result.restart == 'true':
                restart_needed = True

            patchingsof_result = PatchingSofResult(
                operation.id,
                operation.type,
                install_data.id,  # app id
                install_result.apps_to_delete,  # apps_to_delete
                install_result.apps_to_add,  # apps_to_add
                install_result.successful,  # success
                install_result.restart,  # restart
                install_result.error,  # error
                install_result.app_json  # app json
            )

            self._send_results(patchingsof_result)

        # TODO(urgent): should I call a handlers cleaning method from here?

        if os.path.isdir(self._update_directory):
            shutil.rmtree(self._update_directory)

        logger.info('Done installing updates.')

        self._restart_if_needed(operation.restart, restart_needed)

    def _agent_update(self, operation, update_dir):
        install_method = self._get_install_method(operation.type)
        # TODO(urgent): remove this, only for testing
        #install_method = self._operation_handler.install_agent_update

        restart_needed = False

        for install_data in operation.install_data_list:
            install_result = install_method(
                install_data, operation.id, update_dir
            )

            if install_result.restart == 'true':
                restart_needed = True

            patchingsof_result = PatchingSofResult(
                operation.id,
                operation.type,
                install_data.id,  # app id
                install_result.apps_to_delete,  # apps_to_delete
                install_result.apps_to_add,  # apps_to_add
                install_result.successful,  # success
                install_result.restart,  # restart
                install_result.error,  # error
                install_result.app_json  # app json
            )

            if patchingsof_result.success != '':
                self._send_results(patchingsof_result)

        #if os.path.isdir(self._update_directory):
        #    shutil.rmtree(self._update_directory)

        logger.info('Done attempting to update agent.')

        self._restart_if_needed(operation.restart, restart_needed)

    def _get_pkg_sizes(self, pkgs_path):
        pkg_names = os.listdir(pkgs_path)
        pkg_sizes = {}

        for pkg in pkg_names:
            pkg_sizes[pkg] = os.path.getsize(os.path.join(pkgs_path, pkg))

        return pkg_sizes

    def _check_if_downloaded(self, file_path, expected_size):
        file_name = os.path.basename(file_path)
        downloaded_size = os.path.getsize(file_path)

        if isinstance(expected_size, basestring):
            logger.debug("expected_size is instance of basestring.")

            try:
                expected_size = int(expected_size)
            except:
                logger.error("Failed to convert expected_size to int.")

        if downloaded_size == expected_size:
            logger.debug(
                "{0} IS the right size: {1} == {2}"
                .format(file_name, downloaded_size, expected_size)
            )

            return True

        else:
            logger.critical(
                "{0} is NOT the right size: {1} != {2}"
                .format(file_name, downloaded_size, expected_size)
            )

            logger.debug(
                "types: {0} and {1}".format(
                    type(downloaded_size), type(expected_size)
                )
            )

        return False

    def _uninstall_agent_operation(self, operation):
        logger.debug("Attempting to uninstall agent.")
        self.uninstaller.uninstall()
        logger.debug("Done attempting to uninstall agent.")

    def _uninstall_operation(self, operation):
        restart_needed = False

        if not operation.uninstall_data_list:
            error = "No applications specified to uninstall."

            patchingsof_result = PatchingSofResult(
                operation.id,
                operation.type,
                '',  # app id
                [],  # apps_to_delete
                [],  # apps_to_add
                'false',  # success
                'false',  # restart
                error,  # error
                []  # data
            )

            self._send_results(patchingsof_result)

        else:

            for uninstall_data in operation.uninstall_data_list:

                uninstall_result = \
                    self._operation_handler.uninstall_application(
                        uninstall_data
                    )

                if uninstall_result.restart == 'true':
                    restart_needed = True

                patchingsof_result = PatchingSofResult(
                    operation.id,
                    operation.type,
                    uninstall_data.id,  # app id
                    [],  # apps_to_delete
                    [],  # apps_to_add
                    uninstall_result.success,  # success
                    uninstall_result.restart,  # restart
                    uninstall_result.error,  # error
                    []  # data
                )

                self._send_results(patchingsof_result)

        logger.info('Done uninstalling applications.')

        self.run_refresh_apps_operation()

        # TODO(urgent): get restart working for uninstalls
        try:
            self._restart_if_needed(operation.restart, restart_needed)
        except AttributeError:
            logger.error(
                "Failed to check if restart was needed due to no "
                "restart attribute in operation."
            )

    def _check_if_updated(self):
        logger.info("Checking if agent updated.")
        update_result = {}

        try:

            if os.path.exists(settings.update_file):
                with open(settings.update_file, 'r') as _file:
                    update_result = json.load(_file)

                app_id = update_result['app_id']
                operation_id = update_result['operation_id']
                success = update_result['success']
                error = update_result.get('error', '')

                patchingsof_result = PatchingSofResult(
                    operation_id,
                    PatchingOperationValue.InstallAgentUpdate,
                    app_id,  # app id
                    [],  # apps_to_delete
                    [],  # apps_to_add
                    success,  # success
                    'false',  # restart
                    error,  # error
                    "{}"  # app json
                )

                logger.info(patchingsof_result.__dict__)

                self._send_results(patchingsof_result)

                os.remove(settings.update_file)

        except Exception as e:
            logger.error("Failure while sending agent update result.")
            logger.exception(e)

    def initial_data(self, operation_type):
        """
        Retrieves current installed applications and available updates.

        Args:
            operation_type - The type of operation determines what the plugin
                             should return.

        Returns:
            (dict) Dictionary contains all installed and available
            applications.

        """
        self._check_if_updated()

        if operation_type == OperationValue.Startup:
            self.run_refresh_apps_operation()

            return None

        data = {
            'data': self.refresh_apps()
        }

        return data

    def name(self):
        """ Retrieves the name for this plugin.
        @return: Nothing
        """

        return self._name

    def send_results_callback(self, callback):
        """ Sets the callback used to send results back to the server.
        @requires: Nothing
        """

        self._send_results = callback

    def register_operation_callback(self, callback):
        """ Sets the callback used to register/save operations with the agent
        core.
        @requires: Nothing
        """

        self._register_operation = callback

    def _recreate_db_tables(self):
        """ Drops all tables and calls respected methods to populate them with
        new data.
        @return: Nothing
        """

        self._operation_handler.get_available_updates()
        self._operation_handler.get_installed_updates()
        self._operation_handler.get_installed_applications()

    def _download_file(self, uri, download_dir, dl_rate=None):
        """
        Loops through all the file_uris provided and terminates when
        downloaded successfully or exhausts the file_uris list.

        Returns:
            (bool) - Successful download.
        """
        file_uris = uri[PatchingOperationKey.FileUris]
        file_size = uri[PatchingOperationKey.FileSize]

        # Loop through each possible uri for the package
        for file_uri in file_uris:
            logger.debug("Downloading from: {0}".format(file_uri))

            file_name = os.path.basename(file_uri)
            download_path = os.path.join(download_dir, file_name)

            try:
                self.throd.set_rate(dl_rate)
                self.throd.download(file_uri, download_path)

                if self._check_if_downloaded(download_path, file_size):
                    logger.debug("Downloaded successfully.")
                    return True
                else:
                    logger.error(
                        "Failed  to download from: {0}".format(file_uri)
                    )

            except Exception as dlerr:
                logger.error("Failed  to download from: {0}".format(file_uri))
                logger.exception(dlerr)

                continue

        logger.debug("Failed to download.")

        return False

    def _download_packages(self, operation):
        """ Download packages from the urls provided in the 'operation'
         parameter.

        Args:
            - operation: Operation to be worked with.

        Returns:
            Nothing

        """

        if not os.path.isdir(self._update_directory):
            os.mkdir(self._update_directory)

        # Loop through every app
        for install_data in operation.install_data_list:
            app_dir = os.path.join(self._update_directory, install_data.id)

            if os.path.isdir(app_dir):
                shutil.rmtree(app_dir)

            try:
                os.mkdir(app_dir)

                install_data.downloaded = True

                # Loop through the individual packages that make up the app
                for uri in install_data.uris:
                    logger.debug(
                        "File uris: {0}".format(
                            uri[PatchingOperationKey.FileUris]
                        )
                    )

                    dl_success = self._download_file(
                        uri, app_dir, operation.net_throttle
                    )
                    if not dl_success:
                        # On failure to download a single file, quit.
                        install_data.downloaded = False
                        break

                if install_data.downloaded:
                    # Known file extensions to work on.
                    self._untar_files(app_dir)
                    self._unzip_files(app_dir)

            except Exception as e:
                logger.error(
                    "Failed while downloading update {0}."
                    .format(install_data.name)
                )
                logger.exception(e)

                logger.debug(
                    "Setting downloaded to false for: " + install_data.name
                )
                install_data.downloaded = False

    def _untar_files(self, directory):
        """ Scans a directory for any tar files and 'untars' them. Scans
        recursively just in case there's tars within tars. Deletes tar files
        when done.

        @param directory: Directory to be scanned.
        @return: Nothing
        """

        tars = glob.glob(os.path.join(directory, '*.tar*'))

        if not tars:
            return

        import tarfile

        try:
            for tar_file in tars:
                tar = tarfile.open(tar_file)
                tar.extractall(path=directory)
                tar.close()
                os.remove(tar_file)

            self._untar_files(directory)

        except OSError as e:
            logger.info("Could not extract tarball.")
            logger.exception(e)

    def _unzip_files(self, directory):
        zips = glob.glob(os.path.join(directory, '*.zip'))

        if not zips:
            return

        import zipfile

        try:
            for zip_file in zips:
                zip = zipfile.ZipFile(zip_file)
                zip.extractall(directory)
                zip.close()
                os.remove(zip_file)

            self._unzip_files(directory)

        except OSError as e:
            logger.info("Could not extract zipfile.")
            logger.exception(e)

    def available_updates_operation(self, operation):
        operation.applications = self.get_available_updates()
        operation.raw_result = patchingformatter.applications(operation)

        return operation

    def get_available_updates(self):
        """ Wrapper around the operation handler's call to get available
         updates.
        """

        return self._operation_handler.get_available_updates()

    def installed_applications_operation(self, operation):
        operation.applications = self.get_applications_installed()
        operation.raw_result = patchingformatter.applications(operation)

        return operation

    def get_applications_installed(self):
        """ Wrapper around the operation handler's call to get installed
         applications.
        """
        apps = []

        apps.extend(self._operation_handler.get_installed_applications())
        apps.extend(self._operation_handler.get_installed_updates())

        return apps

    def get_agent_app(self):
        try:
            agent_app = AppUtils.create_app(
                settings.AgentName,
                settings.AgentVersion,
                settings.AgentDescription,  # description
                [],  # file_data
                [],  # dependencies
                '',  # support_url
                '',  # vendor_severity
                '',  # file_size
                '',  # vendor_id,
                '',  # vendor_name
                settings.AgentInstallDate,  # install_date
                None,  # release_date
                True,  # installed
                "",  # repo
                "no",  # reboot_required
                "no"  # uninstallable
            )

            return agent_app

        except Exception as e:
            logger.error("Failed to create agent application instance.")
            logger.exception(e)

            return {}

    def refresh_apps(self):
        applications = self.get_installed_and_available_applications()

        data = []
        for app in applications:
            data.append(app.to_dict())

        agent_app = self.get_agent_app()
        if agent_app:
            data.append(agent_app.to_dict())

        self.run_available_agent_update_operation()

        return data

    def refresh_apps_operation(self, operation):
        raw = {}

        # TODO: don't hardcode
        if not operation.id.endswith('-agent'):
            raw[OperationKey.OperationId] = operation.id

        raw[OperationKey.Data] = self.refresh_apps()

        operation.raw_result = json.dumps(raw)

        self._send_results(operation)

    def run_refresh_apps_operation(self):
        """Creates and runs a refresh apps operation.

        Returns:
            Nothing

        """

        operation = PatchingSofOperation()
        operation.type = PatchingOperationValue.RefreshApps

        self._register_operation(operation)

    def check_for_agent_update(self):
        agent_version = settings.AgentVersion.split('-')
        version_string = agent_version[0]
        platform = agent_version[1]
        agent_update = AgentUpdateRetriever.get_available_agent_update(platform, version_string)

        if agent_update:
            return agent_update.to_dict()

        return {}

    def available_agent_update_operation(self, operation):
        raw = {}

        # TODO: don't hardcode
        if not operation.id.endswith('-agent'):
            raw[OperationKey.OperationId] = operation.id

        agent_update = self.check_for_agent_update()

        if agent_update:
            raw[OperationKey.Data] = agent_update

            operation.raw_result = json.dumps(raw)

            self._send_results(operation)

    def run_available_agent_update_operation(self):
        operation = PatchingSofOperation()
        operation.type = PatchingOperationValue.AvailableAgentUpdate

        self._register_operation(operation)

    def get_installed_and_available_applications(self):
        """
        Wrapper around the operation handler's call to get available
        updates and installed applications.
        """

        apps = []

        apps.extend(self._operation_handler.get_installed_updates())
        apps.extend(self._operation_handler.get_installed_applications())
        apps.extend(self._operation_handler.get_available_updates())

        return apps

    def retrieve_agent_log(self, operation):
        """
        Adds the content from the log file, specified by date in operation,
        to the operation's raw_result. Date must be of format 'yyyy-mm-dd'.
        """

        # TODO: get date or date intervals
        date = None

        log_content = []
        try:
            logs = logger.retrieve_log_path(date)

            for log_path in logs:
                with open(log_path, 'r') as log_file:
                    log_content.append(log_file.read())

        except Exception as e:
            logger.error("Failed to retrieve log file.")
            logger.exception(e)

        operation.raw_result = ''.join(log_content)

        return operation

    def execute_command(self, operation):
        """ Execute command line command from operation. """
        pass

if __name__ == "__main__":
    print ("This plugin is not meant to be run directly."
           " Please run it with the core vFense agent.")
