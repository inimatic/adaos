from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstallPreset:
    name: str
    scenarios: tuple[str, ...]
    skills: tuple[str, ...]
    projects: tuple[str, ...] = ()
    applications: tuple[str, ...] = ()


DEFAULT_PRESET = InstallPreset(
    name="default",
    projects=(
        "web_desktop",
        "applications",
        "users_access",
        "subscription_status",
        "notebook",
        "nlu_teacher",
        "weather",
    ),
    applications=(
        "web_desktop",
        "applications",
        "users_access",
        "voice",
        "subscription_status",
        "notebook",
        "nlu_teacher",
        "weather",
    ),
    scenarios=(
        "web_desktop",
        "applications",
        "users_access",
    ),
    skills=(
        "web_desktop_runtime_skill",
        "web_desktop_skill",
        "greet_on_boot_skill",
        "pair_new_device_skill",
        "browsers_skill",
        "voice_chat_skill",
    ),
)


def get_preset(name: str | None) -> InstallPreset:
    normalized = (name or "default").strip().lower()
    if normalized in {"default", "base"}:
        return DEFAULT_PRESET
    raise ValueError(f"unknown preset: {name}")
