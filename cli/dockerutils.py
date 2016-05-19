#!/usr/bin/python

from __future__ import print_function
import os
import subprocess
import sys
import contextlib


@contextlib.contextmanager
def chdir(dirname):
    '''Withable chdir function that restores directory'''
    curdir = os.getcwd()
    try:
        os.chdir(dirname)
        yield
    finally:
        os.chdir(curdir)


class DockerUtils:

    def docker_build(self, image_tag, docker_file):
        if docker_file is not 'Dockerfile':
            exit_code = os.system(
                'docker build -f {} -t {} .'.format(docker_file, image_tag))
        else:
            exit_code = os.system('docker build -t {} .'.format(image_tag))
        if exit_code is not 0:
            raise ValueError("docker build failed")

    def docker_push(self, image):
        exit_code = os.system("docker push {0}".format(image))
        return exit_code