from lxml import etree
import os
from backend.models.schemas import Entity, EntityField, Workflow, Plugin, Role, WebResource, FormDetail


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
            info["uniqueName"] = publisher_el.text

        desc_el = root.find(f".//{ns}Descriptions//{ns}Description") if ns else root.find(".//Descriptions//Description")
        if desc_el is not None:
            info["description"] = desc_el.get("description", desc_el.text or "")

        # Managed flag
        managed_el = root.find(f".//{ns}Managed") if ns else root.find(".//Managed")
        if managed_el is not None and managed_el.text:
            info["isManaged"] = managed_el.text.lower() in ("1", "true", "yes")

        # Dependencies
        dependencies = []
        dep_elements = _find_all(root, ns, ["MissingDependency", "Required", "Dependency"])
        for dep_el in dep_elements:
            dep_name = dep_el.get("solution") or dep_el.get("schemaName") or _get_child_text(dep_el, ns, "solution")
            if dep_name and dep_name not in dependencies:
                dependencies.append(dep_name)
        if dependencies:
            info["dependencies"] = dependencies

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

                # --- Robust Required detection ---
                is_required = False
                # Direct Required="true" attribute
                req_attr = attr.get("Required") or attr.get("required")
                if req_attr and req_attr.lower() in ("true", "1", "yes"):
                    is_required = True
                # RequiredLevel element (Dynamics CRM pattern)
                req_level_el = attr.find(f"{ns}RequiredLevel" if ns else "RequiredLevel")
                if req_level_el is None:
                    req_level_el = attr.find(f"{ns}requiredlevel" if ns else "requiredlevel")
                if req_level_el is not None:
                    req_val = (req_level_el.get("Value") or req_level_el.get("value")
                              or req_level_el.text or "").lower()
                    if req_val in ("applicationrequired", "systemrequired", "required", "true", "1"):
                        is_required = True
                # isrequired attribute
                is_req_attr = attr.get("IsRequired") or attr.get("isRequired") or attr.get("isrequired")
                if is_req_attr and is_req_attr.lower() in ("true", "1", "yes"):
                    is_required = True

                # Description
                field_desc = attr.get("Description") or _get_child_text(attr, ns, "Description")

                # MaxLength
                max_len = attr.get("MaxLength") or _get_child_text(attr, ns, "MaxLength")
                max_len_int = int(max_len) if max_len and max_len.isdigit() else None

                if field_name:
                    fields.append(EntityField(
                        name=field_name,
                        type=field_type,
                        displayName=field_display,
                        required=is_required,
                        description=field_desc,
                        maxLength=max_len_int
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

            # Mode (Sync/Async)
            mode = (wf_el.get("Mode") or wf_el.get("mode") or
                   _get_child_text(wf_el, ns, "Mode"))
            if not mode:
                # Check IsTransacted or Type for sync hints
                is_transacted = wf_el.get("IsTransacted") or _get_child_text(wf_el, ns, "IsTransacted")
                if is_transacted and is_transacted.lower() in ("true", "1"):
                    mode = "Synchronous"
                wf_type = wf_el.get("Type") or _get_child_text(wf_el, ns, "Type")
                if wf_type and wf_type.lower() in ("1", "definition"):
                    if not mode:
                        mode = "Background"

            # Scope
            scope = (wf_el.get("Scope") or wf_el.get("scope") or
                    _get_child_text(wf_el, ns, "Scope"))

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
                mode=mode,
                scope=scope,
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

            # Extended plugin metadata
            exec_mode = (p_el.get("Mode") or p_el.get("ExecutionMode") or
                        _get_child_text(p_el, ns, "Mode") or
                        _get_child_text(p_el, ns, "ExecutionMode"))
            if exec_mode:
                mode_map = {"0": "Synchronous", "1": "Asynchronous"}
                exec_mode = mode_map.get(exec_mode, exec_mode)

            exec_order_str = (p_el.get("Rank") or p_el.get("ExecutionOrder") or
                            _get_child_text(p_el, ns, "Rank") or
                            _get_child_text(p_el, ns, "ExecutionOrder"))
            exec_order = int(exec_order_str) if exec_order_str and exec_order_str.isdigit() else None

            filter_attrs_str = (p_el.get("FilteringAttributes") or
                               _get_child_text(p_el, ns, "FilteringAttributes"))
            filter_attrs = [a.strip() for a in filter_attrs_str.split(",") if a.strip()] if filter_attrs_str else None

            assembly = (p_el.get("AssemblyName") or p_el.get("PluginAssembly") or
                       _get_child_text(p_el, ns, "AssemblyName") or
                       _get_child_text(p_el, ns, "PluginAssembly"))

            secure_config = (p_el.get("SecureConfiguration") or
                           _get_child_text(p_el, ns, "SecureConfiguration"))

            plugins.append(Plugin(
                name=name,
                triggerEntity=trigger_entity,
                operation=operation,
                stage=stage,
                description=desc,
                executionMode=exec_mode,
                executionOrder=exec_order,
                filteringAttributes=filter_attrs,
                assemblyName=assembly,
                secureConfiguration=secure_config
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


def parse_form_files_detailed(file_paths: list[str]) -> list[FormDetail]:
    """Parse form XML files and extract layout details (tabs, sections, controls)."""
    form_details: list[FormDetail] = []
    for fp in file_paths:
        source_file = os.path.basename(fp)
        try:
            tree = _safe_parse(fp)
            root = tree.getroot()
            ns = _get_namespace(root)
            form_elements = _find_all(root, ns, ["form", "Form", "systemform", "SystemForm"])

            if not form_elements:
                # Whole file is one form
                form_elements = [root]

            for fe in form_elements:
                name = (fe.get("Name") or fe.get("name") or
                       _get_child_text(fe, ns, "Name") or
                       os.path.splitext(source_file)[0])

                # Infer entity from form name or parent XML
                entity = fe.get("Entity") or fe.get("entity") or _get_child_text(fe, ns, "Entity")
                if not entity and name:
                    # Try to derive from form name: "Account Main Form" → "Account"
                    parts = name.split()
                    if parts:
                        entity = parts[0]

                # Tabs
                tabs = []
                tab_elements = _find_all(fe, ns, ["tab", "Tab"])
                for tab_el in tab_elements:
                    tab_name = tab_el.get("Name") or tab_el.get("name") or _get_child_text(tab_el, ns, "Name")
                    if tab_name:
                        tabs.append(tab_name)

                # Sections
                sections = []
                section_elements = _find_all(fe, ns, ["section", "Section"])
                for sec_el in section_elements:
                    sec_name = sec_el.get("Name") or sec_el.get("name") or _get_child_text(sec_el, ns, "Name")
                    if sec_name:
                        sections.append(sec_name)

                # Controls / fields on the form
                controls = []
                ctrl_elements = _find_all(fe, ns, ["control", "Control", "cell", "Cell"])
                for ctrl_el in ctrl_elements:
                    ctrl_name = (ctrl_el.get("id") or ctrl_el.get("Id") or
                                ctrl_el.get("datafieldname") or ctrl_el.get("DataFieldName") or
                                ctrl_el.get("Name") or ctrl_el.get("name"))
                    if ctrl_name:
                        controls.append(ctrl_name)

                form_details.append(FormDetail(
                    name=name,
                    entity=entity,
                    tabs=tabs,
                    sections=sections,
                    controls=controls,
                    sourceFile=source_file
                ))

        except Exception:
            name = os.path.splitext(os.path.basename(fp))[0]
            form_details.append(FormDetail(name=name, sourceFile=source_file))

    return form_details


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
# CRM customizations.xml parser (real managed/unmanaged solution ZIPs)
# ---------------------------------------------------------------------------

CRM_STAGE_MAP = {
    "10": "PreValidation",
    "20": "PreOperation",
    "40": "PostOperation",
}

CRM_MODE_MAP = {
    "0": "Synchronous",
    "1": "Asynchronous",
}

CRM_SCOPE_MAP = {
    "1": "User",
    "2": "Business Unit",
    "3": "Parent-Child Business Units",
    "4": "Organization",
}

CRM_WF_MODE_MAP = {
    "0": "Background",
    "1": "Real-time",
}

# Common Dynamics CRM entity type-code → logical-name lookup
CRM_ENTITY_TYPE_CODES: dict[str, str] = {
    "1": "account", "2": "contact", "3": "opportunity",
    "4": "lead", "5": "organization", "8": "systemuser",
    "9": "team", "10": "businessunit", "50": "position",
    "112": "incident", "1024": "quote", "1088": "salesorder",
    "1090": "invoice", "2013": "territory",
    "4200": "activitypointer", "4201": "appointment",
    "4202": "email", "4210": "phonecall", "4212": "task",
    "4214": "serviceappointment", "4300": "marketinglist",
    "4400": "campaign", "4402": "campaignactivity",
    "4406": "campaignresponse",
}

# XAML activity type → human-readable label (CRM-specific activities)
XAML_CRM_ACTIVITIES = {
    "setentityproperty": "Set Field",
    "getentityproperty": "Get Field",
    "createentity": "Create Record",
    "updateentity": "Update Record",
    "deleteentity": "Delete Record",
    "assignentity": "Assign Record",
    "sendemail": "Send Email",
    "sendemailfromtemplate": "Send Email (Template)",
    "setstate": "Set Status",
    "setattributevalue": "Set Field Value",
    "createactivityparty": "Add Activity Party",
    "activityreference": "Call Child Workflow",
    "terminateworkflow": "Terminate Workflow",
    "conditionbranch": "Condition Branch",
    "conditionstep": "Condition Step",
}

# Standard WF4 control-flow activity labels
XAML_WF_CONTROL = {
    "if": "Check Condition",
    "switch": "Switch",
    "while": "While Loop",
    "dowhile": "Do-While Loop",
    "delay": "Wait / Delay",
    "invokemethod": "Invoke Method",
    "flowdecision": "Decision",
    "flowswitch": "Switch (Flowchart)",
    "persist": "Persist State",
}


def _large_file_parser():
    """Parser that can handle very large XML files like customizations.xml."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
        huge_tree=True,
    )


def _get_localized_name(element, ns: str, default: str = "") -> str:
    """Extract English (1033) localized name from <LocalizedNames> child."""
    for tag in ("LocalizedNames", "localizednames"):
        container = element.find(f"{ns}{tag}" if ns else tag)
        if container is None:
            container = element.find(f".//{ns}{tag}" if ns else f".//{tag}")
        if container is not None:
            best = default
            for ln in container:
                lang = ln.get("languagecode") or ""
                desc = ln.get("description") or ln.text or ""
                if lang == "1033" and desc:
                    return desc
                if desc and not best:
                    best = desc
            return best
    return default


def _resolve_entity_code(code: str) -> str:
    """Resolve numeric entity-type-code or logical name to a display name."""
    if not code:
        return ""
    code = code.strip()
    if code.isdigit():
        logical = CRM_ENTITY_TYPE_CODES.get(code, "")
        if logical:
            return logical.replace("_", " ").title()
        return f"Entity_{code}"
    # Already a logical name – title-case it
    return code.replace("_", " ").title() if code.islower() else code


# ---- customizations.xml entry-point ----

def parse_customizations_xml(file_path: str) -> dict:
    """Parse the monolithic customizations.xml from a real CRM solution ZIP.

    Returns dict with keys:
        entities, workflows, plugins, forms, form_details, roles,
        workflow_xaml_map   (workflow-name → relative XAML path)
    """
    result: dict = {
        "entities": [],
        "workflows": [],
        "plugins": [],
        "forms": [],
        "form_details": [],
        "roles": [],
        "workflow_xaml_map": {},
    }

    try:
        tree = etree.parse(file_path, _large_file_parser())
        root = tree.getroot()
        ns = _get_namespace(root)

        _parse_cust_entities(root, ns, result)
        _parse_cust_workflows(root, ns, result)
        _parse_cust_plugin_steps(root, ns, result)
        _parse_cust_roles(root, ns, result)
    except Exception:
        pass

    return result


# ---- customizations.xml: Entities ----

def _parse_cust_entities(root, ns: str, result: dict):
    """Extract entities with fields and forms from <Entities> in customizations.xml."""
    entities_container = None
    for child in root:
        if _clean_tag(child.tag) == "Entities":
            entities_container = child
            break
    if entities_container is None:
        entities_container = root.find(f".//{ns}Entities" if ns else ".//Entities")
    if entities_container is None:
        return

    for entity_el in entities_container:
        if _clean_tag(entity_el.tag) != "Entity":
            continue

        name_el = entity_el.find(f"{ns}Name" if ns else "Name")
        if name_el is None:
            continue
        logical_name = (name_el.text or "").strip()
        if not logical_name:
            continue

        display_name = (
            name_el.get("LocalizedName")
            or name_el.get("OriginalName")
            or _get_localized_name(entity_el, ns)
        )
        if not display_name:
            display_name = logical_name.replace("_", " ").title()

        # ---- fields from <EntityInfo><entity><attributes> ----
        fields: list[EntityField] = []
        attr_source = entity_el
        entity_info = entity_el.find(f".//{ns}EntityInfo" if ns else ".//EntityInfo")
        if entity_info is not None:
            inner = entity_info.find(f"{ns}entity" if ns else "entity")
            if inner is None:
                inner = entity_info.find(f".//{ns}entity" if ns else ".//entity")
            if inner is not None:
                attr_source = inner

        for ac_tag in ("attributes", "Attributes"):
            attrs_container = attr_source.find(f".//{ns}{ac_tag}" if ns else f".//{ac_tag}")
            if attrs_container is not None:
                break
        else:
            attrs_container = None

        if attrs_container is not None:
            for attr_el in attrs_container:
                if _clean_tag(attr_el.tag).lower() != "attribute":
                    continue

                field_name = (
                    attr_el.get("PhysicalName")
                    or attr_el.get("Name")
                    or attr_el.get("name")
                    or ""
                )
                if not field_name:
                    pn_el = attr_el.find(f"{ns}PhysicalName" if ns else "PhysicalName")
                    if pn_el is not None and pn_el.text:
                        field_name = pn_el.text
                if not field_name:
                    continue

                # Type
                field_type = attr_el.get("Type") or ""
                if not field_type:
                    type_el = attr_el.find(f"{ns}Type" if ns else "Type")
                    if type_el is not None and type_el.text:
                        field_type = type_el.text
                field_type = field_type or "string"

                # Required
                is_required = False
                for rl_tag in ("RequiredLevel", "requiredlevel"):
                    rl = attr_el.find(f"{ns}{rl_tag}" if ns else rl_tag)
                    if rl is not None:
                        val = (rl.text or "").lower().strip()
                        if val in ("applicationrequired", "systemrequired", "required"):
                            is_required = True
                        break
                req_attr = attr_el.get("Required") or attr_el.get("required")
                if req_attr and req_attr.lower() in ("true", "1", "yes"):
                    is_required = True

                # Display name (English)
                field_display = None
                for dn_tag in ("displaynames", "DisplayNames"):
                    dn_c = attr_el.find(f".//{ns}{dn_tag}" if ns else f".//{dn_tag}")
                    if dn_c is not None:
                        for dn_el in dn_c:
                            lang = dn_el.get("languagecode") or ""
                            desc = dn_el.get("description") or dn_el.text
                            if (lang == "1033" or not field_display) and desc:
                                field_display = desc
                        break

                # MaxLength
                max_len = None
                ml_el = attr_el.find(f"{ns}MaxLength" if ns else "MaxLength")
                if ml_el is not None and ml_el.text and ml_el.text.strip().isdigit():
                    max_len = int(ml_el.text.strip())

                # Description
                field_desc = None
                for dp in (
                    f".//{ns}Descriptions/{ns}Description" if ns else ".//Descriptions/Description",
                    f".//{ns}Description" if ns else ".//Description",
                ):
                    d_el = attr_el.find(dp)
                    if d_el is not None:
                        field_desc = d_el.get("description") or d_el.text
                        if field_desc:
                            break

                fields.append(EntityField(
                    name=field_name,
                    type=field_type,
                    displayName=field_display,
                    required=is_required,
                    description=field_desc,
                    maxLength=max_len,
                ))

        result["entities"].append(Entity(
            name=display_name,
            displayName=display_name,
            fields=fields,
        ))

        # ---- forms from <FormXml> ----
        form_xml_el = entity_el.find(f".//{ns}FormXml" if ns else ".//FormXml")
        if form_xml_el is not None:
            _parse_cust_entity_forms(form_xml_el, ns, logical_name, display_name, result)


def _parse_cust_entity_forms(form_xml_el, ns: str, logical_name: str, display_name: str, result: dict):
    """Parse forms inside an entity's <FormXml> block in customizations.xml."""
    forms_groups = form_xml_el.findall(f"{ns}forms" if ns else "forms")
    if not forms_groups:
        forms_groups = list(form_xml_el)

    for fg in forms_groups:
        fg_tag = _clean_tag(fg.tag).lower()
        if fg_tag != "forms":
            continue
        form_type = fg.get("type") or "main"

        for sf_el in fg:
            if _clean_tag(sf_el.tag).lower() != "systemform":
                continue

            form_name = _get_localized_name(sf_el, ns)
            if not form_name:
                form_name = _get_child_text(sf_el, ns, "Name") or ""
            if not form_name:
                form_name = f"{display_name} {form_type.title()} Form"

            result["forms"].append(form_name)

            # Layout details
            tabs: list[str] = []
            sections: list[str] = []
            controls: list[str] = []

            form_el = sf_el.find(f".//{ns}form" if ns else ".//form")
            if form_el is not None:
                for descendant in form_el.iter():
                    dtag = _clean_tag(descendant.tag).lower()
                    if dtag == "tab":
                        t = descendant.get("name") or descendant.get("Name") or _get_localized_name(descendant, ns)
                        if t:
                            tabs.append(t)
                    elif dtag == "section":
                        s = descendant.get("name") or descendant.get("Name") or _get_localized_name(descendant, ns)
                        if s:
                            sections.append(s)
                    elif dtag == "control":
                        c = descendant.get("id") or descendant.get("datafieldname") or descendant.get("DataFieldName") or ""
                        if c:
                            controls.append(c)

            result["form_details"].append(FormDetail(
                name=form_name,
                entity=display_name,
                tabs=tabs,
                sections=sections,
                controls=controls,
                sourceFile="customizations.xml",
            ))


# ---- customizations.xml: Workflows ----

def _parse_cust_workflows(root, ns: str, result: dict):
    """Extract workflow metadata from <Workflows> in customizations.xml."""
    wf_container = None
    for child in root:
        if _clean_tag(child.tag) == "Workflows":
            wf_container = child
            break
    if wf_container is None:
        wf_container = root.find(f".//{ns}Workflows" if ns else ".//Workflows")
    if wf_container is None:
        return

    for wf_el in wf_container:
        if _clean_tag(wf_el.tag) != "Workflow":
            continue

        name = (
            wf_el.get("Name")
            or _get_localized_name(wf_el, ns)
            or _get_child_text(wf_el, ns, "Name")
            or "Unknown Workflow"
        )

        primary_entity = _get_child_text(wf_el, ns, "PrimaryEntity")
        if primary_entity:
            primary_entity = _resolve_entity_code(primary_entity)

        # Mode (Background / Real-time)
        mode_val = _get_child_text(wf_el, ns, "Mode")
        mode = CRM_WF_MODE_MAP.get(mode_val, mode_val) if mode_val else None

        # Scope
        scope_val = _get_child_text(wf_el, ns, "Scope")
        scope = CRM_SCOPE_MAP.get(scope_val, scope_val) if scope_val else None

        # Trigger info
        triggers = []
        for trigger_tag, label in [
            ("TriggerOnCreate", "Create"),
            ("TriggerOnDelete", "Delete"),
            ("TriggerOnUpdateAttributeList", "Update"),
        ]:
            tv = _get_child_text(wf_el, ns, trigger_tag)
            if tv and tv not in ("0", "false", ""):
                if trigger_tag == "TriggerOnUpdateAttributeList":
                    triggers.append(f"Update ({tv})")
                else:
                    triggers.append(label)

        on_demand = _get_child_text(wf_el, ns, "OnDemand")
        if on_demand and on_demand not in ("0", "false"):
            triggers.append("On-Demand")

        trigger_str = ", ".join(triggers) if triggers else None

        # XAML file reference (for later merging with parsed XAML steps)
        xaml_file = _get_child_text(wf_el, ns, "XamlFileName")
        if xaml_file:
            result["workflow_xaml_map"][name] = xaml_file.strip().lstrip("/")

        # Workflow type / category
        wf_type = _get_child_text(wf_el, ns, "Type")
        wf_category = _get_child_text(wf_el, ns, "Category")
        is_bpf = wf_category == "4"   # Business Process Flow

        steps = []
        if is_bpf:
            steps.append("Business Process Flow")
        elif xaml_file:
            steps.append(f"XAML definition: {xaml_file.strip().lstrip('/')}")

        result["workflows"].append(Workflow(
            name=name,
            triggerEntity=primary_entity,
            trigger=trigger_str,
            mode=mode,
            scope=scope,
            steps=steps,
        ))


# ---- customizations.xml: SdkMessageProcessingSteps (Plugin registrations) ----

def _parse_cust_plugin_steps(root, ns: str, result: dict):
    """Extract plugin step registrations from <SdkMessageProcessingSteps>."""
    steps_container = None
    for child in root:
        tag = _clean_tag(child.tag)
        if tag == "SdkMessageProcessingSteps":
            steps_container = child
            break
    if steps_container is None:
        steps_container = root.find(
            f".//{ns}SdkMessageProcessingSteps" if ns else ".//SdkMessageProcessingSteps"
        )
    if steps_container is None:
        return

    for step_el in steps_container:
        if _clean_tag(step_el.tag) != "SdkMessageProcessingStep":
            continue

        name = (
            step_el.get("Name")
            or _get_child_text(step_el, ns, "Name")
            or "Unknown Plugin Step"
        )

        # Plugin type name (the .NET class)
        plugin_type = (
            _get_child_text(step_el, ns, "PluginTypeName")
            or _get_child_text(step_el, ns, "PluginTypeId")
            or name
        )

        # Entity
        entity_code = (
            _get_child_text(step_el, ns, "PrimaryObjectTypeCode")
            or step_el.get("PrimaryObjectTypeCode")
        )
        trigger_entity = _resolve_entity_code(entity_code) if entity_code else None

        # Message (Create / Update / Delete / …)
        # In customizations.xml the SdkMessageId is often a GUID;
        # try to infer message from step name: "Namespace.Class: Create of entity"
        message = _get_child_text(step_el, ns, "SdkMessageId")
        if message and len(message) > 20:
            # Likely a GUID – try to parse from step Name instead
            import re
            m = re.search(r":\s*(\w+)\s+of\s+", name, re.IGNORECASE)
            if m:
                message = m.group(1)
            else:
                message = None
        if not message:
            message = step_el.get("Message") or step_el.get("SdkMessageId")

        # Stage
        stage_val = _get_child_text(step_el, ns, "Stage") or step_el.get("Stage")
        stage = CRM_STAGE_MAP.get(stage_val, stage_val) if stage_val else None

        # Mode (sync/async)
        mode_val = _get_child_text(step_el, ns, "Mode") or step_el.get("Mode")
        exec_mode = CRM_MODE_MAP.get(mode_val, mode_val) if mode_val else None

        # Rank / execution order
        rank_str = (
            _get_child_text(step_el, ns, "Rank")
            or _get_child_text(step_el, ns, "ExecutionOrder")
            or step_el.get("Rank")
        )
        exec_order = int(rank_str) if rank_str and rank_str.isdigit() else None

        # Filtering attributes
        fa_str = (
            _get_child_text(step_el, ns, "FilteringAttributes")
            or step_el.get("FilteringAttributes")
        )
        filter_attrs = [a.strip() for a in fa_str.split(",") if a.strip()] if fa_str else None

        # Description
        desc = _get_child_text(step_el, ns, "Description") or step_el.get("Description")

        result["plugins"].append(Plugin(
            name=plugin_type if plugin_type != name else name,
            triggerEntity=trigger_entity,
            operation=message,
            stage=stage,
            executionMode=exec_mode,
            executionOrder=exec_order,
            filteringAttributes=filter_attrs,
            description=desc or name,
        ))


# ---- customizations.xml: Roles ----

def _parse_cust_roles(root, ns: str, result: dict):
    """Extract security roles from <Roles> in customizations.xml."""
    roles_container = None
    for child in root:
        if _clean_tag(child.tag) == "Roles":
            roles_container = child
            break
    if roles_container is None:
        roles_container = root.find(f".//{ns}Roles" if ns else ".//Roles")
    if roles_container is None:
        return

    for role_el in roles_container:
        if _clean_tag(role_el.tag) != "Role":
            continue

        role_name = (
            _get_child_text(role_el, ns, "Name")
            or _get_localized_name(role_el, ns)
            or role_el.get("name")
            or "Unknown Role"
        )

        privileges: list[str] = []
        priv_container = role_el.find(f".//{ns}RolePrivileges" if ns else ".//RolePrivileges")
        if priv_container is not None:
            for priv_el in priv_container:
                pname = priv_el.get("name") or priv_el.get("Name") or ""
                if pname:
                    privileges.append(pname)

        desc = _get_child_text(role_el, ns, "Description") or role_el.get("Description")

        result["roles"].append(Role(
            name=role_name,
            privileges=privileges,
            description=desc,
        ))


# ---------------------------------------------------------------------------
# XAML workflow parser (CRM .xaml workflow definitions)
# ---------------------------------------------------------------------------

def parse_xaml_workflow_file(file_path: str) -> tuple[list[str], list[str]]:
    """Parse a CRM XAML workflow (.xaml) and extract human-readable steps.

    Returns (steps, conditions) where both are lists of strings.
    """
    steps: list[str] = []
    conditions: list[str] = []

    try:
        tree = etree.parse(file_path, _large_file_parser())
        root = tree.getroot()

        for element in root.iter():
            tag = element.tag
            if "}" in tag:
                tag = tag.split("}")[1]
            tag_lower = tag.lower()

            # CRM-specific workflow activities
            if tag_lower in XAML_CRM_ACTIVITIES:
                desc = XAML_CRM_ACTIVITIES[tag_lower]
                attr_name = element.get("Attribute") or element.get("PropertyName") or ""
                entity_ref = element.get("EntityName") or element.get("Entity") or ""
                if attr_name:
                    desc += f": {attr_name}"
                if entity_ref and "[" not in entity_ref:
                    desc += f" ({entity_ref})"
                steps.append(desc)

            # WF4 control-flow activities (skip noisy ones like Sequence)
            elif tag_lower in XAML_WF_CONTROL:
                desc = XAML_WF_CONTROL[tag_lower]

                # For If/FlowDecision, try to grab the condition text
                if tag_lower in ("if", "flowdecision"):
                    for child in element:
                        child_tag = _clean_tag(child.tag).lower()
                        if "condition" in child_tag:
                            for sub in child.iter():
                                if sub.text and sub.text.strip():
                                    cond = sub.text.strip()
                                    conditions.append(cond)
                                    if len(cond) <= 120:
                                        desc += f": {cond}"
                                    else:
                                        desc += f": {cond[:117]}..."
                                    break
                            break

                steps.append(desc)
    except Exception:
        steps.append(f"XAML workflow in {os.path.basename(file_path)}")

    if not steps:
        steps.append(f"Workflow defined in {os.path.basename(file_path)}")

    return steps, conditions


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
