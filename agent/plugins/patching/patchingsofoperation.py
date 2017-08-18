import json

from collections import namedtuple

from serveroperation.sofoperation import SofOperation, OperationError
from src.utils import logger


class PatchingOperationValue():
    # The corresponding SOF equivalent. (Case sensitive)
    InstallUpdate = 'install_os_apps'
    InstallSupportedApps = 'install_supported_apps'
    InstallCustomApps = 'install_custom_apps'
    InstallAgentUpdate = 'install_agent_update'

    InstallOperations = [InstallUpdate, InstallSupportedApps,
                         InstallCustomApps, InstallAgentUpdate]

    Uninstall = 'uninstall'
    UninstallAgent = 'uninstall_agent'
    UpdatesAvailable = 'updates_available'
    ApplicationsInstalled = 'applications_installed'
    RefreshApps = 'updatesapplications'
    AvailableAgentUpdate = 'available_agent_update'

    # TODO: implement
    AgentLogRetrieval = 'agent_log_retrieval'
    ExecuteCommand = 'execute_command'

    ThirdPartyInstall = 'third_party_install'

    IgnoredRestart = 'none'
    OptionalRestart = 'needed'
    ForcedRestart = 'force'


class PatchingOperationKey():
    # The corresponding SOF equivalent. (Case sensitive)
    Uri = 'uri'
    Uris = 'app_uris'
    Name = 'app_name'
    Hash = 'hash'
    AppId = 'app_id'
    Restart = 'restart'
    PackageType = 'pkg_type'
    CliOptions = 'cli_options'
    CpuThrottle = 'cpu_throttle'
    NetThrottle = 'net_throttle'
    ThirdParty = 'supported_third_party'

    FileData = 'file_data'
    FileHash = 'file_hash'
    FileName = 'file_name'
    FileUri = 'file_uri'
    FileUris = 'file_uris'
    FileSize = 'file_size'


class PatchingError(OperationError):

    UpdateNotFound = 'Update not found.'
    UpdatesNotFound = 'No updates found.'
    ApplicationsNotFound = 'No applications found.'


class CpuPriority():

    BelowNormal = 'below normal'
    Normal = 'normal'
    AboveNormal = 'above normal'
    Idle = 'idle'
    High = 'high'

    @staticmethod
    def get_niceness(throttle_value):
        niceness_values = {
            CpuPriority.Idle: 20,
            CpuPriority.BelowNormal: 10,
            CpuPriority.Normal: 0,
            CpuPriority.AboveNormal: -10,
            CpuPriority.High: -20
        }

        return niceness_values.get(throttle_value, 0)

    @staticmethod
    def acceptable_int_niceness(niceness):
        if (isinstance(niceness, int) and
            niceness >= CpuPriority.get_niceness(CpuPriority.High) and
            niceness <= CpuPriority.get_niceness(CpuPriority.BelowNormal)
           ):
            return True

        return False

    @staticmethod
    def niceness_to_string(niceness):
        if CpuPriority.acceptable_int_niceness(niceness):
            return str(niceness)

        try:
            if CpuPriority.acceptable_int_niceness(int(niceness)):
                return niceness
        except Exception:
            pass

        return '0'


class InstallData():

    def __init__(self):

        self.name = ""
        self.id = ""
        # TODO: remove uris when file_data is up and ready
        self.uris = []
        self.file_data = []
        self.third_party = False
        self.cli_options = ""
        self.downloaded = False
        self.proc_niceness = 0

    def __repr__(self):
        return "InstallData(name=%s, id=%s, uris=%s)" % (
            self.name, self.id, self.uris)


class UninstallData():

    def __init__(self):

        self.name = ""
        self.id = ""
        self.third_party = False
        self.cli_options = ""


class PatchingSofOperation(SofOperation):

    def __init__(self, message=None):
        super(PatchingSofOperation, self).__init__(message)

        # TODO: Fix hack. Lazy to use patchingplugin module because of
        # circular deps.
        # TODO: switch to patching
        self.plugin = 'rv'

        self.applications = []

        self.cpu_priority = self._get_cpu_priority()
        self.net_throttle = self._get_net_throttle()

        if self.type in PatchingOperationValue.InstallOperations:
            self.install_data_list = self._load_install_data()
            self.restart = self.json_message.get(
                PatchingOperationKey.Restart,
                PatchingOperationValue.IgnoredRestart
            )

        elif self.type == PatchingOperationValue.Uninstall:
            self.uninstall_data_list = self._load_uninstall_data()

        elif self.type == PatchingOperationValue.ThirdPartyInstall:
            self.cli_options = self.json_message[PatchingOperationKey.CliOptions]
            self.package_urn = self.json_message[PatchingOperationKey.Uris]

    def _get_cpu_priority(self):
        if self.json_message:
            return self.json_message.get(
                PatchingOperationKey.CpuThrottle, CpuPriority.Normal
            )

        return CpuPriority.Normal

    def _get_net_throttle(self):
        if self.json_message:
            return self.json_message.get(
                PatchingOperationKey.NetThrottle, 0
            )

        return 0

    def _load_install_data(self):
        """Parses the 'data' key to get the application info for install.

        Returns:

            A list of InstallData types.
        """

        install_data_list = []

        if PatchingOperationKey.FileData in self.json_message:
            data_list = self.json_message[PatchingOperationKey.FileData]
        else:
            data_list = []

        try:

            for data in data_list:

                install_data = InstallData()

                install_data.name = data[PatchingOperationKey.Name]
                install_data.id = data[PatchingOperationKey.AppId]
                install_data.cli_options = \
                    data.get(PatchingOperationKey.CliOptions, '')
                install_data.proc_niceness = \
                    CpuPriority.get_niceness(self._get_cpu_priority())

                if PatchingOperationKey.Uris in data:

                    install_data.uris = data[PatchingOperationKey.Uris]

                install_data_list.append(install_data)

        except Exception as e:

            logger.error("Could not load install data.")
            logger.exception(e)

        return install_data_list

    def _load_uninstall_data(self):
        """Parses the 'data' key to get the application info for uninstall.

        Returns:

            A list of UninstallData types.

        """

        uninstall_data_list = []

        try:

            if PatchingOperationKey.FileData in self.json_message:
                data_list = self.json_message[PatchingOperationKey.FileData]

            else:
                data_list = []

            for data in data_list:
                uninstall_data = UninstallData()

                uninstall_data.name = data[PatchingOperationKey.Name]
                uninstall_data.id = data[PatchingOperationKey.AppId]

                uninstall_data_list.append(uninstall_data)

        except Exception as e:

            logger.error("Could not load uninstall data.")
            logger.exception(e)

        return uninstall_data_list

    def is_savable(self):
        if not super(PatchingSofOperation, self).is_savable():
            return False

        non_savable = [PatchingOperationValue.RefreshApps]

        if self.type in non_savable:
            return False

        return True


# Simple nametuple to contain install results.
InstallResult = namedtuple(
    'InstallResult',
    ['successful', 'error', 'restart',
     'app_json', 'apps_to_delete', 'apps_to_add']
)

UninstallResult = namedtuple(
    'UninstallResult', ['success', 'error', 'restart']
)


class PatchingSofResult():
    """ Data structure for install/uninstall operation results. """

    def __init__(self, operation_id, operation_type, app_id, apps_to_delete,
                 apps_to_add, success, reboot_required, error, data):

        self.id = operation_id  # "uuid"
        self.type = operation_type
        self.success = success  # "true" or "false"
        self.reboot_required = reboot_required  # "true" or "false"
        self.error = error  # "error message"
        self.app_id = app_id  # "36 char uuid or 64 char hash"
        self.apps_to_delete = apps_to_delete
        self.apps_to_add = apps_to_add
        self.data = data  # Application instance in json

        self.raw_result = self.to_json()

    def to_json(self):
        json_dict = {
            "operation_id": self.id,
            "operation": self.type,
            "success": self.success,
            "reboot_required": self.reboot_required,
            "error": self.error,
            "app_id": self.app_id,
            "apps_to_delete": self.apps_to_delete,
            "apps_to_add": self.apps_to_add,
            "data": self.data
        }

        return json.dumps(json_dict)

    def update_raw_result(self):
        self.raw_result = self.to_json()
