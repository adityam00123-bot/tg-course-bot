"""
Extractors package for Web & LMS Course Downloader.
"""
from .classplus import ClassplusAPI
from .generic_stream import GenericStreamExtractor

__all__ = ["ClassplusAPI", "GenericStreamExtractor"]
