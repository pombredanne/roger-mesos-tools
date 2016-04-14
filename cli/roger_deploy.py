#!/usr/bin/python

from __future__ import print_function
from tempfile import mkdtemp
import argparse
from decimal import *
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
import subprocess
import json
import os
import requests
import sys
from roger_build import RogerBuild
from roger_gitpull import RogerGitPull
import re
import shutil
from roger_push import RogerPush
from settings import Settings
from appconfig import AppConfig
from hooks import Hooks
from marathon import Marathon
from chronos import Chronos
from frameworkUtils import FrameworkUtils
from gitutils  import GitUtils

import contextlib

@contextlib.contextmanager
def chdir(dirname):
  '''Withable chdir function that restores directory'''
  curdir = os.getcwd()
  try:
    os.chdir(dirname)
    yield
  finally: os.chdir(curdir)

def describe():
  return 'runs through all of the steps: gitpull -> build & push to registry -> push to roger mesos.'

def getGitSha(work_dir, repo, branch, gitObj):
  return  gitObj.getGitSha(repo, branch, work_dir)

class Slack:
  def __init__(self, config, token_file):
    self.disabled = True
    try:
      from slackclient import SlackClient
    except:
      print("Warning: SlackClient library not found, not using slack\n", file=sys.stderr)
      return

    try:
      self.channel = config['channel']
      self.method = config['method']
      self.username = config['username']
      self.emoji = config['emoji']
    except (TypeError, KeyError) as e:
      print("Warning: slack not setup in config (error: %s). Not using slack.\n" % e, file=sys.stderr)
      return

    try:
      with open(token_file) as stoken:
        r = stoken.readlines()
      slack_token = ''.join(r).strip()
      self.client = SlackClient(slack_token)
    except IOError:
      print("Warning: slack token file %s not found/readable. Not using slack.\n" % token_file, file=sys.stderr)
      return

    self.disabled = False

  def api_call(self, text):
    if not self.disabled:
      self.client.api_call(self.method, channel=self.channel, username=self.username, icon_emoji=self.emoji, text=text)



# Author: cwhitten
# Purpose: Initial plumbing for a standardized deployment
#          process into the ClusterOS
#
# Keys off of a master config file in APP_ROOT/config/
#   with the naming convention APP_NAME.json
# Container-specific Marathon files live in APP_ROOT/templates/ with the
#   naming convention APP_NAME-SERVICE_NAME.json
#
# See README for details and intended use.
#
# Attempts to get a version from an existing image on marathon (formatting rules apply)

# Expected format:
#   <host>:<port>/moz-content-agora-7da406eb9e8937875e0548ae1149/v0.46

class RogerDeploy(object):

    #To remove a temporary directory created by roger-deploy if this script exits
    def removeDirTree(self, work_dir, args, temp_dir_created):
      exists = os.path.exists(os.path.abspath(work_dir))
      if exists and (temp_dir_created == True):
        shutil.rmtree(work_dir)
        print("Deleting temporary dir:{0}".format(work_dir))

    def getNextVersion(self, config, roger_env, application, branch, work_dir, repo, args, gitObj):
      sha = getGitSha(work_dir, repo, branch, gitObj)
      docker_search = subprocess.check_output("docker search {0}/{1}-{2}".format(roger_env['registry'], config['name'], application), shell=True)
      image_version_list = []
      version = ''
      envs = []
      for each_key in roger_env["environments"].keys():
          envs.append(each_key)
      for line in docker_search.split('\n'):
        image = line.split(' ')[0]
        matchObj = re.match("^{0}-{1}-.*/v.*".format(config['name'], application), image)
        if matchObj and matchObj.group().startswith(config['name'] + '-' + application):
          skip_image = False
          for env in envs:
            if matchObj.group().startswith("{0}-{1}-{2}".format(config['name'], application, env)):
              skip_image = True
              break
          if skip_image == False:
            image_version_list.append(matchObj.group().split('/v')[1])

      if len(image_version_list) == 0:	#Create initial version
        version = "{0}/v0.1.0".format(sha)
        print("No version currently exist in the Docker Registry. \nDeploying version:{0}".format(version))
      else:
        version = self.incrementVersion(sha, image_version_list, args)
      return version

    def incrementVersion(self, sha, image_version_list, args):
      latest = max(image_version_list, key=self.splitVersion)
      ver_tuple = self.splitVersion(latest)
      latest_version = ''
      if args.incr_major:
        latest_version = "{0}/v{1}.0.0".format(sha, (int(ver_tuple[0])+1))
        return latest_version
      if args.incr_patch:
        latest_version = "{0}/v{1}.{2}.{3}".format(sha, int(ver_tuple[0]), int(ver_tuple[1]), (int(ver_tuple[2])+1))
        return latest_version

      latest_version = "{0}/v{1}.{2}.0".format(sha, int(ver_tuple[0]), (int(ver_tuple[1])+1))
      return latest_version

    def splitVersion(self, version):
      major, _, rest = version.partition('.')
      minor, _, rest = rest.partition('.')
      patch, _, rest = rest.partition('.')
      return int(major), int(minor) if minor else 0, int(patch) if patch else 0

    def parseArgs(self):
      self.parser = argparse.ArgumentParser(prog='roger deploy', description=describe())
      self.parser.add_argument('-e', '--environment', metavar='env',
        help="environment to deploy to. Example: 'dev' or 'stage'")
      self.parser.add_argument('application', metavar='application',
        help="application to deploy. Example: 'all' or 'kairos'")
      self.parser.add_argument('-b', '--branch', metavar='branch',
        help="branch to pull code from. Defaults to master. Example: 'production' or 'master'")
      self.parser.add_argument('-s', '--skip-build', action="store_true",
        help="whether to skip the build step. Defaults to false.'")
      self.parser.add_argument('config_file', metavar='config_file',
        help="configuration file to be use. Example: 'content.json' or 'kwe.json'")
      self.parser.add_argument('-M', '--incr-major', action="store_true",
        help="increment major in version. Defaults to false.'")
      self.parser.add_argument('-sp', '--skip-push', action="store_true",
        help="skip the push step. Defaults to false.'")
      self.parser.add_argument('-f', '--force-push', action="store_true",
        help="force push. Not recommended. Forces push even if validation checks failed. Applies only if skip_push is false. Defaults to false.")
      self.parser.add_argument('-p', '--incr-patch', action="store_true",
        help="increment patch in version. Defaults to false.'")
      self.parser.add_argument('-S', '--secrets-file',
        help="specifies an optional secrets file for deployment runtime variables.")
      self.parser.add_argument('-d', '--directory',
        help="working directory. Uses a temporary directory if not specified.")
      return self.parser

    def push(self, root, app, work_dir, image_name, config_file, environment, settingObj, appObj, hooksObj, frameworkUtils, args):
      # Prepare push_args to invoke roger_push
      push_args = args
      push_args.image_name = image_name
      push_args.config_file = config_file
      push_args.env = environment
      push_args.app_name = app
      push_args.directory = work_dir
      roger_push = RogerPush()

      try:
        roger_push.main(settingObj, appObj, frameworkUtils, hooksObj, push_args)
      except (Exception, IOError) as e:
        print("The folowing error occurred.(Error: %s).\n" % e, file=sys.stderr)
        self.removeDirTree(work_dir, args, temp_dir_created)
        raise SystemExit('Exiting')
      return 0

    def pullRepo(self, root, app, work_dir, config_file, branch, args, settingObj, appObj, hooksObj, gitObj):

      args.app_name = app
      args.directory = work_dir
      roger_gitpull = RogerGitPull()

      try:
        exit_code = roger_gitpull.main(settingObj, appObj, gitObj, hooksObj, args)
        return exit_code
      except (IOError) as e:
        print("The folowing error occurred.(Error: %s).\n" % e, file=sys.stderr)
        self.removeDirTree(work_dir, args, temp_dir_created)
        raise SystemExit('Exiting')

    def main(self, settingObject, appObject, frameworkUtilsObject, gitObj, hooksObj, args):
      settingObj = settingObject
      appObj = appObject
      config_dir = settingObj.getConfigDir()
      root = settingObj.getCliDir()
      roger_env = appObj.getRogerEnv(config_dir)
      config = appObj.getConfig(config_dir, args.config_file)
      if args.application not in config['apps']:
        raise ValueError('Application specified not found.')

      if 'registry' not in roger_env:
        raise ValueError('Registry not found in roger-env.json file.')

      #Setup for Slack-Client, token, and git user
      slack = Slack(config['notifications'], '/home/vagrant/.roger_cli.conf.d/slack_token')

      if args.application == 'all':
        apps = config['apps'].keys()
      else:
        apps = [args.application]

      common_repo = config.get('repo', '')
      environment = roger_env.get('default', '')

      work_dir = ''
      if args.directory:
        work_dir = args.directory
        temp_dir_created = False
        print("Using {0} as the working directory".format(work_dir))
      else:
        work_dir = mkdtemp()
        temp_dir_created = True
        print("Created a temporary dir: {0}".format(work_dir))

      if args.environment is None:
        if "ROGER_ENV" in os.environ:
          env_var = os.environ.get('ROGER_ENV')
          if env_var.strip() == '':
            print("Environment variable $ROGER_ENV is not set. Using the default set from roger-env.json file")
          else:
            print("Using value {} from environment variable $ROGER_ENV".format(env_var))
            environment = env_var
      else:
        environment = args.environment

      if environment not in roger_env['environments']:
        self.removeDirTree(work_dir, args, temp_dir_created)
        raise ValueError('Environment not found in roger-env.json file.')

      branch = "master"     #master by default
      if not args.branch is None:
        branch = args.branch

      for app in apps:
        self.deployApp(settingObject, appObject, frameworkUtilsObject, gitObj, hooksObj, root, args, config, roger_env, work_dir, config_dir, environment, app, branch, slack, args.config_file, common_repo, temp_dir_created)

    def deployApp(self, settingObject, appObject, frameworkUtilsObject, gitObj, hooksObj, root, args, config, roger_env, work_dir, config_dir, environment, app, branch, slack, config_file, common_repo, temp_dir_created):

      startTime = datetime.now()
      settingObj = settingObject
      appObj = appObject
      frameworkUtils = frameworkUtilsObject
      environmentObj = roger_env['environments'][environment]
      data = appObj.getAppData(config_dir, config_file, app)
      frameworkObj = frameworkUtils.getFramework(data)
      framework = frameworkObj.getName()

      repo = ''
      if common_repo != '':
        repo = data.get('repo', common_repo)
      else:
        repo = data.get('repo', args.application)

      image_name = ''
      image = ''

      # get/update target source(s)
      try:
        exit_code = self.pullRepo(root, app, os.path.abspath(work_dir), config_file, branch, args, settingObj, appObj, hooksObj, gitObj)
        if exit_code != 0:
          self.removeDirTree(work_dir, args, temp_dir_created)
          raise SystemExit('Exiting')
      except (IOError) as e:
        print("The folowing error occurred.(Error: %s).\n" % e, file=sys.stderr)
        self.removeDirTree(work_dir, args, temp_dir_created)
        raise SystemExit('Exiting')

      skip_build = False
      if args.skip_build is not None:
        skip_build = args.skip_build

      skip_push = False
      if args.skip_push is not None:
        skip_push = args.skip_push

      secrets_file = None
      if args.secrets_file is not None:
        secrets_file = args.secrets_file

      #Set initial version
      image_git_sha = getGitSha(work_dir, repo, branch, gitObj)
      image_name = "{0}-{1}-{2}/v0.1.0".format(config['name'], app, image_git_sha)

      if skip_build == True:
        curr_image_ver = frameworkObj.getCurrentImageVersion(roger_env, environment, app)
        print("Current image version deployed on {0} is :{1}".format(framework, curr_image_ver))
        if not curr_image_ver is None:
          image_name = "{0}-{1}-{2}".format(config['name'], app, curr_image_ver)
          print("Image current version from {0} endpoint is:{1}".format(framework, image_name))
        else:
          print("Using base version for image:{0}".format(image_name))
      else:
        #Docker build,tag and push
        image_name = self.getNextVersion(config, roger_env, app, branch, work_dir, repo, args, gitObj)
        image_name = "{0}-{1}-{2}".format(config['name'], app, image_name)
        print("Bumped up image to version:{0}".format(image_name))

        build_args = args
        build_args.app_name = app
        build_args.directory = os.path.abspath(work_dir)
        build_args.tag_name = image_name
        build_args.config_file = config_file
        build_args.push = True
        roger_build = RogerBuild()

        try:
          roger_build.main(settingObj, appObject, hooksObj, build_args)
        except (ValueError, IOError) as e:
          print("The folowing error occurred.(Error: %s).\n" % e, file=sys.stderr)
          self.removeDirTree(work_dir, args, temp_dir_created)
          raise SystemExit('Exiting')
      print("Version is:"+image_name)

      #Deploying the app to framework
      try:
        exit_code = self.push(root, app, os.path.abspath(work_dir), image_name, config_file, environment, settingObj, appObj, hooksObj, frameworkUtils, args)
        if exit_code != 0:
          self.removeDirTree(work_dir, args, temp_dir_created)
          raise SystemExit('Exiting')
      except (IOError) as e:
        print("The folowing error occurred.(Error: %s).\n" % e, file=sys.stderr)
        self.removeDirTree(work_dir, args, temp_dir_created)
        raise SystemExit('Exiting')

      self.removeDirTree(work_dir, args, temp_dir_created)

      deployTime = datetime.now() - startTime

      try:
          git_username = subprocess.check_output("git config user.name", shell=True)
      except subprocess.CalledProcessError as e:
          print("git config user.name not found. Setting default git_username as unknown")
          git_username = "unknown"

      deployMessage = "{0}'s deploy for {1} / {2} / {3} completed in {4} seconds.".format(
        git_username.rstrip(), app, environment, branch, deployTime.total_seconds())
      slack.api_call(deployMessage)
      print(deployMessage)


if __name__ == "__main__":
  settingObj = Settings()
  appObj = AppConfig()
  frameworkUtils = FrameworkUtils()
  gitObj = GitUtils()
  hooksObj = Hooks()
  roger_deploy = RogerDeploy()
  roger_deploy.parser = roger_deploy.parseArgs()
  args = roger_deploy.parser.parse_args()
  roger_deploy.main(settingObj, appObj, frameworkUtils, gitObj, hooksObj, args)
