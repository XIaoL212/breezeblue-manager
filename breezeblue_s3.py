"""Minimal S3-compatible object storage client (AWS SigV4, path-style).

Same dependency-free approach as the XL Agent helper: only the handful of
operations the snapshot worker needs, so the image does not need boto3.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import httpx


def _uri_encode(value: str, encode_slash: bool = True) -> str:
    safe = "" if encode_slash else "/"
    return urllib.parse.quote(str(value), safe=safe)


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, datestamp: str, region: str) -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, "s3")
    return _hmac_sha256(k_service, "aws4_request")


def _canonical_query(params: Optional[dict]) -> str:
    if not params:
        return ""
    return "&".join(
        f"{_uri_encode(name)}={_uri_encode(value)}"
        for name, value in sorted((str(k), str(v)) for k, v in params.items())
    )


def _signature(secret_key, datestamp, region, method, canonical_uri, query, headers, signed_headers, payload, amzdate) -> str:
    canonical_headers = "".join(f"{name.lower()}:{headers[name].strip()}\n" for name in sorted(headers))
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_request = "\n".join(
        [method, canonical_uri, query, canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amzdate,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    return hmac.new(_signing_key(secret_key, datestamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _normalize(cfg: dict) -> dict:
    endpoint = str(cfg.get("endpoint") or "").strip().rstrip("/")
    if endpoint and not endpoint.startswith(("https://", "http://")):
        endpoint = "https://" + endpoint
    return {
        "endpoint": endpoint,
        "host": urllib.parse.urlparse(endpoint).netloc,
        "bucket": str(cfg.get("bucket") or "").strip(),
        "access_key": str(cfg.get("access_key") or "").strip(),
        "secret_key": str(cfg.get("secret_key") or ""),
        "region": str(cfg.get("region") or "").strip() or "us-east-1",
        "prefix": str(cfg.get("prefix") or "").strip().strip("/"),
        "timeout": float(cfg.get("timeout") or 120),
    }


def _object_path(cfg: dict, key: str) -> str:
    if str(key or "").startswith("s3://"):
        rest = key[len("s3://"):]
        key = rest.split("/", 1)[1] if "/" in rest else ""
    full = "/".join(part for part in (cfg["prefix"], key) if part)
    return "/" + _uri_encode(cfg["bucket"], encode_slash=False) + "/" + _uri_encode(full, encode_slash=False)


def _signed_headers(cfg: dict, method: str, path: str, query: str, payload: bytes) -> dict:
    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {
        "host": cfg["host"],
        "x-amz-date": amzdate,
        "x-amz-content-sha256": payload_hash,
    }
    signed_headers = ";".join(sorted(headers))
    signature = _signature(
        cfg["secret_key"], datestamp, cfg["region"], method, path, query,
        headers, signed_headers, payload, amzdate,
    )
    auth = (
        f"AWS4-HMAC-SHA256 Credential={cfg['access_key']}/{datestamp}/{cfg['region']}/s3/aws4_request, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    out = dict(headers)
    out["Authorization"] = auth
    return out


def _require(cfg: dict) -> dict:
    c = _normalize(cfg)
    if not c["bucket"] or not c["access_key"] or not c["secret_key"]:
        raise ValueError("S3 配置缺少 bucket / access_key / secret_key")
    return c


async def s3_put_object(cfg: dict, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    c = _require(cfg)
    path = _object_path(c, key)
    headers = _signed_headers(c, "PUT", path, "", data)
    headers["Content-Type"] = content_type
    async with httpx.AsyncClient(timeout=c["timeout"]) as client:
        resp = await client.put(c["endpoint"] + path, content=data, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"S3 PUT 失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return "s3://" + c["bucket"] + path


async def s3_get_object(cfg: dict, key: str) -> bytes:
    c = _require(cfg)
    path = _object_path(c, key)
    headers = _signed_headers(c, "GET", path, "", b"")
    async with httpx.AsyncClient(timeout=c["timeout"]) as client:
        resp = await client.get(c["endpoint"] + path, headers=headers)
    if resp.status_code == 404:
        raise FileNotFoundError(key)
    if resp.status_code >= 400:
        raise RuntimeError(f"S3 GET 失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.content


async def s3_delete_object(cfg: dict, key: str) -> None:
    c = _require(cfg)
    path = _object_path(c, key)
    headers = _signed_headers(c, "DELETE", path, "", b"")
    async with httpx.AsyncClient(timeout=c["timeout"]) as client:
        resp = await client.delete(c["endpoint"] + path, headers=headers)
    if resp.status_code >= 400 and resp.status_code != 404:
        raise RuntimeError(f"S3 DELETE 失败 HTTP {resp.status_code}: {resp.text[:300]}")


async def s3_list_objects(cfg: dict, prefix: str = "", max_keys: int = 1000) -> list:
    """List objects under a prefix, transparently following continuation tokens."""
    c = _require(cfg)
    want = "/".join(part for part in (c["prefix"], str(prefix or "").strip("/")) if part)
    items: list = []
    token = ""
    root = "/".join(part for part in (c["prefix"],) if part)
    async with httpx.AsyncClient(timeout=c["timeout"]) as client:
        while True:
            params = {"list-type": "2", "prefix": want, "max-keys": str(max_keys)}
            if token:
                params["continuation-token"] = token
            query = _canonical_query(params)
            list_path = "/" + _uri_encode(c["bucket"], encode_slash=False) + "/"
            headers = _signed_headers(c, "GET", list_path, query, b"")
            resp = await client.get(c["endpoint"] + list_path + "?" + query, headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"S3 LIST 失败 HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.text
            for block in re.findall(r"<Contents>(.*?)</Contents>", body, flags=re.S):
                def field(name: str) -> str:
                    found = re.search(rf"<{name}>(.*?)</{name}>", block, flags=re.S)
                    return urllib.parse.unquote(found.group(1)) if found else ""
                raw_key = field("Key")
                items.append(
                    {
                        "key": raw_key[len(root) + 1:] if root and raw_key.startswith(root + "/") else raw_key,
                        "size": int(field("Size") or 0),
                        "last_modified": field("LastModified"),
                    }
                )
            if "<IsTruncated>true</IsTruncated>" not in body.lower():
                break
            found = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", body, flags=re.S)
            if not found:
                break
            token = found.group(1)
    return items


def s3_config_public(cfg: dict) -> dict:
    return {
        "endpoint": str(cfg.get("endpoint") or ""),
        "bucket": str(cfg.get("bucket") or ""),
        "region": str(cfg.get("region") or ""),
        "prefix": str(cfg.get("prefix") or ""),
        "access_key": str(cfg.get("access_key") or ""),
        "has_secret": bool(cfg.get("secret_key")),
    }
