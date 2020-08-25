#!/usr/bin/env python3

import os
from setuptools import setup


def read(filename):
    return open(os.path.join(os.path.dirname(__file__), filename)).read()


setup(
    name='okerrsensor',
    version='0.0.35',
    packages=['remotecheck'],
    scripts=['sensor.py'],

    # install_requires=[],

    url='https://gitlab.com/yaroslaff/sensor',
    license='GPL',
    author='Yaroslav Polyakov',
    long_description=read('README.md'),
    long_description_content_type='text/markdown',
    author_email='yaroslaff@gmail.com',
    description='network sensor for okerr monitoring server',
    install_requires=[
        'okerrupdate',
        'setproctitle',
        'pika',
        'pyOpenSSL',
        'python-whois',
        'dnspython',
        'ping3',
        'python-dotenv',
        'adns @ git+https://github.com/trolldbois/python3-adns/'
    ],
    data_files=[
        ('okerrsensor', ['contrib/okerr-sensor.service']),
        ('okerrsensor', ['contrib/okerr-sensor-venv.service']),
    ],

    python_requires='>=3',
    classifiers=[
        'Development Status :: 3 - Alpha',

        # Indicate who your project is intended for
        'Intended Audience :: Developers',

        # Pick your license as you wish (should match "license" above)
        'License :: OSI Approved :: GNU General Public License (GPL)',

        # Specify the Python versions you support here. In particular, ensure
        # that you indicate whether you support Python 2, Python 3 or both.
        # 'Programming Language :: Python :: 3.4',
    ]
)
