# -*- coding: utf-8 -*-
#
# Copyright 2016 - 2018  Ternaris.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import absolute_import, division, print_function

import os
from setuptools import setup

HERE = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(HERE, 'README.rst')) as f:
    README = f.read()


with open(os.path.join(HERE, 'requirements.in')) as f:
    INSTALL_REQUIRES = [x for x in
                        [x.strip() for x in f.readlines()]
                        if x
                        if not x.startswith('-r')
                        if not x[0] == '#']
INSTALL_REQUIRES.extend([
    'marv==3.2.0',
])


setup(name='marv-robotics',
      version='3.2.0',
      description='Data management platform for robot logs',
      long_description=README,
      classifiers=[
          'Development Status :: 5 - Production/Stable',
          'License :: OSI Approved :: GNU Affero General Public License v3',
          'Operating System :: POSIX :: Linux',  # for now
          'Programming Language :: Python',
          'Programming Language :: Python :: 2.7',
          'Programming Language :: Python :: 2 :: Only',  # for now
          'Programming Language :: Python :: Implementation :: CPython',  # for now
          'Topic :: Scientific/Engineering',
      ],
      author='Ternaris',
      author_email='team@ternaris.com',
      url='https://ternaris.com/marv-robotics',
      license='AGPL-3.0-only',
      keywords=[],
      packages=['marv_robotics', 'marv_robotics.tests'],
      include_package_data=True,
      zip_safe=False,
      tests_require=[
          'pytest',
          'mock',
          'testfixtures',
      ],
      setup_requires=['pytest-runner'],
      install_requires=INSTALL_REQUIRES)
