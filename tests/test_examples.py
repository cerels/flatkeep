"""Run the examples (doctests) written in the docstrings of flatkeep.core.

    python3 -m unittest            # from the project folder
"""

import doctest
import importlib

MODULES = [
    "flatkeep.core.background",
    "flatkeep.core.bundle",
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
