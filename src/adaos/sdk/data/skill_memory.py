"""Compatibility wrapper for skill-local memory backed by the skill env store."""

from __future__ import annotations

from typing import Any

from .skill_env import async_get_env, async_set_env, get_env, set_env

__all__ = ["get", "set", "async_get", "async_set"]


def get(key: str, default: Any | None = None) -> Any:
    return get_env(key, default)


def set(key: str, value: Any) -> None:
    set_env(key, value)


async def async_get(key: str, default: Any | None = None) -> Any:
    return await async_get_env(key, default)


async def async_set(key: str, value: Any) -> None:
    await async_set_env(key, value)
