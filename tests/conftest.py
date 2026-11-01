"""Put the project root on sys.path for the tests.

The tests live here but the code they exercise sits at the repo root,
which is not on sys.path when pytest collects from this directory.
tests/run.py does the same thing for the no-pytest path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
