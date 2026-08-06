#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

COMPOSE = Path("deploy/nonprod/compose.yaml")
DOCKERFILE = Path("deploy/nonprod/Dockerfile.api")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"missing required {label}: {needle}")


def forbid(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise SystemExit(f"forbidden {label}: {needle}")


def main() -> int:
    compose = COMPOSE.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    require(compose, "internal: true", "internal-only network")
    require(compose, 'cap_drop: ["ALL"]', "capability drop")
    require(compose, 'security_opt: ["no-new-privileges:true"]', "privilege escalation guard")
    require(compose, "read_only: true", "read-only filesystem")
    require(compose, "POSTGRES_PASSWORD_FILE", "file-backed PostgreSQL secret")
    require(compose, "${POSTGRES_PASSWORD_FILE:?", "fail-closed external secret path")
    require(compose, 'profiles: ["postgres"]', "disabled-by-default PostgreSQL profile")
    forbid(compose, "8765", "production listener reference")
    forbid(compose, "ports:", "host-published ports")
    forbid(compose.lower(), "password:", "inline password")

    require(dockerfile, "FROM gcr.io/distroless/static-debian12:nonroot", "non-root runtime image")
    require(dockerfile, "USER 65532:65532", "explicit runtime uid")
    forbid(dockerfile, "latest", "floating image tag")

    print("non-production container topology safety checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
