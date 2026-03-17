from backend.models.schemas import (
    Entity, Workflow, Plugin, KnowledgeGraph,
    KnowledgeGraphEntity, KnowledgeGraphWorkflow,
    KnowledgeGraphPlugin, Relationship
)


def build_knowledge_graph(
    entities: list[Entity],
    workflows: list[Workflow],
    plugins: list[Plugin],
    forms: list[str]
) -> KnowledgeGraph:
    kg_entities: dict[str, KnowledgeGraphEntity] = {}
    kg_workflows: dict[str, KnowledgeGraphWorkflow] = {}
    kg_plugins: dict[str, KnowledgeGraphPlugin] = {}
    relationships: list[Relationship] = []

    entity_names = {e.name.lower(): e.name for e in entities}

    for entity in entities:
        kg_entities[entity.name] = KnowledgeGraphEntity(
            fields=[f.name for f in entity.fields],
            forms=entity.forms[:],
            workflows=[],
            plugins=[]
        )

    for wf in workflows:
        kg_workflows[wf.name] = KnowledgeGraphWorkflow(
            trigger=wf.trigger,
            triggerEntity=wf.triggerEntity,
            steps=wf.steps,
            plugins=wf.plugins[:],
            relatedEntities=wf.relatedEntities[:]
        )

        linked_entities: set[str] = set()

        if wf.triggerEntity and wf.triggerEntity.lower() in entity_names:
            resolved_name = entity_names[wf.triggerEntity.lower()]
            if resolved_name in kg_entities and wf.name not in kg_entities[resolved_name].workflows:
                kg_entities[resolved_name].workflows.append(wf.name)
            relationships.append(Relationship(
                source=resolved_name,
                target=wf.name,
                type="triggers"
            ))
            linked_entities.add(resolved_name)

        for rel_entity in (wf.relatedEntities or []):
            if not rel_entity:
                continue
            rel_lower = rel_entity.lower()
            if rel_lower not in entity_names:
                continue
            resolved_name = entity_names[rel_lower]
            if resolved_name in linked_entities:
                continue
            linked_entities.add(resolved_name)
            if resolved_name in kg_entities and wf.name not in kg_entities[resolved_name].workflows:
                kg_entities[resolved_name].workflows.append(wf.name)
            relationships.append(Relationship(
                source=resolved_name,
                target=wf.name,
                type="used_in"
            ))

    for plugin in plugins:
        kg_plugins[plugin.name] = KnowledgeGraphPlugin(
            triggerEntity=plugin.triggerEntity,
            operation=plugin.operation,
            stage=plugin.stage
        )

        if plugin.triggerEntity and plugin.triggerEntity.lower() in entity_names:
            resolved_name = entity_names[plugin.triggerEntity.lower()]
            if resolved_name in kg_entities:
                kg_entities[resolved_name].plugins.append(plugin.name)
            relationships.append(Relationship(
                source=resolved_name,
                target=plugin.name,
                type="uses_plugin"
            ))

    for wf in workflows:
        for plugin in plugins:
            if (plugin.triggerEntity and wf.triggerEntity and
                plugin.triggerEntity.lower() == wf.triggerEntity.lower()):
                if plugin.name not in kg_workflows[wf.name].plugins:
                    kg_workflows[wf.name].plugins.append(plugin.name)
                relationships.append(Relationship(
                    source=wf.name,
                    target=plugin.name,
                    type="invokes"
                ))

    for form_name in forms:
        for ename, kg_ent in kg_entities.items():
            if ename.lower() in form_name.lower() or form_name.lower() in ename.lower():
                if form_name not in kg_ent.forms:
                    kg_ent.forms.append(form_name)
                relationships.append(Relationship(
                    source=ename,
                    target=form_name,
                    type="has_form"
                ))

    return KnowledgeGraph(
        entities=kg_entities,
        workflows=kg_workflows,
        plugins=kg_plugins,
        relationships=relationships
    )
