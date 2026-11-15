from __future__ import annotations

from pathlib import Path

from app.domain.errors import ConflictError, DependencyError, NotConfiguredError
from app.domain.models import Source
from app.infra.redact import redact


class UnconfiguredFileStore:
    def available(self) -> bool:
        return False

    def status_message(self) -> str:
        return "预览存储需要已配置的平台 S3/MinIO，当前未接通，不会改用本地目录冒充。"

    def exists(self, key: str) -> bool:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def get_bytes(self, key: str) -> bytes:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def head(self, key: str) -> dict:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def get_range(self, key: str, start: int, end: int) -> bytes:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def put_stream(self, key: str, stream, content_type: str, *, overwrite: bool = False) -> str:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def delete_object(self, key: str) -> bool:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")

    def delete_objects(self, keys: list[str]) -> list[str]:
        raise NotConfiguredError(self.status_message(), code="filestore_unconfigured")


class LocalFileStore:
    """仅供测试使用的临时目录适配器，不得在默认应用里伪装成已连接的 S3。"""

    def __init__(self, root: Path, label: str = "test-temp-store") -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.label = label
        self.put_count = 0
        self.skipped_existing = 0

    def available(self) -> bool:
        return True

    def status_message(self) -> str:
        return f"测试用临时文件存储（{self.label}），不是已连接的 S3。"

    def _path(self, key: str) -> Path:
        safe = key.lstrip("/")
        dest = (self.root / safe).resolve()
        if not str(dest).startswith(str(self.root.resolve())):
            raise DependencyError("非法存储键")
        return dest

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        dest = self._path(key)
        if dest.is_file():
            self.skipped_existing += 1
            return key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.put_count += 1
        return key

    def get_bytes(self, key: str) -> bytes:
        dest = self._path(key)
        if not dest.is_file():
            raise DependencyError("预览对象不存在")
        return dest.read_bytes()

    def head(self, key: str) -> dict:
        dest = self._path(key)
        if not dest.is_file():
            raise DependencyError("对象不存在")
        return {"size": dest.stat().st_size, "content_type": "application/octet-stream"}

    def get_range(self, key: str, start: int, end: int) -> bytes:
        dest = self._path(key)
        if not dest.is_file():
            raise DependencyError("对象不存在")
        size = dest.stat().st_size
        start = max(0, int(start))
        end = min(int(end), size - 1) if size else 0
        if start > end:
            return b""
        with dest.open("rb") as handle:
            handle.seek(start)
            return handle.read(end - start + 1)

    def put_stream(self, key: str, stream, content_type: str, *, overwrite: bool = False) -> str:
        dest = self._path(key)
        if dest.is_file() and not overwrite:
            raise ConflictError("对象已存在，拒绝覆盖")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        self.put_count += 1
        return key

    def delete_object(self, key: str) -> bool:
        dest = self._path(key)
        if not dest.is_file():
            return False
        dest.unlink()
        return True

    def delete_objects(self, keys: list[str]) -> list[str]:
        deleted: list[str] = []
        for key in keys:
            self.delete_object(key)
            deleted.append(key)
        return deleted


class S3FileStore:
    def __init__(self, source: Source, access_key: str | None, secret_key: str | None) -> None:
        self._source = source
        self._access = access_key
        self._secret = secret_key

    def available(self) -> bool:
        return bool(
            (self._source.endpoint or "").strip()
            and (self._source.bucket or "").strip()
            and self._access
            and self._secret
        )

    def status_message(self) -> str:
        if self.available():
            return f"将使用平台存储桶 {self._source.bucket}（凭证不回显）。"
        return "已选择平台存储，但 Endpoint、Bucket 或密钥不完整。"

    def _client(self):
        if not self.available():
            raise NotConfiguredError("平台 S3/MinIO 未配置完整，无法写入预览。", code="filestore_unconfigured")
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise NotConfiguredError("未安装 boto3，无法写入 S3。") from exc
        session = boto3.session.Session()
        return session.client(
            "s3",
            endpoint_url=self._source.endpoint.strip(),
            aws_access_key_id=self._access,
            aws_secret_access_key=self._secret,
            region_name=self._source.region or "us-east-1",
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        ), self._source.bucket.strip()

    def exists(self, key: str) -> bool:
        client, bucket = self._client()
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except Exception as exc:
            name = type(exc).__name__
            if "NoSuchKey" in name or "404" in str(exc) or "Not Found" in str(exc):
                return False
            raise DependencyError(redact(f"检查预览对象失败：{exc}")) from exc

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        if self.exists(key):
            return key
        client, bucket = self._client()
        try:
            client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        except Exception as exc:
            raise DependencyError(redact(f"写入预览失败：{exc}")) from exc
        return key

    def get_bytes(self, key: str) -> bytes:
        client, bucket = self._client()
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()
        except Exception as exc:
            raise DependencyError(redact(f"读取预览失败：{exc}")) from exc

    def head(self, key: str) -> dict:
        client, bucket = self._client()
        try:
            obj = client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise DependencyError(redact(f"读取对象头失败：{exc}")) from exc
        return {
            "size": int(obj.get("ContentLength") or 0),
            "content_type": obj.get("ContentType") or "application/octet-stream",
        }

    def get_range(self, key: str, start: int, end: int) -> bytes:
        client, bucket = self._client()
        try:
            obj = client.get_object(Bucket=bucket, Key=key, Range=f"bytes={int(start)}-{int(end)}")
            return obj["Body"].read()
        except Exception as exc:
            raise DependencyError(redact(f"分段读取对象失败：{exc}")) from exc

    def put_stream(self, key: str, stream, content_type: str, *, overwrite: bool = False) -> str:
        if self.exists(key) and not overwrite:
            raise ConflictError("对象已存在，拒绝覆盖")
        client, bucket = self._client()
        try:
            extra = {"ContentType": content_type or "application/octet-stream"}
            client.upload_fileobj(stream, bucket, key, ExtraArgs=extra)
        except Exception as exc:
            raise DependencyError(redact(f"流式写入对象失败：{exc}")) from exc
        return key

    def presign_get(self, key: str, expires_seconds: int = 120) -> str:
        """短 TTL 签名 URL，仅用于有权限文件的临时读取，不是长期公开地址。"""
        expires_seconds = max(1, min(int(expires_seconds), 300))
        client, bucket = self._client()
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        except Exception as exc:
            raise DependencyError(redact(f"签发短时预览地址失败：{exc}")) from exc

    def delete_object(self, key: str) -> bool:
        client, bucket = self._client()
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise DependencyError(redact(f"删除对象失败：{exc}")) from exc
        return True

    def delete_objects(self, keys: list[str]) -> list[str]:
        if not keys:
            return []
        client, bucket = self._client()
        deleted: list[str] = []
        try:
            for i in range(0, len(keys), 1000):
                chunk = keys[i : i + 1000]
                resp = client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
                )
                errors = resp.get("Errors") or []
                if errors:
                    first = errors[0]
                    raise DependencyError(
                        redact(f"批量删除对象失败：{first.get('Key')}: {first.get('Message') or first.get('Code')}")
                    )
                deleted.extend(chunk)
        except DependencyError:
            raise
        except Exception as exc:
            raise DependencyError(redact(f"批量删除对象失败：{exc}")) from exc
        return deleted


class PlatformResolvingFileStore:
    """按需解析当前平台存储，避免只在进程启动时绑定一次导致登记后仍显示未接通。

    连接细节留在 infra；application 仍然只依赖 FileStore 端口。
    测试注入的 override 不会走这一层。
    """

    def __init__(self, metadata) -> None:
        self._metadata = metadata

    def _inner(self):
        source = self._metadata.get_platform_storage()
        if source is None:
            return UnconfiguredFileStore()
        access, secret = self._metadata.get_source_credentials(source.id)
        return S3FileStore(source, access, secret)

    def available(self) -> bool:
        return self._inner().available()

    def status_message(self) -> str:
        return self._inner().status_message()

    def exists(self, key: str) -> bool:
        return self._inner().exists(key)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        return self._inner().put_bytes(key, data, content_type)

    def get_bytes(self, key: str) -> bytes:
        return self._inner().get_bytes(key)

    def head(self, key: str) -> dict:
        inner = self._inner()
        if hasattr(inner, "head"):
            return inner.head(key)
        data = inner.get_bytes(key)
        return {"size": len(data), "content_type": "application/octet-stream"}

    def get_range(self, key: str, start: int, end: int) -> bytes:
        inner = self._inner()
        if hasattr(inner, "get_range"):
            return inner.get_range(key, start, end)
        data = inner.get_bytes(key)
        return data[int(start) : int(end) + 1]

    def put_stream(self, key: str, stream, content_type: str, *, overwrite: bool = False) -> str:
        inner = self._inner()
        if hasattr(inner, "put_stream"):
            return inner.put_stream(key, stream, content_type, overwrite=overwrite)
        raise NotConfiguredError(inner.status_message(), code="filestore_unconfigured")

    def presign_get(self, key: str, expires_seconds: int = 120) -> str:
        inner = self._inner()
        if hasattr(inner, "presign_get"):
            return inner.presign_get(key, expires_seconds)
        raise NotConfiguredError(inner.status_message(), code="filestore_unconfigured")

    def delete_object(self, key: str) -> bool:
        inner = self._inner()
        if hasattr(inner, "delete_object"):
            return inner.delete_object(key)
        raise NotConfiguredError(inner.status_message(), code="filestore_unconfigured")

    def delete_objects(self, keys: list[str]) -> list[str]:
        if not keys:
            return []
        inner = self._inner()
        if hasattr(inner, "delete_objects"):
            return inner.delete_objects(keys)
        if hasattr(inner, "delete_object"):
            deleted: list[str] = []
            for key in keys:
                inner.delete_object(key)
                deleted.append(key)
            return deleted
        raise NotConfiguredError(inner.status_message(), code="filestore_unconfigured")
