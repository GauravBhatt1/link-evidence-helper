from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compose.nonproduction.yaml"
DOCKERFILE = ROOT / "deploy" / "Dockerfile.api"
GITIGNORE = ROOT / "deploy" / "secrets" / ".gitignore"


def require(text: str, fragment: str, source: Path) -> None:
    if fragment not in text:
        raise SystemExit(f"{source}: missing required fragment: {fragment!r}")


def reject(text: str, fragment: str, source: Path) -> None:
    if fragment in text:
        raise SystemExit(f"{source}: forbidden fragment present: {fragment!r}")


def main() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    secret_ignore = GITIGNORE.read_text(encoding="utf-8")

    require(compose, '"127.0.0.1:8780:8780"', COMPOSE)
    reject(compose, "8765", COMPOSE)
    require(compose, "read_only: true", COMPOSE)
    require(compose, 'cap_drop: ["ALL"]', COMPOSE)
    require(compose, "no-new-privileges:true", COMPOSE)
    require(compose, "internal: true", COMPOSE)
    require(compose, 'profiles: ["postgres"]', COMPOSE)
    require(compose, "POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password", COMPOSE)
    reject(compose, "POSTGRES_PASSWORD:", COMPOSE)
    require(compose, "LINK_EVIDENCE_SOURCE_ADMIN_MODE: disabled", COMPOSE)

    require(dockerfile, "FROM gcr.io/distroless/static-debian12:nonroot", DOCKERFILE)
    require(dockerfile, "USER nonroot:nonroot", DOCKERFILE)
    reject(dockerfile, "EXPOSE 8765", DOCKERFILE)
    require(dockerfile, "EXPOSE 8780", DOCKERFILE)

    if secret_ignore.strip() != "*\n!.gitignore":
        raise SystemExit(f"{GITIGNORE}: runtime secret directory must ignore every file")


if __name__ == "__main__":
    main()
