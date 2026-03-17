from backend.models.schemas import KnowledgeGraph, FunctionalFlow


def generate_functional_flows(graph: KnowledgeGraph) -> list[FunctionalFlow]:
    flows = []

    for entity_name, entity_data in graph.entities.items():
        for wf_name in entity_data.workflows:
            wf_data = graph.workflows.get(wf_name)
            if not wf_data:
                continue

            plugins = wf_data.plugins[:]
            steps = []

            trigger_desc = wf_data.trigger or "update"
            steps.append(f"User performs {trigger_desc} on {entity_name}")
            steps.append(f"{wf_name} triggered")

            for step in wf_data.steps:
                steps.append(step)

            for plugin_name in plugins:
                plugin_data = graph.plugins.get(plugin_name)
                if plugin_data:
                    op = plugin_data.operation or "processing"
                    steps.append(f"{plugin_name} executed ({op})")

            description = (
                f"When {entity_name} is modified, "
                f"{wf_name} is triggered"
            )
            if plugins:
                description += f", invoking {', '.join(plugins)}"

            flows.append(FunctionalFlow(
                entity=entity_name,
                workflow=wf_name,
                plugins=plugins,
                steps=steps,
                description=description
            ))

    for wf_name, wf_data in graph.workflows.items():
        already_mapped = any(f.workflow == wf_name for f in flows)
        if not already_mapped:
            entity = wf_data.triggerEntity or "System"
            steps = [f"{wf_name} triggered"]
            steps.extend(wf_data.steps)

            for plugin_name in wf_data.plugins:
                plugin_data = graph.plugins.get(plugin_name)
                if plugin_data:
                    op = plugin_data.operation or "processing"
                    steps.append(f"{plugin_name} executed ({op})")

            flows.append(FunctionalFlow(
                entity=entity,
                workflow=wf_name,
                plugins=wf_data.plugins,
                steps=steps,
                description=f"Workflow {wf_name} processes {entity} operations"
            ))

    return flows
