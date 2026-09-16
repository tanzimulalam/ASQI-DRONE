"""Make the ``app`` package importable when running pytest from ``ground/``."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
