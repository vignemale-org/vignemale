"""Vignemale CLI — the developer tooling, separate from the runtime.

Components: `cli` (run/check/gen/gdpr commands), `collect` (the static
parser: Python code → meta.proto), `devinfra` (local docker
provisioning), `gen` (typed clients). The runtime (`vignemale`) stays the only
package needed in production.
"""
