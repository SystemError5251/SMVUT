"""Instagram: Video kurz in R2/S3 ablegen, signierte URL erzeugen, danach löschen.

Meta lädt Reels nur von einer öffentlich abrufbaren URL. Der Bucket bleibt privat;
eine Lifecycle-Regel im Bucket löscht Reste nach einem Tag (etwa nach einem Absturz).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from terminal import config
from terminal.errors import UploadError


class Hosting:
    def __init__(self, client: Any = None, bucket: str | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=config.env("S3_ENDPOINT_URL"),
                region_name=config.env("S3_REGION", "auto"),
                aws_access_key_id=config.require_env("S3_ACCESS_KEY_ID"),
                aws_secret_access_key=config.require_env("S3_SECRET_ACCESS_KEY"),
            )
        self.s3 = client
        self.bucket = bucket or config.require_env("S3_BUCKET")

    def put(self, video: Path, progress: Callable[[int], None] | None = None) -> str:
        key = f"{config.APP_NAME}/{uuid.uuid4()}.mp4"
        self.s3.upload_file(str(video), self.bucket, key,
                            ExtraArgs={"ContentType": "video/mp4"}, Callback=progress)
        return key

    def url(self, key: str) -> str:
        url: str = self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=config.PRESIGNED_URL_TTL)
        return url

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

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
