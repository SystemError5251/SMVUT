"""Instagram: Video kurz in R2/S3 ablegen, signierte URL erzeugen, danach löschen.

Meta lädt Reels nur von einer öffentlich abrufbaren URL. Der Bucket bleibt privat;
eine Lifecycle-Regel im Bucket löscht Reste nach einem Tag (etwa nach einem Absturz).

Statt boto3 (mit ~30 MB AWS-Modellen) signiert dieses Modul die drei nötigen Anfragen
selbst nach AWS Signature Version 4 – geprüft gegen die offiziellen AWS-Testvektoren.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from terminal import config
from terminal.errors import UploadError
from terminal.retry import transient

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED = "UNSIGNED-PAYLOAD"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CHUNK = 1024 * 1024


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _encode(text: str) -> str:
    return quote(text, safe="-_.~")


class SigV4:
    """AWS Signature Version 4 für S3 (auch Cloudflare R2)."""

    def __init__(self, access_key: str, secret_key: str, region: str) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def _scope(self, now: datetime) -> tuple[str, str]:
        date = now.strftime("%Y%m%d")
        return now.strftime("%Y%m%dT%H%M%SZ"), f"{date}/{self.region}/s3/aws4_request"

    def _signature(self, now: datetime, canonical_request: str) -> str:
        amz_date, scope = self._scope(now)
        to_sign = "\n".join([ALGORITHM, amz_date, scope,
                             hashlib.sha256(canonical_request.encode()).hexdigest()])
        key = _hmac(f"AWS4{self.secret_key}".encode(), now.strftime("%Y%m%d"))
        for part in (self.region, "s3", "aws4_request"):
            key = _hmac(key, part)
        return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _canonical(method: str, url: str, query: list[tuple[str, str]],
                   headers: dict[str, str], payload_hash: str) -> tuple[str, str]:
        path = urlsplit(url).path or "/"  # bereits URI-kodiert
        canonical_query = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query))
        names = sorted(headers)
        canonical_headers = "".join(f"{n}:{' '.join(headers[n].split())}\n" for n in names)
        signed = ";".join(names)
        return "\n".join([method, path, canonical_query, canonical_headers, signed,
                          payload_hash]), signed

    def sign_headers(self, method: str, url: str, headers: dict[str, str],
                     payload_hash: str, now: datetime | None = None) -> dict[str, str]:
        """Liefert die Header inkl. Authorization; `headers` mit kleingeschriebenen Namen."""
        now = now or datetime.now(UTC)
        amz_date, scope = self._scope(now)
        all_headers = {**{k.lower(): v for k, v in headers.items()},
                       "host": urlsplit(url).netloc, "x-amz-date": amz_date,
                       "x-amz-content-sha256": payload_hash}
        canonical, signed = self._canonical(method, url, [], all_headers, payload_hash)
        signature = self._signature(now, canonical)
        all_headers["authorization"] = (f"{ALGORITHM} Credential={self.access_key}/{scope}, "
                                        f"SignedHeaders={signed}, Signature={signature}")
        return all_headers

    def presign(self, method: str, url: str, expires: int,
                now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        amz_date, scope = self._scope(now)
        query = [("X-Amz-Algorithm", ALGORITHM),
                 ("X-Amz-Credential", f"{self.access_key}/{scope}"),
                 ("X-Amz-Date", amz_date), ("X-Amz-Expires", str(expires)),
                 ("X-Amz-SignedHeaders", "host")]
        canonical, _ = self._canonical(method, url, query, {"host": urlsplit(url).netloc},
                                       UNSIGNED)
        canonical_query = canonical.split("\n")[2]
        return f"{url}?{canonical_query}&X-Amz-Signature={self._signature(now, canonical)}"


def _s3_error(response: httpx.Response) -> str:
    code = re.search(r"<Code>(.*?)</Code>", response.text)
    message = re.search(r"<Message>(.*?)</Message>", response.text)
    detail = " – ".join(m.group(1) for m in (code, message) if m)
    return f"HTTP {response.status_code}" + (f": {detail}" if detail else "")


class Hosting:
    def __init__(self, client: httpx.Client | None = None, bucket: str | None = None,
                 endpoint: str | None = None, region: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None) -> None:
        self.bucket = bucket or config.require_env("S3_BUCKET")
        self.endpoint = (endpoint if endpoint is not None
                         else config.env("S3_ENDPOINT_URL") or "").rstrip("/")
        region = region or config.env("S3_REGION") or ("auto" if self.endpoint
                                                        else "us-east-1")
        self.signer = SigV4(access_key or config.require_env("S3_ACCESS_KEY_ID"),
                            secret_key or config.require_env("S3_SECRET_ACCESS_KEY"), region)
        self.client = client or httpx.Client(timeout=httpx.Timeout(60, write=300))

    def object_url(self, key: str) -> str:
        path = quote(key, safe="-_.~/")
        if self.endpoint:  # R2 & Co.: Pfad-Stil
            return f"{self.endpoint}/{_encode(self.bucket)}/{path}"
        region = self.signer.region
        host = ("s3.amazonaws.com" if region == "us-east-1"
                else f"s3.{region}.amazonaws.com")
        return f"https://{self.bucket}.{host}/{path}"

    def put(self, video: Path, progress: Callable[[int], None] | None = None) -> str:
        key = f"{config.APP_NAME}/{uuid.uuid4()}.mp4"
        self._put_file(key, video, progress)
        return key

    @transient
    def _put_file(self, key: str, path: Path, progress: Callable[[int], None] | None) -> None:
        size = path.stat().st_size

        def body() -> Iterator[bytes]:
            with path.open("rb") as f:
                while chunk := f.read(CHUNK):
                    if progress:
                        progress(len(chunk))
                    yield chunk

        url = self.object_url(key)
        headers = self.signer.sign_headers("PUT", url, {"content-type": "video/mp4"},
                                           UNSIGNED)
        headers["content-length"] = str(size)
        r = self.client.put(url, content=body(), headers=headers)
        if r.status_code >= 500 or r.status_code == 429:
            r.raise_for_status()  # vorübergehend: transient wiederholt
        if r.status_code != 200:
            raise UploadError(f"Upload in den Bucket fehlgeschlagen: {_s3_error(r)}")

    def url(self, key: str) -> str:
        return self.signer.presign("GET", self.object_url(key), config.PRESIGNED_URL_TTL)

    @transient
    def delete(self, key: str) -> None:
        url = self.object_url(key)
        r = self.client.delete(url, headers=self.signer.sign_headers("DELETE", url, {},
                                                                     EMPTY_SHA256))
        if r.status_code >= 500 or r.status_code == 429:
            r.raise_for_status()
        if r.status_code not in (200, 204):
            raise UploadError(f"Löschen im Bucket fehlgeschlagen: {_s3_error(r)}")

    @staticmethod
    def assert_reachable(url: str) -> None:
        """Meta lädt selbst herunter: Die URL muss ohne Login und ohne Weiterleitung gehen."""
        try:
            r = httpx.get(url, headers={"Range": "bytes=0-0"}, follow_redirects=False,
                          timeout=30)
        except httpx.TransportError as exc:
            raise UploadError(f"Bucket-URL nicht erreichbar: {type(exc).__name__}") from exc
        if r.status_code not in (200, 206):
            raise UploadError(f"Bucket-URL liefert HTTP {r.status_code} – Meta könnte das "
                              "Video nicht abholen (Bucket-Rechte und Endpoint prüfen)")
