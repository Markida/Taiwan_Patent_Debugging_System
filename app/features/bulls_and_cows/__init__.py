"""Offline Bulls and Cows (1A2B) game feature."""

from .engine import (
    BullsAndCowsError,
    GuessRecord,
    GuessResult,
    LocalTwoPlayerGame,
    SinglePlayerGame,
    generate_secret,
    score_guess,
    validate_number,
)

__all__ = [
    "BullsAndCowsError",
    "GuessRecord",
    "GuessResult",
    "LocalTwoPlayerGame",
    "SinglePlayerGame",
    "generate_secret",
    "score_guess",
    "validate_number",
]
