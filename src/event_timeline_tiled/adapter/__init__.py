"""HTTP adapter between Grafana (Infinity data source) and Tiled."""

from .app import create_app

__all__ = ["create_app"]
