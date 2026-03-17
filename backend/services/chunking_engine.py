from backend.models.schemas import KnowledgeGraph
import json
import math


def create_chunks(graph: KnowledgeGraph, section_key: str | None = None) -> list[dict]:
    chunks = []

    # Determine which chunk types are most relevant for each section
    _SECTION_CHUNK_PRIORITY: dict[str, list[str]] = {
        "executive_summary": ["functional_module", "relationships", "entity_detail", "role_detail"],
        "business_requirements": ["functional_module", "workflow_detail", "entity_detail", "role_detail"],
        "functional_design": ["functional_module", "entity_detail", "workflow_detail", "relationships"],
        "technical_design": ["plugin_detail", "workflow_detail", "webresource_detail", "entity_detail", "relationships"],
        "data_model": ["entity_detail", "relationships"],
        "integration": ["plugin_detail", "webresource_detail", "workflow_detail"],
        "customization": ["plugin_detail", "workflow_detail", "webresource_detail", "entity_detail"],
        "security_model": ["role_detail", "entity_detail"],
        "deployment": ["functional_module", "entity_detail"],
        "testing": ["workflow_detail", "plugin_detail", "entity_detail"],
        "support_operations": ["plugin_detail", "workflow_detail", "webresource_detail"],
        "user_guide": ["entity_detail", "workflow_detail", "functional_module"],
        "solution_inventory": ["entity_detail", "plugin_detail", "workflow_detail", "role_detail", "webresource_detail"],
        "environment_config": ["functional_module"],
        "change_log": ["functional_module"],
    }

    entity_workflow_map: dict[str, set] = {}
    for ename, edata in graph.entities.items():
        entity_workflow_map[ename] = set(edata.workflows)

    modules = _identify_modules(graph)
    for module in modules:
        chunk = {
            "module": module["name"],
            "entities": module["entities"],
            "workflows": module["workflows"],
            "plugins": module["plugins"],
            "type": "functional_module"
        }
        chunks.append(chunk)

    for ename, edata in graph.entities.items():
        chunk = {
            "type": "entity_detail",
            "entity": ename,
            "fields": edata.fields,
            "forms": edata.forms,
            "related_workflows": edata.workflows,
            "related_plugins": edata.plugins
        }
        chunks.append(chunk)

    for wname, wdata in graph.workflows.items():
        chunk = {
            "type": "workflow_detail",
            "workflow": wname,
            "trigger": wdata.trigger,
            "trigger_entity": wdata.triggerEntity,
            "steps": wdata.steps,
            "plugins": wdata.plugins
        }
        chunks.append(chunk)

    for pname, pdata in graph.plugins.items():
        chunk = {
            "type": "plugin_detail",
            "plugin": pname,
            "trigger_entity": pdata.triggerEntity,
            "operation": pdata.operation,
            "stage": pdata.stage
        }
        chunks.append(chunk)

    # --- Roles / Security ---
    for rname, rdata in graph.roles.items():
        chunk = {
            "type": "role_detail",
            "role": rname,
            "privileges": rdata.privileges,
            "related_entities": rdata.relatedEntities,
            "description": rdata.description
        }
        chunks.append(chunk)

    # --- Web Resources ---
    for wrname, wrdata in graph.webResources.items():
        chunk = {
            "type": "webresource_detail",
            "webresource": wrname,
            "resource_type": wrdata.type,
            "related_entity": wrdata.relatedEntity,
            "description": wrdata.description
        }
        chunks.append(chunk)

    rel_chunk = {
        "type": "relationships",
        "relationships": [
            {"source": r.source, "target": r.target, "type": r.type}
            for r in graph.relationships
        ]
    }
    chunks.append(rel_chunk)

    # --- Section-aware filtering: prioritize relevant chunks ---
    if section_key and section_key in _SECTION_CHUNK_PRIORITY:
        priority_types = _SECTION_CHUNK_PRIORITY[section_key]
        # Always include relationships for context
        priority_types_set = set(priority_types) | {"relationships", "functional_module"}

        # Split chunks into primary (relevant) and secondary (context)
        primary = [c for c in chunks if c.get("type") in priority_types_set]
        secondary = [c for c in chunks if c.get("type") not in priority_types_set]

        # Prioritize primary chunks, append secondary for broader context
        chunks = primary + secondary

    return chunks


def _identify_modules(graph: KnowledgeGraph) -> list[dict]:
    modules = []
    processed_entities: set[str] = set()

    for ename, edata in graph.entities.items():
        if ename in processed_entities:
            continue

        module_entities = {ename}
        module_workflows = set(edata.workflows)
        module_plugins = set(edata.plugins)

        for wname in edata.workflows:
            wdata = graph.workflows.get(wname)
            if wdata:
                module_plugins.update(wdata.plugins)

        for other_name, other_data in graph.entities.items():
            if other_name == ename:
                continue
            shared_wf = set(other_data.workflows) & module_workflows
            shared_plugins = set(other_data.plugins) & module_plugins
            if shared_wf or shared_plugins:
                module_entities.add(other_name)
                module_workflows.update(other_data.workflows)
                module_plugins.update(other_data.plugins)

        processed_entities.update(module_entities)

        module_name = f"{list(module_entities)[0]} Management"
        if len(module_entities) > 1:
            module_name = f"{' & '.join(sorted(module_entities))} Module"

        modules.append({
            "name": module_name,
            "entities": sorted(module_entities),
            "workflows": sorted(module_workflows),
            "plugins": sorted(module_plugins)
        })

    return modules


def chunks_to_context(chunks: list[dict], max_chars: int = 0) -> str:
    """Serialize all chunks into a single context string. No truncation."""
    context_parts = [json.dumps(chunk, indent=2) for chunk in chunks]
    return "\n\n".join(context_parts)


# ---------------------------------------------------------------------------
# Priority order:  higher-priority chunk types come first so that when
# batches are built the most important context is always present.
# ---------------------------------------------------------------------------
_CHUNK_TYPE_PRIORITY = {
    "functional_module": 0,
    "relationships": 1,
    "entity_detail": 2,
    "workflow_detail": 3,
    "plugin_detail": 4,
    "role_detail": 5,
    "webresource_detail": 6,
}


def group_chunks_into_batches(
    chunks: list[dict],
    batch_char_limit: int = 60000,
) -> list[list[dict]]:
    """Split chunks into batches that each fit within *batch_char_limit*.

    Semantically related chunks (same type) are kept together when possible.
    Every batch also receives a slim "solution_summary" header so the AI has
    global awareness even when processing a partial batch.
    """
    if not chunks:
        return []

    # Sort by priority so modules/relationships are packed first
    sorted_chunks = sorted(
        chunks,
        key=lambda c: _CHUNK_TYPE_PRIORITY.get(c.get("type", ""), 99),
    )

    total_text = chunks_to_context(sorted_chunks)
    if len(total_text) <= batch_char_limit:
        # Everything fits in one batch — no splitting needed
        return [sorted_chunks]

    # Build a compact summary that is prepended to every batch
    summary = _build_solution_summary(chunks)
    summary_str = json.dumps(summary, indent=2)
    available = batch_char_limit - len(summary_str) - 100  # 100 chars margin
    if available < 5000:
        available = 5000  # absolute minimum per batch

    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    current_size = 0

    for chunk in sorted_chunks:
        chunk_str = json.dumps(chunk, indent=2)
        chunk_len = len(chunk_str)

        if current_size + chunk_len > available and current_batch:
            # Finalize current batch — prepend summary
            current_batch.insert(0, summary)
            batches.append(current_batch)
            current_batch = []
            current_size = 0

        current_batch.append(chunk)
        current_size += chunk_len

    # Last batch
    if current_batch:
        current_batch.insert(0, summary)
        batches.append(current_batch)

    return batches


def _build_solution_summary(chunks: list[dict]) -> dict:
    """Create a lightweight summary dict from all chunks for global context."""
    entity_names = []
    workflow_names = []
    plugin_names = []
    role_names = []
    wr_names = []

    for c in chunks:
        ctype = c.get("type", "")
        if ctype == "entity_detail":
            entity_names.append(c.get("entity", ""))
        elif ctype == "workflow_detail":
            workflow_names.append(c.get("workflow", ""))
        elif ctype == "plugin_detail":
            plugin_names.append(c.get("plugin", ""))
        elif ctype == "role_detail":
            role_names.append(c.get("role", ""))
        elif ctype == "webresource_detail":
            wr_names.append(c.get("webresource", ""))

    return {
        "type": "solution_summary",
        "total_entities": len(entity_names),
        "total_workflows": len(workflow_names),
        "total_plugins": len(plugin_names),
        "total_roles": len(role_names),
        "total_webresources": len(wr_names),
        "entity_names": entity_names,
        "workflow_names": workflow_names,
        "plugin_names": plugin_names,
        "role_names": role_names,
        "webresource_names": wr_names,
        "note": "This batch is one part of a larger solution. Other batches cover the remaining components.",
    }
