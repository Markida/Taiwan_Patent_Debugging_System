"""Machine-local, filename-keyed figure snapshots with durable image copies."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from app.paths import get_user_data_dir


class FigureResultStoreError(RuntimeError):
    pass


class FigureResultStore:
    SCHEMA_VERSION = 1
    ASSET_FIELD = "__figure_asset__"

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else get_user_data_dir() / "figure_recognition"
        self._asset_cache = {}

    @staticmethod
    def _hash_file(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _key(kind, names):
        normalized = [unicodedata.normalize("NFKC", name).casefold() for name in names]
        value = json.dumps([kind, normalized], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def identify(self, source_paths, kind="pdf"):
        paths = [Path(path) for path in source_paths]
        if kind not in ("pdf", "images") or not paths or (kind == "pdf" and len(paths) != 1):
            raise FigureResultStoreError("圖式來源種類或檔案數量不正確。")
        names = [path.name for path in paths]
        return {"key": self._key(kind, names), "kind": kind, "names": names,
                "sha256s": [self._hash_file(path) for path in paths]}

    def _validate_identity(self, identity):
        if not isinstance(identity, dict):
            raise FigureResultStoreError("本機圖式來源紀錄格式不正確。")
        names = identity.get("names")
        hashes = identity.get("sha256s")
        if (identity.get("kind") not in ("pdf", "images")
                or not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name for name in names)
                or not isinstance(hashes, list) or len(hashes) != len(names)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)
                or identity.get("key") != self._key(identity["kind"], names)):
            raise FigureResultStoreError("本機圖式來源紀錄格式不正確。")

    def _record_path(self, identity):
        self._validate_identity(identity)
        return self.root / "records" / (identity["key"] + ".json")

    def _asset_path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]{1,10}", name):
            raise FigureResultStoreError("本機圖式圖片路徑不正確。")
        directory = (self.root / "assets").resolve()
        path = (directory / name).resolve()
        if path.parent != directory:
            raise FigureResultStoreError("本機圖式圖片路徑超出保存目錄。")
        return path

    def _copy_asset(self, source):
        source = Path(source).resolve()
        stat = source.stat()
        cache_key = (str(source), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        cached = self._asset_cache.get(cache_key)
        if cached and self._asset_path(cached).is_file():
            return cached
        directory = self.root / "assets"
        directory.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower().lstrip(".")
        if not re.fullmatch(r"[a-z0-9]{1,10}", suffix):
            suffix = "img"
        temporary = None
        try:
            digest = hashlib.sha256()
            with source.open("rb") as reader, tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as writer:
                temporary = Path(writer.name)
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            after = source.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise FigureResultStoreError("保存期間圖片被修改，請稍後重試。")
            name = digest.hexdigest() + "." + suffix
            destination = self._asset_path(name)
            if not destination.exists():
                os.replace(temporary, destination)
                temporary = None
            self._asset_cache[cache_key] = name
            return name
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _encode(self, value, field=""):
        if field in ("image_paths", "source_image_paths") and isinstance(value, list):
            return [{self.ASSET_FIELD: self._copy_asset(path)} for path in value]
        if field in ("image_path", "original_image_path") and value:
            return {self.ASSET_FIELD: self._copy_asset(value)}
        if isinstance(value, dict):
            return {key: self._encode(item, key) for key, item in value.items()
                    if key != "_saved_source_identity"}
        if isinstance(value, (tuple, list)):
            return [self._encode(item) for item in value]
        return value

    def _decode(self, value):
        if isinstance(value, dict):
            if self.ASSET_FIELD in value:
                if len(value) != 1:
                    raise FigureResultStoreError("本機圖式圖片紀錄格式不正確。")
                path = self._asset_path(value[self.ASSET_FIELD])
                if not path.is_file():
                    raise FigureResultStoreError("本機保存的圖式圖片已遺失，請重新辨識。")
                return str(path)
            return {key: self._decode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._decode(item) for item in value]
        return value

    def save(self, identity, state):
        temporary = None
        try:
            destination = self._record_path(identity)
            if not isinstance(state, dict):
                raise FigureResultStoreError("圖式結果格式不正確。")
            envelope = {"schema_version": self.SCHEMA_VERSION, "identity": identity,
                        "state": self._encode(state)}
            encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
        except (OSError, TypeError, ValueError) as exc:
            raise FigureResultStoreError(f"無法保存本機圖式結果：{exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self, identity):
        try:
            path = self._record_path(identity)
            if not path.exists():
                return None
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("schema_version") != self.SCHEMA_VERSION:
                raise FigureResultStoreError("本機圖式結果版本不支援，請重新辨識。")
            saved_identity = envelope.get("identity")
            self._validate_identity(saved_identity)
            if saved_identity["key"] != identity["key"] or not isinstance(envelope.get("state"), dict):
                raise FigureResultStoreError("本機圖式紀錄與檔案不符。")
            state = self._decode(envelope["state"])
            # Preview paths may only refer to the durable local image copies.
            for field in ("image_paths", "source_image_paths"):
                paths = state.get(field)
                if not isinstance(paths, list) or any(
                    not isinstance(item, str) or Path(item).resolve().parent != (self.root / "assets").resolve()
                    or not Path(item).is_file() for item in paths
                ):
                    raise FigureResultStoreError("本機圖式圖片紀錄不完整。")
            state["_saved_source_identity"] = saved_identity
            return state
        except (OSError, ValueError, TypeError) as exc:
            raise FigureResultStoreError(f"無法讀取本機圖式結果：{exc}") from exc
