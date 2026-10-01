"""read_web_page: public addresses only, with the checked IP pinned for the request."""

from __future__ import annotations

import socket

import httpx
import pytest

from dotsfam.tools.web import check_url, fetch_page


def fake_dns(monkeypatch, answers: dict[str, str]):
    def getaddrinfo(host, *_args, **_kwargs):
        if host not in answers:
            raise socket.gaierror("unknown")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8787/api/state",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://user:pw@example.com/",
        "file:///etc/passwd",
    ],
)
def test_private_and_odd_urls_are_refused(url, monkeypatch):
    fake_dns(monkeypatch, {"example.com": "93.184.216.34"})
    with pytest.raises(ValueError):
        check_url(url)


def test_names_that_resolve_privately_are_refused(monkeypatch):
    fake_dns(monkeypatch, {"rebind.example": "127.0.0.1"})
    with pytest.raises(ValueError, match="Private"):
        check_url("https://rebind.example/")


async def test_the_request_goes_to_the_ip_that_was_checked(monkeypatch):
    fake_dns(monkeypatch, {"example.com": "93.184.216.34", "evil.example": "10.0.0.7"})
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, html="<title>Hi</title><p>Hello there</p>")

    page = await fetch_page("https://example.com/page", 1000, httpx.MockTransport(handler))
    [request] = seen
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"
    assert page == {
        "url": "https://example.com/page",
        "title": "Hi",
        "text": "Hi\nHello there",
        "truncated": False,
    }
    with pytest.raises(ValueError, match="Private"):
        await fetch_page("https://example.com/old", 1000, httpx.MockTransport(handler))
