"""Shared fixtures."""

import os
from collections.abc import Iterator

import pytest

from waterlooworks_flash_next.runtime_secrets import clear_local_environment


@pytest.fixture(autouse=True)
def private_umask() -> Iterator[None]:
    """Mirror the production entrypoint's umask 077 for files tests create.

    Key files, env files and databases are rejected when group/other accessible, so
    fixtures that write them with a permissive default umask would be invalid.
    """
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture(autouse=True)
def isolated_local_environment() -> Iterator[None]:
    """Shared isolation for process-private runtime configuration lookups."""
    clear_local_environment()
    yield
    clear_local_environment()
