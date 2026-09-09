"""Approved API/feed adapters. No HTML listing scraping is performed."""

from app.adapters.base import JobSourceAdapter, SourceError
from app.adapters.greenhouse import GreenhouseAdapter
from app.adapters.himalayas import HimalayasAdapter
from app.adapters.lever import LeverAdapter
from app.adapters.remoteok import RemoteOKAdapter
from app.adapters.we_work_remotely import WeWorkRemotelyAdapter

__all__ = ["GreenhouseAdapter", "JobSourceAdapter", "LeverAdapter", "RemoteOKAdapter", "SourceError", "WeWorkRemotelyAdapter"]
