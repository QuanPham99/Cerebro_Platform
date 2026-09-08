from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session", autouse=True)
def offline_network_guard():
    """Block IPv4/IPv6 egress for the whole suite when the flag is set.

    `AF_UNIX` stays available so local tooling keeps working. The guard is
    installed once per session and never uninstalled, so no test can quietly
    reach the network later in the run.
    """
    if os.environ.get("CEREBRO_TEST_NO_NETWORK") != "1":
        yield
        return

    from cerebro.hosted_provider import install_offline_network_guard

    restore = install_offline_network_guard()
    try:
        yield
    finally:
        restore()
