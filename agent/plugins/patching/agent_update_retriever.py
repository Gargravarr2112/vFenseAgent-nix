import json
import urllib2

from datetime import datetime
from src.utils import settings, logger
from plugins.patching.data.application import AppUtils

class AgentUpdateRetriever():

    @staticmethod
    def _get_available_agent_file_data(github_release_assets, platform):
        """Retrieves the file_uri information for the agent update. It gets
        the information from the assets of the release.

        Args:
            github_release_assets (list): The list of assets for the release.

        Returns:
            (list): A list of dictionaries where each dictionary is data
                about a necessary file to download this update.

                Ex:
                [
                    {
                        'file_name': 'VFAgent_0_7_0-deb.tar.gz',
                        'file_uri': 'https://api.github.com/repos/toppatch/vFenseAgent-nix/releases/assets/91651',
                        'file_hash': '', # Currently no way of getting the hash,
                        'file_size': 544460
                    },
                    ...
                ]
        """

        for asset in github_release_assets:
            name = asset.get('name')

            if not name:
                continue

            if platform not in name:
                continue

            data_dict = {
                'file_name': name,
                'file_uri': asset['url'],
                'file_hash': '',
                'file_size': asset['size']
            }

            return [data_dict]

        return []

    @staticmethod
    def get_available_agent_update(platform, version_string):
        agent_update = None

        # TODO: don't hardcode
        releases_api = \
            'https://api.github.com/repos/toppatch/vFenseAgent-nix/releases'

        try:
            response = urllib2.urlopen(releases_api)
            releases = json.loads(response.read())

            #version_string = settings.AgentVersion.split('-')[0]
            # Gets replaced in for loop if newer version is found
            current_version = [int(x) for x in version_string.split('.')]

            for release in releases:

                try:
                    # [1:] to avoid the v in tag name. (Ex: v0.7.0)
                    release_version = [int(x) for x in
                                       release['tag_name'][1:].split('.')]

                except Exception as e:
                    logger.error(
                        "Failed to convert tag_name to an integer list."
                    )
                    logger.exception(e)

                    continue

                release_date = datetime.strptime(
                    release.get('published_at'), "%Y-%d-%mT%XZ"
                ).strftime(settings.DATE_FORMAT)

                update_file_data = \
                    AgentUpdateRetriever._get_available_agent_file_data(
                        release.get('assets', []), platform
                    )

                if not update_file_data:
                    logger.debug(
                        "Could not generate file_data from {0}"
                        .format(release.get('assets'))
                    )

                    continue

                file_size = reduce(
                    lambda a, b: a+b,
                    [file_data['file_size'] for file_data in update_file_data]
                )

                if current_version < release_version:
                    # To keep looping and getting the highest version
                    current_version = release_version

                    agent_version = (
                        '.'.join([str(x) for x in release_version])
                        + '-'
                        + platform
                    )

                    agent_update = AppUtils.create_app(
                        settings.AgentName,
                        agent_version,
                        settings.AgentDescription,  # description
                        update_file_data,  # file_data
                        [],  # dependencies
                        '',  # support_url
                        'recommended',  # vendor_severity
                        file_size,  # file_size
                        '',  # vendor_id,
                        'vFense',  # vendor_name
                        '',  # install_date
                        release_date,  # release_date
                        False,  # installed
                        '',  # repo
                        'no',  # reboot_required
                        'no'  # uninstallable
                    )

        except Exception as e:
            logger.error("Could not check for available agent update.")
            print "error: %s" % e
            logger.exception(e)

        return agent_update

