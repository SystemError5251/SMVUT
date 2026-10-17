"""S3-Signatur gegen die offiziellen Beispiele aus der AWS-Dokumentation
(„Signature Calculations for the Authorization Header“ und „Query String Auth“)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from terminal.errors import UploadError
from terminal.hosting import EMPTY_SHA256, Hosting, SigV4

AWS_NOW = datetime(2013, 5, 24, tzinfo=UTC)
AWS = SigV4("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1")


def test_aws_example_get_object() -> None:
    headers = AWS.sign_headers("GET", "https://examplebucket.s3.amazonaws.com/test.txt",
                               {"Range": "bytes=0-9"}, EMPTY_SHA256, AWS_NOW)
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


def test_aws_example_put_object() -> None:
    headers = AWS.sign_headers(
        "PUT", "https://examplebucket.s3.amazonaws.com/test%24file.text",
        {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
        "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072", AWS_NOW)
    assert headers["authorization"].endswith(
        "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, "
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")


def test_aws_example_presigned_url() -> None:
    url = AWS.presign("GET", "https://examplebucket.s3.amazonaws.com/test.txt", 86400,
                      AWS_NOW)
    assert url == (
        "https://examplebucket.s3.amazonaws.com/test.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host"
        "&X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


def hosting(**kwargs: str) -> Hosting:
    return Hosting(client=httpx.Client(), bucket="videos", access_key="AK", secret_key="SK",
                   **kwargs)


def test_urls_r2_and_aws() -> None:
    r2 = hosting(endpoint="https://acc.r2.cloudflarestorage.com/")
    assert r2.object_url("terminal/a b.mp4") == \
        "https://acc.r2.cloudflarestorage.com/videos/terminal/a%20b.mp4"
    assert r2.signer.region == "auto"
    assert hosting(endpoint="").object_url("k.mp4") == "https://videos.s3.amazonaws.com/k.mp4"
    assert hosting(endpoint="", region="eu-central-1").object_url("k.mp4") == \
        "https://videos.s3.eu-central-1.amazonaws.com/k.mp4"


@respx.mock
def test_put_and_delete(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * (2 * 1024 * 1024 + 5))
    put = respx.put(url__regex=r"https://acc\.r2\.cloudflarestorage\.com/videos/terminal/.+\.mp4"
                    ).mock(return_value=httpx.Response(200))
    delete = respx.delete(url__regex=r".*/videos/terminal/.+").mock(
        return_value=httpx.Response(204))
    h = hosting(endpoint="https://acc.r2.cloudflarestorage.com")
    sent: list[int] = []
    key = h.put(video, sent.append)
    request = put.calls[0].request
    assert request.content == video.read_bytes()
    assert request.headers["content-length"] == str(video.stat().st_size)
    assert request.headers["x-amz-content-sha256"] == "UNSIGNED-PAYLOAD"
    assert "/auto/s3/aws4_request" in request.headers["authorization"]
    assert sum(sent) == video.stat().st_size
    url = h.url(key)
    assert url.startswith(h.object_url(key) + "?X-Amz-Algorithm=AWS4-HMAC-SHA256")
    assert "X-Amz-Expires=3600" in url
    h.delete(key)
    assert delete.calls[0].request.headers["x-amz-content-sha256"] == EMPTY_SHA256


@respx.mock
def test_access_denied_message(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    respx.put(url__regex=r".*").mock(return_value=httpx.Response(
        403, text="<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"))
    with pytest.raises(UploadError, match="AccessDenied – Access Denied"):
        hosting(endpoint="https://acc.r2.cloudflarestorage.com").put(video)
