"""NATS-compatible subject validation and matching."""

from __future__ import annotations


class InvalidSubjectError(ValueError):
    """Raised when a subscription subject is malformed."""


def validate_subscription_subject(subject: str) -> None:
    tokens = _tokens(subject)
    if ">" in tokens[:-1]:
        raise InvalidSubjectError("the > wildcard is only valid as the last token")
    if any("*" in component and component != "*" for component in tokens):
        raise InvalidSubjectError("the * wildcard must occupy a complete token")
    if any(">" in component and component != ">" for component in tokens):
        raise InvalidSubjectError("the > wildcard must occupy a complete token")


def subject_matches(pattern: str, subject: str) -> bool:
    pattern_tokens = pattern.split(".")
    subject_tokens = subject.split(".")
    for index, pattern_component in enumerate(pattern_tokens):
        if pattern_component == ">":
            return index < len(subject_tokens)
        if index >= len(subject_tokens):
            return False
        if pattern_component != "*" and pattern_component != subject_tokens[index]:
            return False
    return len(pattern_tokens) == len(subject_tokens)


def _tokens(subject: str) -> list[str]:
    if not subject or subject != subject.strip():
        raise InvalidSubjectError("subject cannot be blank or padded")
    tokens = subject.split(".")
    if any(not token or any(character.isspace() for character in token) for token in tokens):
        raise InvalidSubjectError("subject tokens must be non-empty and contain no whitespace")
    return tokens
