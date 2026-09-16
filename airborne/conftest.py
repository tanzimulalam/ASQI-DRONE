"""Make the ``airborne_daemon`` package importable when running pytest from here.

(pytest 6.2 predates the ``pythonpath`` ini option, so we insert it explicitly.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
