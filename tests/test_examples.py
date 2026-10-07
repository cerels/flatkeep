"""Run the examples (doctests) written in the docstrings of Flatkeep's
GTK-free modules: flatkeep.core and the command line.

    python3 -m unittest            # from the project folder
"""

import doctest
import importlib

MODULES = [
    "flatkeep.cli",
    "flatkeep.core.background",
    "flatkeep.core.bundle",
    "flatkeep.core.describe",
    "flatkeep.core.desktop_entry",
    "flatkeep.core.github",
    "flatkeep.core.host",
    "flatkeep.core.store",
    "flatkeep.core.updater",
    "flatkeep.core.windows",
]


def load_tests(loader, tests, ignore):
    for name in MODULES:
        tests.addTests(doctest.DocTestSuite(importlib.import_module(name)))
    return tests
