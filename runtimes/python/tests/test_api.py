"""Serveur API (app_hello.py) : unary, param de chemin, body JSON, streaming SSE."""

import os
import sys

import pytest

from conftest import HERE, Server, free_port, request, sse


@pytest.fixture(scope="module")
def hello():
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr)
    srv = Server([sys.executable, os.path.join(HERE, "app_hello.py")], addr, env=env)
    yield addr
    srv.stop()


def test_unary(hello):
    assert request(hello, "/hello") == (200, {"msg": "bonjour depuis vignemale"})


def test_path_param(hello):
    assert request(hello, "/greet/Jacques") == (200, {"hello": "Jacques"})


def test_body_json(hello):
    body = {"x": 1, "k": "v"}
    assert request(hello, "/echo", body) == (200, {"you_sent": body})


def test_streaming_sse(hello):
    assert sse(hello, "/stream") == "ceci est un flux vignemale token par token".split(" ")
