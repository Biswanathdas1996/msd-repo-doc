"""
Build Entity / Plugin / Workflow models from arbitrary source repositories so the
same knowledge-graph → chunking → PwC Gen AI documentation pipeline can run
without Microsoft Dynamics solution XML.
"""

from __future__ import annotations

import os
import re

from backend.models.schemas import Entity, EntityField, Plugin, Workflow
from backend.services.extractor import JUNK_DIR_NAMES, JUNK_FILE_NAMES, JUNK_EXTENSIONS

GENERIC_CODE_EXTENSIONS = frozenset({
    ".py", ".pyw", ".pyi",
    ".js", ".mjs", ".cjs", ".jsx",
    ".ts", ".tsx", ".mts", ".cts",
    ".java", ".kt", ".kts",
    ".go", ".rs", ".rb", ".php",
    ".cs", ".vb", ".fs", ".fsx",
    ".scala", ".swift", ".dart",
    ".sql", ".vue", ".svelte", ".r", ".ex", ".exs",
    ".cpp", ".cc", ".cxx", ".h", ".hpp", ".c",
})

MAX_GENERIC_FILES = 450
MAX_FILE_READ = 48 * 1024
MAX_PREVIEW_CHARS = 900


def _module_entity_name(parent_rel: str) -> str:
    if not parent_rel or parent_rel == ".":
        return "ApplicationRoot"
    safe = parent_rel.replace("\\", "/").strip("/")
    parts = [p for p in safe.split("/") if p]
    if not parts:
        return "ApplicationRoot"
    return ".".join(parts)


def _read_preview(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read(MAX_FILE_READ)
    except OSError:
        return ""
    raw = raw.strip()
    raw = re.sub(r"\s+", " ", raw)
    if len(raw) > MAX_PREVIEW_CHARS:
        return raw[: MAX_PREVIEW_CHARS - 20] + " …(truncated)"
    return raw


def parse_generic_project(folder: str) -> dict:
    """
    Walk *folder* for source files, group by parent directory into synthetic
    entities, emit one plugin per file with a text preview for AI context.
    """
    collected: list[tuple[str, str]] = []  # (rel_posix, full_path)

    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in JUNK_DIR_NAMES]
        for f in files:
            fl = f.lower()
            if fl in JUNK_FILE_NAMES:
                continue
            _base, ext = os.path.splitext(fl)
            if ext not in GENERIC_CODE_EXTENSIONS:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, folder).replace("\\", "/")
            collected.append((rel, full))

    collected.sort(key=lambda x: x[0])
    lang_counts: dict[str, int] = {}
    for rel, full in collected:
        _b, ext = os.path.splitext(rel.lower())
        lang_counts[ext] = lang_counts.get(ext, 0) + 1

    if len(collected) > MAX_GENERIC_FILES:
        step = max(1, len(collected) // MAX_GENERIC_FILES)
        collected = collected[::step][:MAX_GENERIC_FILES]

    by_parent: dict[str, list[tuple[str, str]]] = {}
    for rel, full in collected:
        parent = os.path.dirname(rel) or "."
        by_parent.setdefault(parent, []).append((rel, full))

    entities: list[Entity] = []
    plugins: list[Plugin] = []
    ext_to_field_type = {e: e.lstrip(".") for e in GENERIC_CODE_EXTENSIONS}

    for parent, items in sorted(by_parent.items(), key=lambda x: x[0]):
        mod_name = _module_entity_name(parent)
        fields: list[EntityField] = []
        mod_plugins: list[str] = []

        for rel, full in sorted(items, key=lambda x: x[0]):
            base = os.path.basename(rel)
            _root, ext = os.path.splitext(base.lower())
            ft = ext_to_field_type.get(ext, "source")
            fields.append(
                EntityField(
                    name=base,
                    type=ft,
                    displayName=rel,
                    description="Source file in repository",
                )
            )
            preview = _read_preview(full)
            desc = f"Path: {rel}\n"
            if preview:
                desc += f"Preview: {preview}"

            pname = rel.replace("\\", "/")
            plugins.append(
                Plugin(
                    name=pname,
                    triggerEntity=mod_name,
                    operation="source_file",
                    stage="n/a",
                    description=desc,
                )
            )
            mod_plugins.append(pname)

        display = mod_name.replace(".", " / ") if mod_name != "ApplicationRoot" else "Repository root"
        entities.append(
            Entity(
                name=mod_name,
                displayName=display,
                fields=fields,
                plugins=mod_plugins,
            )
        )

    workflows: list[Workflow] = []
    if entities:
        workflows.append(
            Workflow(
                name="RepositoryStructure",
                triggerEntity=entities[0].name,
                trigger="manual",
                mode="n/a",
                steps=[
                    f"{len(entities)} module(s), {len(plugins)} source file(s) indexed for documentation.",
                ],
                relatedEntities=[e.name for e in entities[:50]],
            )
        )

    if not plugins:
        exts = ", ".join(sorted(GENERIC_CODE_EXTENSIONS)[:12]) + ", …"
        raise ValueError(
            "No supported source files found in the archive. "
            f"Include source files with extensions such as: {exts}"
        )

    metadata = {
        "type": "generic_code",
        "projectKind": "generic",
        "description": "Non–Microsoft Dynamics source repository (generic code index)",
        "generic_file_count": len(plugins),
        "generic_module_count": len(entities),
        "languages_by_extension": dict(sorted(lang_counts.items(), key=lambda x: -x[1])[:24]),
    }

    return {
        "entities": entities,
        "workflows": workflows,
        "plugins": plugins,
        "metadata": metadata,
    }
