# -*- encoding: utf-8 -*-

"""
Provides a class and script. See bin/roger.py for a reference of how the script
is called.

:classes:

RogerPromote: Provides application promotion between environments for the
supported Mesos frameworks.

:exceptions:

RogerPromoteError: Generic exception for errors in RogerPromote
"""

# ~
# stdlib
import argparse
import tempfile
import os
import os.path
import shutil
import sys

# ~
# core
from cli.settings import Settings
from cli.appconfig import AppConfig
from cli.framework_utils import FrameworkUtils
from cli.marathon import Marathon
from cli.chronos import Chronos


class RogerPromoteError(Exception):
    pass


class RogerPromote(object):
    """
    Enable application promotion between environments

    :params:
    :app_config [cli.appconfig.AppConfig]:
    :settings [cli.settings.Settings]:
    :framework_utils [cli.framework_utils.FrameworkUtils]:
    """

    def __init__(
        self,
        app_config=AppConfig(),
        settings=Settings(),
        framework_utils=FrameworkUtils(),
        framework=Marathon()
    ):
        self._app_config = app_config
        self._settings = settings
        self._framework_utils = framework_utils
        self._framework = framework
        self._config_dir = None
        self._roger_env = None
        self._temp_dir = None

    @classmethod
    def promote(cls, instance=None):
        """
        :params:
        :instance [roger_promote.RogerPromote]: Avalailable for test
        """
        # Get instance
        if instance:
            rp = instance
        else:
            rp = cls()

        # Get Namespace obj
        args = rp.arg_parse.parse_args()

        # Set framework based on app config
        self._set_framework(args.config, args.app_name)

        # Get deployed version in source environment (should be the image name)
        image = rp._image_name(args.from_env, args.app_name)

        # Get repo name
        repo = rp._config_resolver('repo', args.app_name, args.config)
        if not repo:
            raise RogerPromoteError('Repo not found')

        # Clone the repo
        rp.clone_repo(repo)

        # Locate roger_push.py
        roger_push = rp._roger_push_script()

        # Execute the script
        cmd = [
            roger_push,
            '--env', args.to_env,
            args.app_name, rp._temp_dir, image, args.config
        ]
        ret_val = os.system(cmd)

        # CleanUp
        shutil.rmtree(self._temp_dir)

        if ret_val != 0:
            print("Roger failed to push {} to {}".format(
                args.app_name, args.to_env
            ))
            return False
        return True

    def arg_parse(self):
        """
        Returns a argparse.NameSpace instance holding argument data
        """
        parser = argparse.ArgumentParser(
            prog='roger promote',
            description='Enables application promotion between environments'
        )

        env_choices = ['dev', 'stage', 'prod']

        parser.add_argument(
            'from_env', choices=env_choices, help='The source environment'
        )
        parser.add_argument(
            'to_env', choices=env_choices, help='The destination environment'
        )
        parser.add_argument('config', help='The team config file')
        parser.add_argument('app_name', help='The name of the application')

        return parser

    @property
    def config_dir(self):
        """
        Property that returns the config dir, typically /vagrant/config
        """
        if not self._config_dir:
            self._config_dir = self._settings.getConfigDir()
        return self._config_dir

    @property
    def roger_env(self):
        """
        Property that returns the dict loaded from
        /vagrant/config/roger-mesos-tools.config
        """
        if not self._roger_env:
            self._rover_env = self._app_config.getRogerEnv(self.config_dir)
        return self._roger_env

    def _set_framework(self, config_file, app_name):
        """
        Set the _framework instance variable based on the application config
        """
        app_data = self.app_config.getAppData(
            self.config_dir, config_file, app_name
        )
        self._framework = self.framework_utils.getFramework(app_data)

    def _image_name(self, environment, application):
        """
        Returns the image name as a str

        :params:
        :roger_env [dict]: Data loaded from roger-mesos-tools.config
        :environment [str]: Environment as found in roger-mesos-tools.config
        :application [str]: application as defined in the appropriate yml or
                            json file under config/
        """
        return self._framework.getCurrentImageVersion(
            self.roger_env, environment, application
        )

    def _config_resolver(self, key, application, config_file):
        """
        Returns the filepath to the repo directory directly below the app repo
        checkout (e.g. /home/vagrant/[repo_name] - not including repo-name)

        :params:
        :key [str]: The key containing the desired value we wish to return
        :application [str]: The application being promoted
        :config_file [str] path to the yml or json file, typically found under
                           /vagrant/config/
        """
        config_data = self._app_config.getConfig(self.config_dir, config_file)
        found = None
        if key in config_data:
            found = config_data[key]
        for app in config_data['apps']:
            if app['name'] == application:
                if key in app:
                    found = app[key]
                    break
        return found

    def _clone_repo(self, repo):
        """
        Clone the repo
        """
        repo_url = self._app_config.getRepoUrl(repo)
        self.temp_dir = tempfile.mkdtemp()
        subprocess.check_call(['git', 'clone', repo_url], cwd=self.temp_dir)

    def _roger_push_script(self):
        code_dir = os.path.abspath(os.path.dirname(__file__))
        return os.path.join(code_dir, 'roger_push.py')


if __name__ == '__main__':
    if not RogerPromote.promote():
        sys.exit(1)