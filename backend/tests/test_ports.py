"""Tests for safe port selection."""

import socket

from app.main import find_free_port


def test_returns_preferred_when_free():
    port = find_free_port(18420, "127.0.0.1")
    assert port == 18420


def test_skips_occupied_port():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 18421))
    blocker.listen(1)
    try:
        port = find_free_port(18421, "127.0.0.1")
        assert port == 18422
    finally:
        blocker.close()
