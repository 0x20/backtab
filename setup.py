#!/usr/bin/env python3
import setuptools
setuptools.setup(
    name="backtab",
    version="1.0",
    description="Backend for tab",
    author="TQ Hirsch <thequux@thequux.com>",
    license="MIT",
    packages=setuptools.find_packages("src"),
    package_dir = {"": "src"},
    entry_points = {
        'console_scripts': [
            "backtab-import-spacebar = backtab.dataconv:main"
        ]
    },
    install_requires=[
        "bottle ~= 0.12",
        "PyYAML ~= 3.13",
    ],
    dependency_links=[
        "hg+https://bitbucket.org/blais/beancount",
    ],
)