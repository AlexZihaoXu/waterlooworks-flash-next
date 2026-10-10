"""Shared isolation for process-private runtime configuration lookups."""

from collections.abc import Iterator

import pytest

from waterlooworks_flash_next.runtime_secrets import clear_local_environment


@pytest.fixture(autouse=True)
def isolated_local_environment() -> Iterator[None]:
    clear_local_environment()
    yield
    clear_local_environment()
