"""YOLO 字元模型共用的類別定義。"""

import string


DIGITS = tuple(string.digits)
LETTERS = tuple(string.ascii_uppercase)
PRIME_CLASS_NAME = "prime"
PRIME_MARKS = {"'", "’", "′", "`"}

CLASS_NAMES = DIGITS + LETTERS + (PRIME_CLASS_NAME,)
CLASS_TO_ID = {name: index for index, name in enumerate(CLASS_NAMES)}


def normalize_character(character):
    """將來源字元正規化成模型使用的 class name。"""

    character = str(character).strip()

    if character in PRIME_MARKS:
        return PRIME_CLASS_NAME

    if len(character) == 1 and character.isascii() and character.isdigit():
        return character

    if len(character) == 1 and character.isascii() and character.isalpha():
        return character.upper()

    return ""


def display_character(class_name):
    """將 class name 轉回應用程式顯示的字元。"""

    if class_name == PRIME_CLASS_NAME:
        return "'"

    return class_name
