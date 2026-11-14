"""Object storage (doc 01 §8; layout doc 11 §6). Private buckets with SSE; presigned GETs last ≤ 5 min.

Files move to and from storage by path, so large uploads are streamed and never fully buffered in memory (doc 02 §5).
Presigned URLs are signed for S3_PUBLIC_ENDPOINT (what the browser can reach) when it is set."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

import aioboto3

from app.core.config import Settings


class ObjectStore(Protocol):
    async def put(self, bucket: str, key: str, data: bytes | AsyncIterator[bytes], content_type: str) -> None: ...
    async def put_file(self, bucket: str, key: str, path: str, content_type: str) -> None: ...
    async def get_file(self, bucket: str, key: str, path: str) -> None: ...
    async def exists(self, bucket: str, key: str) -> bool: ...
    async def delete(self, bucket: str, key: str) -> None: ...
    async def get_url(self, bucket: str, key: str, ttl_s: int = 300) -> str: ...
    async def delete_prefix(self, bucket: str, prefix: str) -> int: ...
    async def check(self) -> bool: ...


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._session = aioboto3.Session()

    def _client(self, endpoint: str | None = None):  # type: ignore[no-untyped-def]
        return self._session.client(
            "s3", endpoint_url=endpoint or self._s.S3_ENDPOINT, region_name=self._s.S3_REGION,
            aws_access_key_id=self._s.S3_ACCESS_KEY.get_secret_value(),
            aws_secret_access_key=self._s.S3_SECRET_KEY.get_secret_value())

    async def put(self, bucket: str, key: str, data: bytes | AsyncIterator[bytes], content_type: str) -> None:
        if not isinstance(data, bytes):
            data = b"".join([chunk async for chunk in data])  # streaming multipart comes in Phase 1
        async with self._client() as s3:
            await s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type,
                                ServerSideEncryption="AES256")

    async def put_file(self, bucket: str, key: str, path: str, content_type: str) -> None:
        async with self._client() as s3:
            await s3.upload_file(path, bucket, key, ExtraArgs={"ContentType": content_type,
                                                               "ServerSideEncryption": "AES256"})

    async def get_file(self, bucket: str, key: str, path: str) -> None:
        async with self._client() as s3:
            await s3.download_file(bucket, key, path)

    async def exists(self, bucket: str, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=bucket, Key=key)
                return True
            except s3.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

    async def delete(self, bucket: str, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=bucket, Key=key)

    async def get_url(self, bucket: str, key: str, ttl_s: int = 300) -> str:
        async with self._client(self._s.S3_PUBLIC_ENDPOINT or None) as s3:
            return await s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                                   ExpiresIn=min(ttl_s, 300))

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        deleted = 0
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    await s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
                    deleted += len(keys)
        return deleted

    async def check(self) -> bool:
        async with self._client() as s3:
            for bucket in self._s.S3_BUCKETS:
                await s3.head_bucket(Bucket=bucket)
        return True
