"""The browser version answers only its own page (F-11)."""
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from tomoreveal.webapp import make_handler


class FakeStudio:
    """Only what the request handler touches; `calls` records what ran."""

    def __init__(self, work):
        self.work, self.outdir, self.job, self.thumb, self.calls = work, work, {}, b"", []

    def info(self):
        return {}

    def progress(self):
        return {}

    def reset(self):
        self.calls.append("reset"); return {}

    def call(self, fn, *a):
        return fn(*a)


@pytest.fixture
def server(tmp_path):
    st = FakeStudio(str(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield st, httpd.server_address[1]
    httpd.shutdown(); httpd.server_close()


def ask(port, raw, body=b"", half_close=False):
    """Send raw bytes on one connection; return everything the server sends until it closes (or 3 s pass)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(raw.encode() + body)
    if half_close:
        s.shutdown(socket.SHUT_WR)
    out = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    except socket.timeout:
        pass
    s.close()
    return out


def status(resp):
    return [int(line.split()[1]) for line in resp.split(b"\r\n") if line.startswith(b"HTTP/1.")]


def post(port, path, origin=None, host=None, body=b"", length=None):
    head = f"POST {path} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\nContent-Length: {len(body) if length is None else length}\r\n"
    if origin:
        head += f"Origin: {origin}\r\n"
    return head + "Connection: close\r\n\r\n", body


def test_own_page_is_answered(server):
    st, port = server
    assert status(ask(port, f"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n")) == [200]
    assert status(ask(port, f"GET /api/state HTTP/1.1\r\nHost: localhost:{port}\r\nConnection: close\r\n\r\n")) == [200]
    assert status(ask(port, *post(port, "/api/reset", origin=f"http://127.0.0.1:{port}"))) == [200] and st.calls == ["reset"]


def test_other_pages_and_wrong_host_are_refused(server):
    st, port = server
    assert status(ask(port, f"GET /api/state HTTP/1.1\r\nHost: evil.example:{port}\r\nConnection: close\r\n\r\n")) == [403]
    assert status(ask(port, *post(port, "/api/reset", origin="http://evil.example"))) == [403]
    assert status(ask(port, *post(port, "/api/reset", host=f"evil.example:{port}"))) == [403]
    assert st.calls == []


def test_upload_from_another_page_is_refused(server, tmp_path):
    st, port = server
    assert status(ask(port, *post(port, "/api/upload?name=x.mrc", origin="http://evil.example", body=b"12345"))) == [403]
    assert not os.path.exists(tmp_path / "x.mrc")
    assert status(ask(port, *post(port, "/api/upload?name=y.mrc", origin=f"http://127.0.0.1:{port}", body=b"12345"))) == [200]
    assert (tmp_path / "y.mrc").read_bytes() == b"12345"


def test_request_hidden_in_a_refused_body_never_runs(server):
    st, port = server
    inner = f"POST /api/reset HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 0\r\n\r\n".encode()
    head = (f"POST /api/reset HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://evil.example\r\n"
            f"Content-Length: {len(inner)}\r\n\r\n")
    resp = ask(port, head, inner)
    assert status(resp) == [403] and b"Connection: close" in resp
    time.sleep(0.2)
    assert st.calls == []


def test_cut_off_upload_leaves_no_file(server, tmp_path):
    st, port = server
    raw, _ = post(port, "/api/upload?name=part.mrc", origin=f"http://127.0.0.1:{port}", length=1000)
    resp = ask(port, raw, b"only ten b", half_close=True)
    assert status(resp) == [400]
    assert not os.path.exists(tmp_path / "part.mrc")


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows lets two servers share a busy port")
def test_second_app_moves_to_the_next_port():
    """F-30: a second `tomoreveal app` does not bind the port the first one listens on."""
    from tomoreveal.webapp import _bind
    handler = make_handler(FakeStudio("."))
    first, p1 = _bind(handler, 18765)
    try:
        second, p2 = _bind(handler, p1)
        second.server_close()
        assert p2 != p1
    finally:
        first.server_close()

