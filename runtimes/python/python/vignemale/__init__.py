"""Vignemale — Python SDK (binding of the Rust core).

Deploy your AI agents to production on Scaleway, from Python.
For now: exposes the core's config loading (testable as we go).
"""

from vignemale._core import (
    version,
    encode_demo_config,
    parse_runtime_config_b64,
    load_config_from_env,
    resolve_env_secret,
    resolve_b64_secret,
    resolve_json_key_secret,
    s3_roundtrip,
)
from vignemale import log
from vignemale.api import (
    api, auth_handler, serve, serve_gateway, static_files, APIError, HTTPError,
)
from vignemale.bucket import Bucket, BucketError
from vignemale.call import call
from vignemale.service import Service
from vignemale.secret import Secret
from vignemale.sqldb import SQLDatabase, SQLError

__all__ = [
    "version",
    "encode_demo_config",
    "parse_runtime_config_b64",
    "load_config_from_env",
    "resolve_env_secret",
    "resolve_b64_secret",
    "resolve_json_key_secret",
    "s3_roundtrip",
    "api",
    "auth_handler",
    "call",
    "serve",
    "serve_gateway",
    "static_files",
    "APIError",
    "HTTPError",
    "Service",
    "SQLDatabase",
    "SQLError",
    "Bucket",
    "BucketError",
    "Secret",
    "log",
]
