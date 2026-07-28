"""Validated JSON adapter persistence."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from adapter_models import validate_adapter

class AdapterStorage:
    def __init__(self, directory: str | Path): self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)

    def inventory(self) -> dict[str, Any]:
        """Read adapter files from disk, retaining invalid-file visibility."""
        adapters: list[dict[str, Any]] = []
        invalid: list[dict[str, str]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                adapter = validate_adapter(json.loads(path.read_text(encoding="utf-8")))
                adapters.append({
                    "adapter": adapter,
                    "file_name": path.name,
                    "storage_location": str(Path("adapters") / path.name),
                })
            except Exception as exc:
                invalid.append({
                    "file_name": path.name,
                    "storage_location": str(Path("adapters") / path.name),
                    "error": str(exc)[:180],
                })
        return {"adapters": adapters, "invalid_files": invalid, "storage_directory": str(self.directory.resolve())}

    def list(self) -> list[dict[str, Any]]:
        return [item["adapter"] for item in self.inventory()["adapters"]]
    def save(self, adapter: dict[str, Any]) -> dict[str, Any]:
        adapter=validate_adapter(adapter); path=self.directory / f"{adapter['id']}.json"
        if path.exists(): raise ValueError("Adapter ID already exists")
        path.write_text(json.dumps(adapter, indent=2, sort_keys=True)+"\n"); return adapter
    def replace(self, adapter: dict[str, Any]) -> dict[str, Any]:
        adapter=validate_adapter(adapter); (self.directory / f"{adapter['id']}.json").write_text(json.dumps(adapter, indent=2, sort_keys=True)+"\n"); return adapter
    def get(self, adapter_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list() if item["id"] == adapter_id), None)
    def delete(self, adapter_id: str) -> None:
        path=self.directory / f"{adapter_id}.json"
        if not path.exists(): raise ValueError("Adapter not found")
        path.unlink()
