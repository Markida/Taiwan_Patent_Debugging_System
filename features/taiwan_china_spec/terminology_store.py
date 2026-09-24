"""Shared terminology loading and publishing with safe offline fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Iterable

from .converter import (
    ConversionError,
    default_terminology_path,
    normalize_terminology_pairs,
    parse_terminology_text,
)


CLOUD_TERMINOLOGY_DIRECTORY = Path(
    r"\\sic11\Doc\_CP\Saint-Island\_Patent\_MDS"
)
CLOUD_TERMINOLOGY_FILENAME = "taiwan_china_terminology.txt"
CLOUD_TERMINOLOGY_ENVIRONMENT_VARIABLE = (
    "SAINT_ISLAND_TW_CN_TERMINOLOGY_PATH"
)
# Increment this when a release changes the cache format or bundled dictionary.
# A versioned cache prevents an old offline snapshot from hiding newer rules.
TERMINOLOGY_CACHE_VERSION = 4


@dataclass(frozen=True)
class TerminologySnapshot:
    pairs: tuple[tuple[str, str], ...]
    source_kind: str
    source_path: Path
    warning: str = ""


def default_cloud_terminology_path() -> Path:
    override = os.environ.get(
        CLOUD_TERMINOLOGY_ENVIRONMENT_VARIABLE,
        "",
    ).strip()
    if override:
        return Path(override)
    return CLOUD_TERMINOLOGY_DIRECTORY / CLOUD_TERMINOLOGY_FILENAME


def default_terminology_cache_path() -> Path:
    return (
        Path.home()
        / "Documents"
        / "Saint-Island_Patent_MDS"
        / "cache"
        / f"taiwan_china_terminology.v{TERMINOLOGY_CACHE_VERSION}.txt"
    )


def _read_pairs(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp950")
    pairs = tuple(parse_terminology_text(text))
    if not pairs:
        raise ConversionError("用語辭典沒有可用條目。")
    return pairs


def _serialize_pairs(pairs: tuple[tuple[str, str], ...]) -> str:
    # Do not sort or deduplicate: replacements form a user-controlled pipeline.
    # A blank target intentionally means "delete this source phrase".
    return "".join(f"{source}\t{target}\n" for source, target in pairs)


def _write_pairs_atomically(
    path: Path,
    pairs: tuple[tuple[str, str], ...],
) -> None:
    """Replace *path* only after a complete sibling file has been written."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.write_text(_serialize_pairs(pairs), encoding="utf-8")
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class TerminologyDictionaryStore:
    """Load, cache, and publish the ordered company terminology dictionary."""

    def __init__(
        self,
        cloud_path: Path | None = None,
        cache_path: Path | None = None,
        bundled_path: Path | None = None,
    ):
        self.cloud_path = Path(cloud_path or default_cloud_terminology_path())
        self.cache_path = Path(cache_path or default_terminology_cache_path())
        self.bundled_path = Path(bundled_path or default_terminology_path())

    def _snapshot(self, path: Path, source_kind: str, warning: str = ""):
        return TerminologySnapshot(
            pairs=_read_pairs(path),
            source_kind=source_kind,
            source_path=path,
            warning=warning,
        )

    def load_bundled(self, warning: str = "") -> TerminologySnapshot:
        try:
            return self._snapshot(self.bundled_path, "內建", warning)
        except (OSError, UnicodeError, ConversionError) as exc:
            raise ConversionError(
                f"無法讀取內建用語辭典：{self.bundled_path}\n{exc}"
            ) from exc

    def _cache_pairs(self, pairs: tuple[tuple[str, str], ...]) -> str:
        # The preferred dictionary is refreshed whenever this page opens.
        # Avoid replacing an already identical local snapshot: on roaming or
        # redirected Documents folders that write can be much slower than the
        # small comparison and creates needless antivirus/file-sync work.
        try:
            if self.cache_path.is_file() and _read_pairs(self.cache_path) == pairs:
                return ""
        except (OSError, UnicodeError, ConversionError):
            # A damaged or unreadable cache is repaired by the atomic write
            # below; only a verified identical snapshot may skip the write.
            pass
        try:
            _write_pairs_atomically(self.cache_path, pairs)
        except OSError as exc:
            return f"雲端辭典已載入，但無法更新本機快取：{exc}"
        return ""

    def load_preferred(self) -> TerminologySnapshot:
        cloud_warning = ""
        try:
            if self.cloud_path.is_file():
                pairs = _read_pairs(self.cloud_path)
                cache_warning = self._cache_pairs(pairs)
                return TerminologySnapshot(
                    pairs=pairs,
                    source_kind="雲端",
                    source_path=self.cloud_path,
                    warning=cache_warning,
                )
        except (OSError, UnicodeError, ConversionError) as exc:
            cloud_warning = f"雲端辭典無法使用，已自動改用備援：{exc}"

        try:
            if self.cache_path.is_file():
                return self._snapshot(
                    self.cache_path,
                    "快取",
                    cloud_warning,
                )
        except (OSError, UnicodeError, ConversionError) as exc:
            cache_warning = f"本機快取無法使用：{exc}"
            cloud_warning = "；".join(
                value for value in (cloud_warning, cache_warning) if value
            )

        return self.load_bundled(cloud_warning)

    def upload_pairs(
        self,
        pairs: Iterable[tuple[object, object]],
    ) -> TerminologySnapshot:
        """Atomically replace the shared TXT and refresh the local cache."""

        normalized = tuple(normalize_terminology_pairs(pairs))
        if not normalized:
            raise ConversionError("用語辭典沒有可上傳的條目。")
        try:
            _write_pairs_atomically(self.cloud_path, normalized)
        except OSError as exc:
            raise ConversionError(
                f"無法上傳共用用語辭典：{self.cloud_path}\n{exc}"
            ) from exc

        cache_warning = self._cache_pairs(normalized)
        return TerminologySnapshot(
            pairs=normalized,
            source_kind="雲端",
            source_path=self.cloud_path,
            warning=cache_warning,
        )
