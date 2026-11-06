"""Desk Sentry v2: local webcam rear-view mirror."""

import os

# Must be set before the first ``import cv2`` anywhere in the package.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

__version__ = "2.0.1"
