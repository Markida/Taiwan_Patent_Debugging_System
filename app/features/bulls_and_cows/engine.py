"""Rules and state machines for the classic four-digit 1A2B game."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Iterable


class BullsAndCowsError(ValueError):
    """Raised when a secret or guess does not follow the game rules."""


def validate_number(value: object) -> str:
    """Return a normalized valid number.

    Treat the answer as a four-character code: exactly four different
    decimal digits.  Zero is allowed in the first position.
    """

    text = str(value).strip()
    if len(text) != 4 or not text.isascii() or not text.isdigit():
        raise BullsAndCowsError("請輸入恰好四位阿拉伯數字。")
    if len(set(text)) != 4:
        raise BullsAndCowsError("四個數字不可重複。")
    return text


def generate_secret(random_source: random.Random | None = None) -> str:
    """Generate a valid four-digit secret without repeated digits."""

    source = random_source or random.SystemRandom()
    return "".join(source.sample(list("0123456789"), 4))


@dataclass(frozen=True)
class GuessResult:
    bulls: int
    cows: int

    @property
    def notation(self) -> str:
        return f"{self.bulls}A{self.cows}B"

    @property
    def won(self) -> bool:
        return self.bulls == 4


@dataclass(frozen=True)
class GuessRecord:
    turn: int
    player_index: int
    guess: str
    result: GuessResult


def score_guess(secret: object, guess: object) -> GuessResult:
    """Compare a guess with the secret using the classic A/B definition."""

    normalized_secret = validate_number(secret)
    normalized_guess = validate_number(guess)
    bulls = sum(
        secret_digit == guess_digit
        for secret_digit, guess_digit in zip(normalized_secret, normalized_guess)
    )
    common = len(set(normalized_secret).intersection(normalized_guess))
    return GuessResult(bulls=bulls, cows=common - bulls)


class SinglePlayerGame:
    """Computer-picked secret that one local player tries to solve."""

    def __init__(
        self,
        secret: str | None = None,
        *,
        random_source: random.Random | None = None,
    ):
        self.secret = validate_number(secret) if secret is not None else generate_secret(random_source)
        self.history: list[GuessRecord] = []
        self.finished = False

    def submit_guess(self, guess: object) -> GuessRecord:
        if self.finished:
            raise BullsAndCowsError("本局已結束，請開始新遊戲。")
        normalized_guess = validate_number(guess)
        result = score_guess(self.secret, normalized_guess)
        record = GuessRecord(
            turn=len(self.history) + 1,
            player_index=0,
            guess=normalized_guess,
            result=result,
        )
        self.history.append(record)
        self.finished = result.won
        return record


class LocalTwoPlayerGame:
    """Two people set secrets and alternate guesses on the same computer."""

    def __init__(self, player_names: Iterable[str] = ("玩家 1", "玩家 2")):
        names = tuple(str(name).strip() or f"玩家 {index + 1}" for index, name in enumerate(player_names))
        if len(names) != 2:
            raise BullsAndCowsError("雙人模式需要兩位玩家。")
        self.player_names = names
        self.secrets: list[str | None] = [None, None]
        self.history: list[GuessRecord] = []
        self.active_player = 0
        self.winner_index: int | None = None

    @property
    def ready(self) -> bool:
        return all(self.secrets)

    @property
    def finished(self) -> bool:
        return self.winner_index is not None

    @property
    def setup_player_index(self) -> int | None:
        for index, secret in enumerate(self.secrets):
            if secret is None:
                return index
        return None

    def set_secret(self, player_index: int, secret: object) -> str:
        if player_index not in (0, 1):
            raise BullsAndCowsError("找不到指定玩家。")
        if self.secrets[player_index] is not None:
            raise BullsAndCowsError("這位玩家已設定秘密數字。")
        normalized = validate_number(secret)
        self.secrets[player_index] = normalized
        return normalized

    def submit_guess(self, guess: object) -> GuessRecord:
        if not self.ready:
            raise BullsAndCowsError("請先讓兩位玩家設定秘密數字。")
        if self.finished:
            raise BullsAndCowsError("本局已結束，請開始新遊戲。")
        normalized_guess = validate_number(guess)
        opponent_index = 1 - self.active_player
        opponent_secret = self.secrets[opponent_index]
        assert opponent_secret is not None
        result = score_guess(opponent_secret, normalized_guess)
        record = GuessRecord(
            turn=len(self.history) + 1,
            player_index=self.active_player,
            guess=normalized_guess,
            result=result,
        )
        self.history.append(record)
        if result.won:
            self.winner_index = self.active_player
        else:
            self.active_player = opponent_index
        return record
