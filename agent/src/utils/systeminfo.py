import os
import platform
import subprocess
import socket

from src.utils.distro import mac
from src.utils import hardware, logger


supported_linux_distros = (
    'SuSE', 'debian', 'fedora', 'oracle', 'redhat', 'centos',
    'mandrake', 'mandriva')


class OSCode():

    Mac = "darwin"
    Windows = "windows"
    Linux = "linux"


def get_os_code():
    """Gets the os code defined for this system.
    @return: The os code.
    """
    return platform.system().lower()


def get_os_string():
    """The "pretty" string of the OS.
    @return: The os string.
    """
    os_code = get_os_code()
    os_string = 'Unknown'

    if os_code == OSCode.Mac:
        mac_ver = platform.mac_ver()[0]
        os_string = "OS X %s" % mac_ver

    elif os_code == OSCode.Linux:
        distro_info = platform.linux_distribution(
            supported_dists=supported_linux_distros)

        os_string = '%s %s' % (distro_info[0], distro_info[1])

    return os_string


def get_version():
    """This returns the kernel of the respective platform.
    @return: The version.
    """
    return platform.release()


def get_bit_type():
    """The archtecture of the platform. '32' or '64'.
    @return: The bit type.
    """
    return platform.architecture()[0][:2]


def get_system_architecture():
    """
    Returns a nice string for the system architecture. 'x86_64' or 'i386'
    """
    sys_arch = None

    sys_bit_type = get_bit_type()

    if sys_bit_type == '64':
        sys_arch = 'x86_64'

    elif sys_bit_type == '32':
        sys_arch = 'i386'

    return sys_arch


def get_computer_name():
    """The bare machine name.
    @return: The computer name.
    """

    hostname = platform.node()
    if hostname.find('.'): #Fully-qualified, happens on Macs, so split out the computer name
        hostname = hostname.split('.')[0]
    return platform.node()

def get_host_name():
    """
    @return: The FQDN of the machine.
    """

    return socket.getfqdn()


def get_hardware_info():
    """Returns the hardware for the system.
    @return: The hardware.
    """
    return hardware.get_hw_info()


def uptime():
    """Gets the current uptime in a platform independent way.

    Returns:
        (long) The uptime in seconds.
    """

    plat = get_os_code()
    up = 0

    try:

        if plat == OSCode.Mac:

            up = mac.get_current_uptime()

        elif plat == OSCode.Linux:

            cmd = ['cat', '/proc/uptime']
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            raw_output, _stderr = process.communicate()

            secs = raw_output.split()[0]

            # Truncate the decimals for Linux.
            up = long(float(secs))

    except Exception as e:

        logger.error("Could not determine uptime.")
        logger.exception(e)

    return up


class MachineType():
    def __init__(self):
        self.known_virtual_machines = [
            'Parallels Virtual Platform',
            'VMware Virtual Platform',
            'VirtualBox',
            'KVM',
            'Bochs',
            'QEMU',
            'innotek GmbH'
        ]
        self.dmidecode_path = self._get_dmidecode_path()

    def _get_dmidecode_path(self):
        known_paths = [
            '/usr/sbin/dmidecode',
        ]

        for path in known_paths:
            if os.path.exists(path):
                return path

        return None

    def get_machine_type(self):
        if self.dmidecode_path:
            cmd = [self.dmidecode_path, '-s', 'system-manufacturer']
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            output, err = proc.communicate()

            logger.debug("System product name: {0}".format(output))

            if not err:
                known_virtual_machines = \
                    [x.lower() for x in self.known_virtual_machines]

                if output.lower().strip() in known_virtual_machines:
                    return 'virtual'

        return 'physical'
