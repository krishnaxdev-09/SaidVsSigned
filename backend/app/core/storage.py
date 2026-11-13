"""Object storage (doc 01 §8). Phase 0 uses only the readiness check; put/get_url/delete_prefix serve later phases."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

import aioboto3

from app.core.config import Settings


class ObjectStore(Protocol):
    async def put(self, bucket: str, key: str, data: bytes | AsyncIterator[bytes], content_type: str) -> None: ...
    async def get_url(self, bucket: str, key: str, ttl_s: int = 300) -> str: ...
    async def delete_prefix(self, bucket: str, prefix: str) -> int: ...
    async def check(self) -> bool: ...


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._session = aioboto3.Session()

    def _client(self):  # type: ignore[no-untyped-def]
        return self._session.client(
            "s3", endpoint_url=self._s.S3_ENDPOINT, region_name=self._s.S3_REGION,
            aws_access_key_id=self._s.S3_ACCESS_KEY.get_secret_value(),
            aws_secret_access_key=self._s.S3_SECRET_KEY.get_secret_value())

    async def put(self, bucket: str, key: str, data: bytes | AsyncIterator[bytes], content_type: str) -> None:
        if not isinstance(data, bytes):
            data = b"".join([chunk async for chunk in data])  # streaming multipart comes in Phase 1
        async with self._client() as s3:
            await s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type,
                                ServerSideEncryption="AES256")

    async def get_url(self, bucket: str, key: str, ttl_s: int = 300) -> str:
        async with self._client() as s3:
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
