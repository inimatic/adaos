# CLI Reference

Status: compact public reference.

This page is intentionally compact. Exhaustive internal command contracts and
development-only references are not part of the public stable documentation.

## Core Groups

| Command group | Purpose |
| --- | --- |
| `adaos install` | Prepare local runtime dependencies. |
| `adaos update` | Update the local checkout/runtime when supported. |
| `adaos dev ...` | Run development workflows. |
| `adaos api ...` | Run lower-level API debugging flows. |
| `adaos hub ...` | Manage hub-side operations such as join codes. |
| `adaos node ...` | Inspect node status and reliability. |
| `adaos skill ...` | List or operate skills through supported flows. |
| `adaos scenario ...` | List or operate scenarios through supported flows. |
| `adaos applications ...` | Inspect Core lifecycle, review local actions and recover exact operations (beta). |
| `adaos autostart ...` | Manage production-style local runtime slots. |

Run `adaos --help` and `adaos <group> --help` for the current command surface.

`adaos dev contracts ...` provides local-beta
[contract compatibility checks](../operations/contract-evolution.md), including
contract comparison, consumer assessments, proposed Dev Tickets and evidence.

