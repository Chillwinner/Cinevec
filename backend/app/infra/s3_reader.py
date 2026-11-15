from __future__ import annotations

from pathlib import Path

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.lifecycle import ListCompleteResult
from app.domain.models import ObjectEntry, Source
from app.infra.redact import redact


class S3SourceReader:
    """Reads S3-compatible storage. Never mutates or deletes source objects."""

    def _client(self, source: Source, access_key: str | None, secret_key: str | None):
        endpoint = (source.endpoint or "").strip()
        bucket = (source.bucket or "").strip()
        if not endpoint or not bucket or not access_key or not secret_key:
            raise NotConfiguredError(
                "该存储源的 Endpoint、Bucket 或密钥尚未配置完整，无法连接 S3/MinIO。"
            )
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise NotConfiguredError(
                "本机未安装 boto3，无法探测 S3/MinIO。可在后端虚拟环境中安装依赖后再试。"
            ) from exc
        session = boto3.session.Session()
        return session.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=source.region or "us-east-1",
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        ), bucket

    def probe(self, source: Source, access_key: str | None, secret_key: str | None) -> None:
        client, bucket = self._client(source, access_key, secret_key)
        try:
            client.head_bucket(Bucket=bucket)
        except Exception as exc:
            raise DependencyError(redact(f"连接 S3/MinIO 失败：{exc}")) from exc

    def list_prefix(
        self,
        source: Source,
        access_key: str | None,
        secret_key: str | None,
        prefix: str,
    ) -> list[ObjectEntry]:
        client, bucket = self._client(source, access_key, secret_key)
        clean = prefix.lstrip("/")
        try:
            response = client.list_objects_v2(
                Bucket=bucket, Prefix=clean, Delimiter="/", MaxKeys=200
            )
        except Exception as exc:
            raise DependencyError(redact(f"列举对象失败：{exc}")) from exc
        entries: list[ObjectEntry] = []
        for prefix_item in response.get("CommonPrefixes") or []:
            key = prefix_item.get("Prefix") or ""
            entries.append(ObjectEntry(key=key, is_dir=True))
        for item in response.get("Contents") or []:
            key = item.get("Key") or ""
            if key == clean:
                continue
            modified = item.get("LastModified")
            entries.append(
                ObjectEntry(
                    key=key,
                    is_dir=False,
                    size=item.get("Size"),
                    last_modified=modified.isoformat() if modified else None,
                    etag=(item.get("ETag") or "").strip('"') or None,
                )
            )
        return entries

    def download_readonly(
        self,
        source: Source,
        access_key: str | None,
        secret_key: str | None,
        object_key: str,
        dest_path: Path,
    ) -> Path:
        client, bucket = self._client(source, access_key, secret_key)
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest_path.with_name(dest_path.name + ".part")
        try:
            client.download_file(bucket, object_key, str(tmp))
        except Exception as exc:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise DependencyError(redact(f"只读下载失败：{exc}")) from exc
        if dest_path.exists():
            dest_path.chmod(0o644)
            dest_path.unlink()
        tmp.replace(dest_path)
        dest_path.chmod(0o444)
        return dest_path

    def head_object(
        self,
        source: Source,
        access_key: str | None,
        secret_key: str | None,
        object_key: str,
    ) -> dict:
        client, bucket = self._client(source, access_key, secret_key)
        try:
            obj = client.head_object(Bucket=bucket, Key=object_key)
        except Exception as exc:
            raise DependencyError(redact(f"读取源对象头失败：{exc}")) from exc
        return {
            "size": int(obj.get("ContentLength") or 0),
            "content_type": obj.get("ContentType") or "application/octet-stream",
        }

    def get_range(
        self,
        source: Source,
        access_key: str | None,
        secret_key: str | None,
        object_key: str,
        start: int,
        end: int,
    ) -> bytes:
        client, bucket = self._client(source, access_key, secret_key)
        try:
            obj = client.get_object(
                Bucket=bucket,
                Key=object_key,
                Range=f"bytes={int(start)}-{int(end)}",
            )
            return obj["Body"].read()
        except Exception as exc:
            raise DependencyError(redact(f"分段读取源对象失败：{exc}")) from exc

    def list_complete(
        self,
        source: Source,
        access_key: str | None,
        secret_key: str | None,
        prefix: str = "",
    ) -> ListCompleteResult:
        """完整分页列举。中途失败则 complete=False，不得据此判定文件缺失。"""
        client, bucket = self._client(source, access_key, secret_key)
        clean = prefix.lstrip("/")
        entries: list[ObjectEntry] = []
        token = None
        pages = 0
        try:
            while True:
                kwargs = {"Bucket": bucket, "Prefix": clean, "MaxKeys": 1000}
                if token:
                    kwargs["ContinuationToken"] = token
                response = client.list_objects_v2(**kwargs)
                pages += 1
                for item in response.get("Contents") or []:
                    key = item.get("Key") or ""
                    if not key or key.endswith("/"):
                        continue
                    modified = item.get("LastModified")
                    entries.append(
                        ObjectEntry(
                            key=key,
                            is_dir=False,
                            size=item.get("Size"),
                            last_modified=modified.isoformat() if modified else None,
                            etag=(item.get("ETag") or "").strip('"') or None,
                        )
                    )
                if response.get("IsTruncated") and response.get("NextContinuationToken"):
                    token = response["NextContinuationToken"]
                    continue
                break
        except Exception as exc:
            return ListCompleteResult(
                entries=entries,
                complete=False,
                pages=pages,
                truncated=True,
                error_code="list_incomplete",
                error_message=redact(f"完整列举未完成：{exc}"),
            )
        return ListCompleteResult(entries=entries, complete=True, pages=pages, truncated=False)
