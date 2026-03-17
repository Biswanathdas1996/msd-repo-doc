from backend.models.schemas import KnowledgeGraph
import json


def create_chunks(graph: KnowledgeGraph) -> list[dict]:
    chunks = []

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

    rel_chunk = {
        "type": "relationships",
        "relationships": [
            {"source": r.source, "target": r.target, "type": r.type}
            for r in graph.relationships
        ]
    }
    chunks.append(rel_chunk)

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


def chunks_to_context(chunks: list[dict], max_chars: int = 50000) -> str:
    context_parts = []
    total_chars = 0

    for chunk in chunks:
        chunk_str = json.dumps(chunk, indent=2)
        if total_chars + len(chunk_str) > max_chars:
            break
        context_parts.append(chunk_str)
        total_chars += len(chunk_str)

    return "\n\n".join(context_parts)
