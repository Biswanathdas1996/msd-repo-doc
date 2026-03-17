from lxml import etree
import os
from backend.models.schemas import Entity, EntityField, Workflow, Plugin, Role, WebResource


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


def parse_ax_class_file(file_path: str) -> dict:
    result = {"name": "", "type": "class", "base_class": None, "methods": [], "references": [], "source_code": ""}
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        name_el = root.find(f"{ns}Name") if ns else root.find("Name")
        if name_el is not None and name_el.text:
            result["name"] = name_el.text
        else:
            result["name"] = os.path.splitext(os.path.basename(file_path))[0]

        declaration_el = root.find(f".//{ns}Declaration" if ns else ".//Declaration")
        if declaration_el is not None and declaration_el.text:
            decl = declaration_el.text
            result["source_code"] += decl

            import re
            ext_match = re.search(r'extends\s+(\w+)', decl)
            if ext_match:
                result["base_class"] = ext_match.group(1)

            impl_match = re.search(r'implements\s+([\w,\s]+)', decl)
            if impl_match:
                result["references"].append(f"implements: {impl_match.group(1).strip()}")

            ext_of_match = re.search(r'\[ExtensionOf\(\w+Str\((\w+)\)\)\]', decl)
            if ext_of_match:
                result["references"].append(f"extension_of: {ext_of_match.group(1)}")

        methods_container = root.find(f".//{ns}Methods" if ns else ".//Methods")
        if methods_container is not None:
            for method_el in methods_container:
                method_tag = method_el.tag
                if "}" in method_tag:
                    method_tag = method_tag.split("}")[1]
                if method_tag == "Method":
                    m_name_el = method_el.find(f"{ns}Name" if ns else "Name")
                    m_source_el = method_el.find(f"{ns}Source" if ns else "Source")
                    m_name = m_name_el.text if m_name_el is not None and m_name_el.text else "unknown"
                    m_source = m_source_el.text if m_source_el is not None and m_source_el.text else ""
                    result["methods"].append({"name": m_name, "source": m_source})
                    result["source_code"] += "\n" + m_source

            import re
            all_source = result["source_code"]
            table_refs = set()
            for pattern in [
                r'tableNum\((\w+)\)',
                r'new\s+(\w+)\(\)',
                r'\.find\w*\(',
                r'(\w+)::find',
            ]:
                for m in re.finditer(pattern, all_source):
                    if m.lastindex:
                        ref = m.group(1)
                        if ref[0].isupper() and len(ref) > 2 and ref not in {"RecordInsertList", "List", "Set", "Map", "Query", "QueryRun", "QueryBuildDataSource", "Args", "FormDataSource"}:
                            table_refs.add(ref)
            for ref in table_refs:
                if f"table_ref: {ref}" not in result["references"]:
                    result["references"].append(f"table_ref: {ref}")
    except Exception:
        result["name"] = os.path.splitext(os.path.basename(file_path))[0]

    return result


def parse_ax_table_file(file_path: str) -> Entity:
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        name_el = root.find(f"{ns}Name") if ns else root.find("Name")
        name = name_el.text if name_el is not None and name_el.text else os.path.splitext(os.path.basename(file_path))[0]

        root_tag = root.tag
        if "}" in root_tag:
            root_tag = root_tag.split("}")[1]
        is_extension = "Extension" in root_tag
        display_name = f"{name} (Extension)" if is_extension else name

        fields = []
        fields_container = root.find(f".//{ns}Fields" if ns else ".//Fields")
        if fields_container is not None:
            for field_el in fields_container:
                f_tag = field_el.tag
                if "}" in f_tag:
                    f_tag = f_tag.split("}")[1]
                f_name_el = field_el.find(f"{ns}Name" if ns else "Name")
                f_name = f_name_el.text if f_name_el is not None and f_name_el.text else ""

                f_type = field_el.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
                if not f_type:
                    f_type = f_tag.replace("AxTableField", "").replace("AxViewField", "") or "string"

                if f_name:
                    fields.append(EntityField(name=f_name, type=f_type, displayName=f_name))

        return Entity(name=name, displayName=display_name, fields=fields)
    except Exception:
        name = os.path.splitext(os.path.basename(file_path))[0]
        return Entity(name=name, fields=[])


def parse_ax_view_file(file_path: str) -> Entity:
    return parse_ax_table_file(file_path)


def ax_classes_to_plugins(ax_classes: list[dict]) -> list[Plugin]:
    plugins = []
    for cls in ax_classes:
        ext_of = None
        table_refs = []
        for ref in cls.get("references", []):
            if ref.startswith("extension_of:"):
                ext_of = ref.split(":", 1)[1].strip()
            elif ref.startswith("table_ref:"):
                table_refs.append(ref.split(":", 1)[1].strip())

        desc_parts = []
        if cls.get("base_class"):
            desc_parts.append(f"Extends: {cls['base_class']}")
        if ext_of:
            desc_parts.append(f"Extension of: {ext_of}")
        if table_refs:
            desc_parts.append(f"References: {', '.join(table_refs[:10])}")

        method_names = [m["name"] for m in cls.get("methods", [])]
        if method_names:
            desc_parts.append(f"Methods: {', '.join(method_names[:15])}")

        plugins.append(Plugin(
            name=cls["name"],
            triggerEntity=ext_of or (table_refs[0] if table_refs else None),
            operation="X++ Class",
            stage=cls.get("base_class"),
            description="; ".join(desc_parts) if desc_parts else None
        ))
    return plugins


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


# ---------------------------------------------------------------------------
# Role / Security Role parser
# ---------------------------------------------------------------------------

def parse_role_files(file_paths: list[str]) -> list[Role]:
    """Parse Dynamics security role / AX security XML files into Role objects."""
    roles: list[Role] = []
    for fp in file_paths:
        try:
            tree = _safe_parse(fp)
            root = tree.getroot()
            ns = _get_namespace(root)
            root_tag = _clean_tag(root.tag).lower()

            # AX SecurityRole / SecurityDuty / SecurityPrivilege
            if root_tag in ("axsecurityrole", "axsecurityduty", "axsecurityprivilege"):
                name_el = root.find(f"{ns}Name") if ns else root.find("Name")
                name = name_el.text if name_el is not None and name_el.text else os.path.splitext(os.path.basename(fp))[0]
                desc_el = root.find(f".//{ns}Description" if ns else ".//Description")
                desc = desc_el.text if desc_el is not None and desc_el.text else None

                privileges = []
                for priv_el in root.iter():
                    tag = _clean_tag(priv_el.tag).lower()
                    if "privilege" in tag or "duty" in tag or "permission" in tag:
                        p_name = priv_el.find(f"{ns}Name" if ns else "Name")
                        if p_name is not None and p_name.text:
                            privileges.append(p_name.text)
                        elif priv_el.get("Name"):
                            privileges.append(priv_el.get("Name"))

                roles.append(Role(name=name, privileges=privileges, description=desc))
                continue

            # CRM-style role XMLs
            role_elements = _find_all(root, ns, ["Role", "role", "SecurityRole"])
            if not role_elements:
                role_elements = [root]

            for role_el in role_elements:
                name = (role_el.get("Name") or role_el.get("name") or
                        _get_child_text(role_el, ns, "Name") or
                        os.path.splitext(os.path.basename(fp))[0])
                desc = role_el.get("Description") or _get_child_text(role_el, ns, "Description")
                privileges = []
                priv_elements = _find_all(role_el, ns, [
                    "RolePrivilege", "Privilege", "privilege",
                    "roleprivilege", "RolePrivileges"
                ])
                for pe in priv_elements:
                    pname = pe.get("name") or pe.get("Name") or _get_child_text(pe, ns, "Name") or ""
                    if pname:
                        privileges.append(pname)

                roles.append(Role(name=name, privileges=privileges, description=desc))

        except Exception:
            name = os.path.splitext(os.path.basename(fp))[0]
            roles.append(Role(name=name))
    return roles


# ---------------------------------------------------------------------------
# Web Resource parser
# ---------------------------------------------------------------------------

WEB_RESOURCE_TYPE_MAP = {
    "1": "HTML", "2": "CSS", "3": "JavaScript",
    "4": "XML", "5": "PNG", "6": "JPG",
    "7": "GIF", "8": "Silverlight", "9": "StyleSheet",
    "10": "ICO", "11": "SVG", "12": "RESX",
}


def parse_webresource_files(file_paths: list[str]) -> list[WebResource]:
    """Parse Dynamics web resource XML files."""
    resources: list[WebResource] = []
    for fp in file_paths:
        try:
            tree = _safe_parse(fp)
            root = tree.getroot()
            ns = _get_namespace(root)

            wr_elements = _find_all(root, ns, [
                "WebResource", "webresource", "WebResources"
            ])
            if not wr_elements:
                wr_elements = [root]

            for wr_el in wr_elements:
                name = (wr_el.get("Name") or wr_el.get("name") or
                        _get_child_text(wr_el, ns, "Name") or
                        os.path.splitext(os.path.basename(fp))[0])
                display = wr_el.get("DisplayName") or _get_child_text(wr_el, ns, "DisplayName")
                desc = wr_el.get("Description") or _get_child_text(wr_el, ns, "Description")
                wr_type_code = wr_el.get("WebResourceType") or _get_child_text(wr_el, ns, "WebResourceType") or ""
                wr_type = WEB_RESOURCE_TYPE_MAP.get(wr_type_code, wr_type_code or "unknown")

                # Try to infer type from file extension in name
                if wr_type == "unknown" and name:
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    ext_map = {"js": "JavaScript", "html": "HTML", "htm": "HTML",
                               "css": "CSS", "xml": "XML", "png": "PNG",
                               "jpg": "JPG", "jpeg": "JPG", "gif": "GIF",
                               "svg": "SVG", "ico": "ICO", "resx": "RESX"}
                    wr_type = ext_map.get(ext, "unknown")

                # Try to infer related entity from path/name
                related_entity = None
                name_parts = name.replace("/", "_").replace("\\", "_").split("_")
                for part in name_parts:
                    if part and part[0].isupper() and len(part) > 2:
                        related_entity = part
                        break

                resources.append(WebResource(
                    name=name,
                    type=wr_type,
                    displayName=display,
                    description=desc,
                    relatedEntity=related_entity
                ))
        except Exception:
            name = os.path.splitext(os.path.basename(fp))[0]
            resources.append(WebResource(name=name))
    return resources


# ---------------------------------------------------------------------------
# AX Query parser
# ---------------------------------------------------------------------------

def parse_ax_query_file(file_path: str) -> dict:
    """Parse an AX query XML file and return structured info."""
    result = {"name": "", "type": "query", "data_sources": [], "related_tables": []}
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        name_el = root.find(f"{ns}Name") if ns else root.find("Name")
        result["name"] = name_el.text if name_el is not None and name_el.text else os.path.splitext(os.path.basename(file_path))[0]

        for ds_el in root.iter():
            tag = _clean_tag(ds_el.tag)
            if tag in ("AxQuerySimpleDataSource", "AxQuerySimpleEmbeddedDataSource", "DataSource"):
                table_el = ds_el.find(f"{ns}Table" if ns else "Table")
                if table_el is not None and table_el.text:
                    result["data_sources"].append(table_el.text)
                    if table_el.text not in result["related_tables"]:
                        result["related_tables"].append(table_el.text)
                ds_name_el = ds_el.find(f"{ns}Name" if ns else "Name")
                if ds_name_el is not None and ds_name_el.text:
                    if ds_name_el.text not in result["data_sources"]:
                        result["data_sources"].append(ds_name_el.text)
    except Exception:
        result["name"] = os.path.splitext(os.path.basename(file_path))[0]
    return result


# ---------------------------------------------------------------------------
# AX Report (SSRS) parser
# ---------------------------------------------------------------------------

def parse_ax_report_file(file_path: str) -> dict:
    """Parse an AX SSRS report XML file and return structured info."""
    result = {"name": "", "type": "report", "data_sources": [], "parameters": []}
    try:
        tree = _safe_parse(file_path)
        root = tree.getroot()
        ns = _get_namespace(root)

        name_el = root.find(f"{ns}Name") if ns else root.find("Name")
        result["name"] = name_el.text if name_el is not None and name_el.text else os.path.splitext(os.path.basename(file_path))[0]

        for ds_el in root.iter():
            tag = _clean_tag(ds_el.tag)
            if tag in ("AxReportDataSource", "DataSource", "AxReportDataSetDataSource"):
                ds_name_el = ds_el.find(f"{ns}Name" if ns else "Name")
                query_el = ds_el.find(f"{ns}Query" if ns else "Query")
                ds_name = ds_name_el.text if ds_name_el is not None and ds_name_el.text else ""
                query_name = query_el.text if query_el is not None and query_el.text else ""
                if ds_name:
                    result["data_sources"].append(ds_name)
                if query_name and query_name not in result["data_sources"]:
                    result["data_sources"].append(query_name)
            if tag in ("AxReportParameter", "ReportParameter"):
                p_name_el = ds_el.find(f"{ns}Name" if ns else "Name")
                if p_name_el is not None and p_name_el.text:
                    result["parameters"].append(p_name_el.text)
    except Exception:
        result["name"] = os.path.splitext(os.path.basename(file_path))[0]
    return result


# ---------------------------------------------------------------------------
# Fallback parser for unclassified XML (other_xml)
# ---------------------------------------------------------------------------

def parse_other_xml_files(
    file_paths: list[str],
) -> tuple[list[Entity], list[Workflow], list[Plugin]]:
    """Best-effort parse of unclassified XML files.

    Tries each file through entity, workflow, and plugin parsers. If the file
    yields meaningful data it is kept; otherwise it is discarded.
    """
    entities: list[Entity] = []
    workflows: list[Workflow] = []
    plugins: list[Plugin] = []

    for fp in file_paths:
        basename = os.path.splitext(os.path.basename(fp))[0].lower()
        parsed_something = False

        # Try workflow first (workflows are more specific)
        try:
            wfs = parse_workflow_file(fp)
            for wf in wfs:
                if wf.steps and wf.steps != [f"Process defined in {os.path.basename(fp)}"]:
                    workflows.append(wf)
                    parsed_something = True
        except Exception:
            pass

        if parsed_something:
            continue

        # Try plugin
        try:
            pls = parse_plugin_file(fp)
            for pl in pls:
                if pl.triggerEntity or pl.operation:
                    plugins.append(pl)
                    parsed_something = True
        except Exception:
            pass

        if parsed_something:
            continue

        # Try entity
        try:
            ents = parse_entity_file(fp)
            for ent in ents:
                if ent.fields:
                    entities.append(ent)
                    parsed_something = True
        except Exception:
            pass

        # If nothing specific was found, still record as a minimal entity
        # so the file is at least represented in the knowledge graph.
        if not parsed_something:
            entities.append(Entity(
                name=os.path.splitext(os.path.basename(fp))[0],
                displayName=f"[Unclassified] {os.path.basename(fp)}",
                fields=[]
            ))

    return entities, workflows, plugins
