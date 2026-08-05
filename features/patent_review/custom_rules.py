"""Persistent, literal-text rules created by the application user."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import unicodedata
from typing import Iterable, List, Optional, Tuple

from app.paths import get_app_install_dir


CUSTOM_TEXT_RULES_FILENAME = "custom_text_rules.json"
CUSTOM_TEXT_RULES_SCHEMA_VERSION = 2
MAX_CUSTOM_RULE_LENGTH = 80
CUSTOM_RULE_BLACKLIST = "blacklist"
CUSTOM_RULE_WHITELIST = "whitelist"
CUSTOM_RULE_TYPES = {CUSTOM_RULE_BLACKLIST, CUSTOM_RULE_WHITELIST}


class CustomRuleError(ValueError):
    """Base error for invalid rules or an unreadable rule file."""


class CustomRuleValidationError(CustomRuleError):
    """Raised when a user-entered literal is not a valid custom rule."""


class CustomRuleStorageError(CustomRuleError):
    """Raised when the persistent JSON file cannot be read or written."""


def normalize_custom_rule_text(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if not text:
        raise CustomRuleValidationError("偵測文字不能是空白。")
    if any(character in text for character in "\r\n"):
        raise CustomRuleValidationError("每一條規則只能包含單行文字。")
    if len(text) > MAX_CUSTOM_RULE_LENGTH:
        raise CustomRuleValidationError(
            f"偵測文字不可超過 {MAX_CUSTOM_RULE_LENGTH} 個字元。"
        )
    return text


def normalize_custom_rule_type(value: object) -> str:
    rule_type = str(value or CUSTOM_RULE_BLACKLIST).strip().lower()
    if rule_type not in CUSTOM_RULE_TYPES:
        raise CustomRuleValidationError("自訂規則類型必須是黑名單或白名單。")
    return rule_type


def custom_rule_id(text: str, rule_type: str = CUSTOM_RULE_BLACKLIST) -> str:
    normalized_type = normalize_custom_rule_type(rule_type)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10].upper()
    # Keep legacy blacklist IDs stable so existing reports and saved files do
    # not change merely because whitelist support was added.
    prefix = "USR" if normalized_type == CUSTOM_RULE_BLACKLIST else "USR-W"
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class CustomTextRule:
    rule_id: str
    text: str
    rule_type: str = CUSTOM_RULE_BLACKLIST

    @classmethod
    def from_text(
        cls,
        value: object,
        rule_type: str = CUSTOM_RULE_BLACKLIST,
    ) -> "CustomTextRule":
        text = normalize_custom_rule_text(value)
        normalized_type = normalize_custom_rule_type(rule_type)
        return cls(
            rule_id=custom_rule_id(text, normalized_type),
            text=text,
            rule_type=normalized_type,
        )


class CustomTextRuleStore:
    """Load and atomically save literal rules beside the application."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(
            path or (get_app_install_dir() / CUSTOM_TEXT_RULES_FILENAME)
        )

    def load(self) -> List[CustomTextRule]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CustomRuleStorageError(
                f"無法讀取自訂規則檔：{self.path}\n{error}"
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
            raise CustomRuleStorageError(
                f"自訂規則檔格式不正確：{self.path}"
            )

        rules: List[CustomTextRule] = []
        seen = set()
        try:
            for entry in payload["rules"]:
                if not isinstance(entry, dict):
                    raise CustomRuleValidationError("規則項目必須是物件。")
                # Version-1 files contain only ``text`` and are therefore
                # interpreted as blacklist entries.
                rule = CustomTextRule.from_text(
                    entry.get("text", ""),
                    entry.get("rule_type", CUSTOM_RULE_BLACKLIST),
                )
                identity = (rule.rule_type, rule.text)
                if identity not in seen:
                    seen.add(identity)
                    rules.append(rule)
        except CustomRuleValidationError as error:
            raise CustomRuleStorageError(
                f"自訂規則檔包含無效內容：{self.path}\n{error}"
            ) from error
        return rules

    def save(self, rules: Iterable[CustomTextRule]) -> None:
        normalized: List[CustomTextRule] = []
        seen = set()
        for item in rules:
            rule = CustomTextRule.from_text(
                item.text,
                getattr(item, "rule_type", CUSTOM_RULE_BLACKLIST),
            )
            identity = (rule.rule_type, rule.text)
            if identity not in seen:
                seen.add(identity)
                normalized.append(rule)
        payload = {
            "schema_version": CUSTOM_TEXT_RULES_SCHEMA_VERSION,
            "rules": [asdict(rule) for rule in normalized],
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise CustomRuleStorageError(
                f"無法儲存自訂規則檔：{self.path}\n{error}"
            ) from error

    def add(
        self,
        text: object,
        rule_type: str = CUSTOM_RULE_BLACKLIST,
    ) -> Tuple[CustomTextRule, bool]:
        rule = CustomTextRule.from_text(text, rule_type)
        rules = self.load()
        if any(
            existing.text == rule.text and existing.rule_type == rule.rule_type
            for existing in rules
        ):
            return rule, False
        rules.append(rule)
        self.save(rules)
        return rule, True

    def remove(self, rule_ids: Iterable[str]) -> int:
        selected = set(rule_ids)
        rules = self.load()
        remaining = [rule for rule in rules if rule.rule_id not in selected]
        removed = len(rules) - len(remaining)
        if removed:
            self.save(remaining)
        return removed
