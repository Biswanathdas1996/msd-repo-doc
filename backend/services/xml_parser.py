from lxml import etree
import os
from backend.models.schemas import Entity, EntityField, Workflow, Plugin


def _secure_parser():
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
        huge_tree=False,
    )


def _safe_parse(file_path: str):
    return etree.parse(file_path, _secure_parser())


def parse_solution_xml(file_path: str) -> dict:
    if not file_path or not os.path.exists(file_path):
        return {}

    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        info = {}
        version_el = root.find(f".//{ns}Version") if ns else root.find(".//Version")
        if version_el is not None and version_el.text:
            info["solutionVersion"] = version_el.text

        publisher_el = root.find(f".//{ns}UniqueName") if ns else root.find(".//UniqueName")
        if publisher_el is not None and publisher_el.text:
            info["publisher"] = publisher_el.text

        desc_el = root.find(f".//{ns}Descriptions//{ns}Description") if ns else root.find(".//Descriptions//Description")
        if desc_el is not None:
            info["description"] = desc_el.get("description", desc_el.text or "")

        return info
    except Exception:
        return {}


def parse_entity_file(file_path: str) -> list[Entity]:
    entities = []
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        entity_elements = _find_all(root, ns, ["Entity", "entity"])
        if not entity_elements:
            entity_elements = [root]

        for entity_el in entity_elements:
            name = (entity_el.get("Name") or entity_el.get("name") or
                    _get_child_text(entity_el, ns, "Name") or
                    os.path.splitext(os.path.basename(file_path))[0])

            display_name = (entity_el.get("DisplayName") or
                           _get_child_text(entity_el, ns, "DisplayName"))

            fields = []
            attr_elements = _find_all(entity_el, ns,
                                       ["attribute", "Attribute", "attributes/attribute", "Attributes/Attribute"])
            for attr in attr_elements:
                field_name = attr.get("PhysicalName") or attr.get("Name") or attr.get("name") or ""
                field_type = (attr.get("Type") or attr.get("type") or
                             _get_child_text(attr, ns, "Type") or "string")
                field_display = attr.get("DisplayName") or _get_child_text(attr, ns, "DisplayName")
                is_required = attr.get("Required") == "true" or attr.get("required") == "true"

                if field_name:
                    fields.append(EntityField(
                        name=field_name,
                        type=field_type,
                        displayName=field_display,
                        required=is_required
                    ))

            if not fields:
                for child in entity_el:
                    tag = _clean_tag(child.tag)
                    if tag.lower() not in ["name", "displayname", "description", "entityinfo"]:
                        fields.append(EntityField(
                            name=tag,
                            type="element",
                            displayName=tag
                        ))

            entities.append(Entity(
                name=name,
                displayName=display_name,
                fields=fields
            ))
    except Exception:
        name = os.path.splitext(os.path.basename(file_path))[0]
        entities.append(Entity(name=name, fields=[]))

    return entities


def parse_workflow_file(file_path: str) -> list[Workflow]:
    workflows = []
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        wf_elements = _find_all(root, ns, ["Workflow", "workflow", "Process", "process"])
        if not wf_elements:
            wf_elements = [root]

        for wf_el in wf_elements:
            name = (wf_el.get("Name") or wf_el.get("name") or
                    _get_child_text(wf_el, ns, "Name") or
                    os.path.splitext(os.path.basename(file_path))[0])

            trigger_entity = (wf_el.get("PrimaryEntity") or wf_el.get("primaryentity") or
                            _get_child_text(wf_el, ns, "PrimaryEntity"))

            trigger = wf_el.get("Trigger") or _get_child_text(wf_el, ns, "Trigger")

            steps = []
            step_elements = _find_all(wf_el, ns, ["Step", "step", "Activity", "activity"])
            for step in step_elements:
                step_name = (step.get("Name") or step.get("name") or
                           _get_child_text(step, ns, "Name") or step.text or "")
                if step_name.strip():
                    steps.append(step_name.strip())

            if not steps:
                steps = [f"Process defined in {os.path.basename(file_path)}"]

            conditions = []
            cond_elements = _find_all(wf_el, ns, ["Condition", "condition"])
            for cond in cond_elements:
                cond_text = cond.get("Name") or cond.text or ""
                if cond_text.strip():
                    conditions.append(cond_text.strip())

            workflows.append(Workflow(
                name=name,
                triggerEntity=trigger_entity,
                trigger=trigger,
                steps=steps,
                conditions=conditions
            ))
    except Exception:
        name = os.path.splitext(os.path.basename(file_path))[0]
        workflows.append(Workflow(name=name, steps=["Workflow processing"]))

    return workflows


def parse_plugin_file(file_path: str) -> list[Plugin]:
    plugins = []
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        plugin_elements = _find_all(root, ns, [
            "Plugin", "plugin", "PluginType", "plugintype",
            "SdkMessageProcessingStep", "Step"
        ])
        if not plugin_elements:
            plugin_elements = [root]

        for p_el in plugin_elements:
            name = (p_el.get("Name") or p_el.get("name") or p_el.get("TypeName") or
                    _get_child_text(p_el, ns, "Name") or
                    _get_child_text(p_el, ns, "TypeName") or
                    os.path.splitext(os.path.basename(file_path))[0])

            trigger_entity = (p_el.get("PrimaryEntity") or
                            _get_child_text(p_el, ns, "PrimaryEntity") or
                            _get_child_text(p_el, ns, "PrimaryObjectTypeCode"))

            operation = (p_el.get("Message") or p_el.get("SdkMessageId") or
                       _get_child_text(p_el, ns, "Message") or
                       _get_child_text(p_el, ns, "SdkMessageId"))

            stage = (p_el.get("Stage") or _get_child_text(p_el, ns, "Stage"))

            desc = (p_el.get("Description") or _get_child_text(p_el, ns, "Description"))

            plugins.append(Plugin(
                name=name,
                triggerEntity=trigger_entity,
                operation=operation,
                stage=stage,
                description=desc
            ))
    except Exception:
        name = os.path.splitext(os.path.basename(file_path))[0]
        plugins.append(Plugin(name=name))

    return plugins


def parse_form_files(file_paths: list[str]) -> list[str]:
    forms = []
    for fp in file_paths:
        try:
            tree = _safe_parse(fp)
            root = tree.getroot()
            ns = _get_namespace(root)
            form_elements = _find_all(root, ns, ["form", "Form", "systemform", "SystemForm"])
            for fe in form_elements:
                name = fe.get("Name") or fe.get("name") or _get_child_text(fe, ns, "Name")
                if name:
                    forms.append(name)
            if not form_elements:
                forms.append(os.path.splitext(os.path.basename(fp))[0])
        except Exception:
            forms.append(os.path.splitext(os.path.basename(fp))[0])
    return forms


def _get_namespace(root) -> str:
    tag = root.tag
    if tag.startswith("{"):
        return tag.split("}")[0] + "}"
    return ""


def _clean_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}")[1]
    return tag


def _find_all(element, ns: str, tag_names: list[str]) -> list:
    seen_ids = set()
    results = []

    for tag in tag_names:
        found = []
        if ns:
            found = element.findall(f".//{ns}{tag}")
        if not found:
            found = element.findall(f".//{tag}")
        if not found:
            for child in element.iter():
                clean = _clean_tag(child.tag)
                if clean == tag or clean.lower() == tag.lower():
                    found.append(child)

        for item in found:
            item_id = id(item)
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                results.append(item)

    return results


def _get_child_text(element, ns: str, tag_name: str) -> str | None:
    if ns:
        child = element.find(f"{ns}{tag_name}")
    else:
        child = element.find(tag_name)

    if child is not None:
        return child.text

    for ch in element:
        if _clean_tag(ch.tag) == tag_name:
            return ch.text
    return None
