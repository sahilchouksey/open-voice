from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    # CI/runtime images in this project install asyncio backend but not trio.
    # Pinning avoids spurious anyio trio parametrization failures.
    return "asyncio"
