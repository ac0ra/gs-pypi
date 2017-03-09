#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    setup.py
    ~~~~~~~~

    installation script

    :copyright: (c) 2013-2015 by Jauhien Piatlicki
    :license: GPL-2, see LICENSE for more details.
"""

from distutils.core import setup
import sys
import os

setup(name          = 'gs-pypi',
      version       = '0.2.1',
      description   = 'g-sorcery backend for pypi packages',
      author        = 'Jauhien Piatlicki',
      author_email  = 'jauhien@gentoo.org',
      packages      = ['gs_pypi'],
      package_data  = {'gs_pypi': ['data/*']},
      data_files    = [(os.path.join(sys.prefix, '..', 'etc', 'g-sorcery'), 
                                     ['gs-pypi.json']),
                       (os.path.join(sys.prefix, '..', 'etc', 'layman', 
                                     'overlays'), ['gs-pypi-overlays.xml']),
                       (os.path.join(sys.prefix, 'bin'), 
                           [os.path.join('bin', 'gs-pypi-generate-db'), 
                                         os.path.join('bin', 'gs-pypi')]) 
                      ],
      license       = 'GPL-2',
      )
