#!/usr/bin/env python3
import setuptools
setuptools.setup(
    name="backtab",
    version="1.1",
    description="Backend for tab",
    author="TQ Hirsch <thequux@thequux.com>",
    license="MIT",
    packages=setuptools.find_packages("src"),
    package_dir = {"": "src"},
    entry_points = {
        'console_scripts': [
            "backtab-import-spacebar = backtab.dataconv:main",
            "backtab-server = backtab.server:main",
        ]
    },
    install_requires=[
        "click >= 7.0, <8",
        "bottle >= 0.12, <0.13",
        "PyYAML >= 3.13, <4",
        "sdnotify >= 0.3.1, <0.4",
        "beancount",
    ],
    dependency_links=[
        "hg+https://bitbucket.org/blais/beancount#egg=beancount",
    ],
)
