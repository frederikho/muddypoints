"""Cached boto3 clients. `reset()` is used by tests (moto) and the dev server."""
import functools

import boto3
from botocore.config import Config

from .config import settings


@functools.lru_cache(maxsize=None)
def dynamodb():
    kwargs = {"region_name": settings.region}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    return boto3.resource("dynamodb", **kwargs)


@functools.lru_cache(maxsize=None)
def table():
    return dynamodb().Table(settings.table_name)


@functools.lru_cache(maxsize=None)
def s3():
    if settings.endpoint_url:
        return boto3.client("s3", region_name=settings.region, endpoint_url=settings.endpoint_url,
                            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    # Regional virtual-host endpoint so presigned URLs work from browsers (CORS) in every region.
    return boto3.client("s3", region_name=settings.region,
                        endpoint_url=f"https://s3.{settings.region}.amazonaws.com",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


@functools.lru_cache(maxsize=None)
def ses():
    return boto3.client("ses", region_name=settings.region)


@functools.lru_cache(maxsize=None)
def logs():
    return boto3.client("logs", region_name=settings.region)


def reset() -> None:
    for fn in (dynamodb, table, s3, ses, logs):
        fn.cache_clear()
