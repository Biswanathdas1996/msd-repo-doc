import os
import re
from lxml import etree
from backend.models.schemas import Entity, EntityField, Workflow, Plugin


KNOWN_CRM_ENTITIES = {
    "account", "contact", "lead", "opportunity", "email", "task", "appointment",
    "phonecall", "letter", "fax", "socialactivity", "activitypointer",
    "activityparty", "activitymimeattachment", "annotation", "attachment",
    "businessunit", "calendar", "campaign", "campaignactivity", "campaignresponse",
    "case", "incident", "contract", "contractdetail", "competitor",
    "connection", "connectionrole", "customeraddress", "discount", "discounttype",
    "invoice", "invoicedetail", "knowledgearticle", "knowledgebaserecord",
    "list", "listmember", "marketing", "metric", "note", "order", "orderclose",
    "organization", "pluginassembly", "plugintype", "post", "pricelevel",
    "product", "productpricelevel", "queue", "queueitem", "quote", "quotedetail",
    "recurringappointmentmaster", "report", "role", "salesliterature",
    "salesorder", "salesorderdetail", "savedquery", "sdkmessage",
    "sdkmessageprocessingstep", "sdkmessagefilter", "serviceappointment",
    "sharepointdocumentlocation", "sharepointsite", "sla", "slaitem",
    "solution", "subject", "systemuser", "team", "template", "territory",
    "transactioncurrency", "uom", "uomschedule", "userquery", "workflow",
}

RE_CLASS = re.compile(
    r'(?:public|internal)\s+(?:static\s+|sealed\s+|abstract\s+|partial\s+)*class\s+(\w+)(?:<[^>]*>)?\s*(?::\s*([^{]+))?',
    re.MULTILINE
)
RE_ENTITY_REF = re.compile(r'(?:new\s+Entity|EntityReference)\s*\(\s*"(\w+)"', re.IGNORECASE)
RE_LOGICAL_NAME = re.compile(r'LogicalName\s*=\s*"(\w+)"', re.IGNORECASE)
RE_QUERY_EXPR = re.compile(r'QueryExpression\s*\(\s*"(\w+)"', re.IGNORECASE)
RE_NAMESPACE = re.compile(r'namespace\s+([\w.]+)')
RE_SUMMARY = re.compile(r'///\s*<summary>\s*(.*?)\s*</summary>', re.DOTALL)

MAX_CS_FILE_SIZE = 2 * 1024 * 1024

WORKFLOW_BASE_TYPES = {"CodeActivity", "BaseCodeActivity", "Activity",
                       "System.Activities.CodeActivity", "NativeActivity"}
PLUGIN_BASE_TYPES = {"IPlugin", "PluginBase", "Plugin",
                     "Microsoft.Xrm.Sdk.IPlugin"}


def detect_source_code_repo(folder: str) -> bool:
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(".cs") or f.endswith(".csproj") or f.endswith(".crmregister"):
                return True
    return False


def parse_source_code_repo(folder: str) -> dict:
    cs_files = []
    csproj_files = []
    crmregister_files = []
    config_files = []

    for root, dirs, files in os.walk(folder):
        for f in files:
            full_path = os.path.join(root, f)
            if f.endswith(".cs"):
                cs_files.append(full_path)
            elif f.endswith(".csproj"):
                csproj_files.append(full_path)
            elif f.endswith(".crmregister"):
                crmregister_files.append(full_path)
            elif f.endswith(".config") and f.lower() != "packages.config":
                config_files.append(full_path)

    entities = []
    workflows = []
    plugins = []
    project_info = {}

    non_test_cs = [f for f in cs_files if not _is_test_file(os.path.relpath(f, folder))]

    class_entity_map = _build_class_entity_map(non_test_cs)

    for reg_file in crmregister_files:
        reg_wf, reg_plugins = _parse_crmregister(reg_file, class_entity_map)
        workflows.extend(reg_wf)
        plugins.extend(reg_plugins)

    reg_workflow_names = {w.name.lower() for w in workflows}
    reg_plugin_names = {p.name.lower() for p in plugins}

    for cs_file in non_test_cs:
        cs_entities, cs_workflows, cs_plugins = _parse_cs_file(cs_file, reg_workflow_names, reg_plugin_names)
        entities.extend(cs_entities)
        workflows.extend(cs_workflows)
        plugins.extend(cs_plugins)

    for csproj in csproj_files:
        info = _parse_csproj(csproj)
        if info:
            project_info[os.path.basename(csproj)] = info

    entity_names_seen = set()
    unique_entities = []
    for e in entities:
        if e.name.lower() not in entity_names_seen:
            entity_names_seen.add(e.name.lower())
            unique_entities.append(e)

    wf_names_seen = set()
    unique_workflows = []
    for w in workflows:
        key = w.name.lower()
        if key not in wf_names_seen:
            wf_names_seen.add(key)
            unique_workflows.append(w)

    plugin_keys_seen = set()
    unique_plugins = []
    for p in plugins:
        key = (p.name.lower(), (p.triggerEntity or "").lower(), (p.operation or "").lower(), (p.stage or "").lower())
        if key not in plugin_keys_seen:
            plugin_keys_seen.add(key)
            unique_plugins.append(p)

    return {
        "entities": unique_entities,
        "workflows": unique_workflows,
        "plugins": unique_plugins,
        "project_info": project_info,
        "source_files_count": len(cs_files),
        "projects_count": len(csproj_files),
    }


def _is_test_file(rel_path: str) -> bool:
    lower = rel_path.lower()
    return "test" in lower and ("unittest" in lower or "integrationtest" in lower or "test/" in lower or "tests/" in lower)


def _scan_cs_entities(cs_file: str) -> tuple[list[str], set[str]]:
    """Return (class_names, known_crm_entity_refs) for a single .cs file."""
    try:
        if os.path.getsize(cs_file) > MAX_CS_FILE_SIZE:
            return [], set()
        with open(cs_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return [], set()

    entity_refs: set[str] = set()
    for m in RE_ENTITY_REF.finditer(content):
        entity_refs.add(m.group(1).lower())
    for m in RE_LOGICAL_NAME.finditer(content):
        entity_refs.add(m.group(1).lower())
    for m in RE_QUERY_EXPR.finditer(content):
        entity_refs.add(m.group(1).lower())
    known = {e for e in entity_refs if e in KNOWN_CRM_ENTITIES}

    classes = [m.group(1) for m in RE_CLASS.finditer(content)]
    return classes, known


def _build_class_entity_map(cs_files: list[str]) -> dict[str, set[str]]:
    """Map each C# class name → CRM entity refs.

    Uses three levels of aggregation so that workflow/plugin classes pick up
    entity references from related files in the same project folder:
      1. Per-file refs (direct)
      2. All refs within the same directory (sibling files)
      3. All refs within the parent directory (cross-project within the solution)
    """
    file_info: dict[str, tuple[list[str], set[str]]] = {}
    for cs_file in cs_files:
        classes, known = _scan_cs_entities(cs_file)
        if classes or known:
            file_info[cs_file] = (classes, known)

    dir_entities: dict[str, set[str]] = {}
    for cs_file, (_, refs) in file_info.items():
        d = os.path.dirname(cs_file)
        dir_entities.setdefault(d, set()).update(refs)

    parent_entities: dict[str, set[str]] = {}
    for d, refs in dir_entities.items():
        parent = os.path.dirname(d)
        parent_entities.setdefault(parent, set()).update(refs)

    result: dict[str, set[str]] = {}
    for cs_file, (classes, direct) in file_info.items():
        d = os.path.dirname(cs_file)
        parent = os.path.dirname(d)
        combined = direct | dir_entities.get(d, set()) | parent_entities.get(parent, set())
        for class_name in classes:
            result[class_name] = combined
            result[class_name.lower()] = combined

    return result


def _parse_crmregister(file_path: str, class_entity_map: dict[str, set[str]] | None = None) -> tuple[list[Workflow], list[Plugin]]:
    workflows = []
    plugins = []
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        tree = etree.parse(file_path, parser)
        root = tree.getroot()
        ns_map = root.nsmap
        ns = ""
        if None in ns_map:
            ns = "{" + ns_map[None] + "}"

        for wf_type in root.iter(f"{ns}WorkflowType" if ns else "WorkflowType"):
            name = wf_type.get("FriendlyName") or wf_type.get("Name", "")
            desc = wf_type.get("Description", "")
            type_name = wf_type.get("TypeName", "")
            group = wf_type.get("WorkflowActivityGroupName", "")

            simple_class = type_name.split(".")[-1] if type_name else ""
            related: list[str] = []
            if class_entity_map and simple_class:
                found = class_entity_map.get(simple_class) or class_entity_map.get(simple_class.lower()) or set()
                related = sorted(found)

            steps = []
            if desc:
                steps.append(desc)
            if type_name:
                steps.append(f"Implementation: {type_name}")

            workflows.append(Workflow(
                name=name,
                triggerEntity=None,
                trigger="Custom Workflow Activity",
                steps=steps or [f"Workflow activity in group {group}"],
                conditions=[],
                relatedEntities=related,
            ))

        for plugin_type in root.iter(f"{ns}Type" if ns else "Type"):
            name = plugin_type.get("FriendlyName") or plugin_type.get("Name", "")
            type_name = plugin_type.get("TypeName", "")
            desc = plugin_type.get("Description", "")

            for step in plugin_type.iter(f"{ns}Step" if ns else "Step"):
                step_name = step.get("Name") or step.get("FriendlyName") or ""
                message = step.get("MessageName", "")
                entity = step.get("PrimaryEntityName", "")
                stage_val = step.get("Stage", "")
                stage_map = {"10": "Pre-Validation", "20": "Pre-Operation", "40": "Post-Operation"}
                stage = stage_map.get(stage_val, stage_val)

                plugins.append(Plugin(
                    name=step_name or name,
                    triggerEntity=entity or None,
                    operation=message or None,
                    stage=stage or None,
                    description=desc or f"Plugin type: {type_name}"
                ))

            if not list(plugin_type.iter(f"{ns}Step" if ns else "Step")):
                if name:
                    plugins.append(Plugin(
                        name=name,
                        description=desc or f"Plugin type: {type_name}"
                    ))

    except Exception:
        pass

    return workflows, plugins


def _parse_cs_file(file_path: str, known_wf_names: set, known_plugin_names: set) -> tuple[list[Entity], list[Workflow], list[Plugin]]:
    entities = []
    workflows = []
    plugins = []

    try:
        if os.path.getsize(file_path) > MAX_CS_FILE_SIZE:
            return entities, workflows, plugins
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return entities, workflows, plugins

    entity_refs = set()
    for match in RE_ENTITY_REF.finditer(content):
        entity_refs.add(match.group(1).lower())
    for match in RE_LOGICAL_NAME.finditer(content):
        entity_refs.add(match.group(1).lower())
    for match in RE_QUERY_EXPR.finditer(content):
        entity_refs.add(match.group(1).lower())

    for ref in entity_refs:
        if ref in KNOWN_CRM_ENTITIES:
            entities.append(Entity(
                name=ref,
                displayName=ref.replace("_", " ").title(),
                fields=[]
            ))

    for class_match in RE_CLASS.finditer(content):
        class_name = class_match.group(1)
        base_classes = class_match.group(2) or ""
        base_list = [b.strip() for b in base_classes.split(",")]

        def _matches_base(base_list, known_set):
            for b in base_list:
                stripped = b.split(".")[-1]
                if b in known_set or stripped in known_set:
                    return True
            return False

        is_workflow = _matches_base(base_list, WORKFLOW_BASE_TYPES)
        is_plugin = _matches_base(base_list, PLUGIN_BASE_TYPES)

        if class_name.lower() in known_wf_names or class_name.lower() in known_plugin_names:
            continue

        summary = ""
        class_pos = class_match.start()
        before = content[:class_pos]
        summary_match = RE_SUMMARY.search(before[-500:] if len(before) > 500 else before)
        if summary_match:
            summary = summary_match.group(1).strip()
            summary = re.sub(r'\s*///\s*', ' ', summary).strip()

        if is_workflow:
            friendly = _class_to_friendly(class_name)
            steps = []
            if summary:
                steps.append(summary)

            known_in_file = sorted(e for e in entity_refs if e in KNOWN_CRM_ENTITIES)
            trigger_entity = known_in_file[0] if known_in_file else None

            workflows.append(Workflow(
                name=friendly,
                triggerEntity=trigger_entity,
                trigger="Custom Workflow Activity",
                steps=steps or [f"Custom workflow activity: {class_name}"],
                conditions=[],
                relatedEntities=known_in_file,
            ))

        elif is_plugin:
            friendly = _class_to_friendly(class_name)
            known_in_file = sorted(e for e in entity_refs if e in KNOWN_CRM_ENTITIES)
            trigger_entity = known_in_file[0] if known_in_file else None

            plugins.append(Plugin(
                name=friendly,
                triggerEntity=trigger_entity,
                description=summary or f"Plugin class: {class_name}"
            ))

    return entities, workflows, plugins


def _parse_csproj(file_path: str) -> dict:
    info = {}
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        tree = etree.parse(file_path, parser)
        root = tree.getroot()

        ns = ""
        tag = root.tag
        if tag.startswith("{"):
            ns = tag.split("}")[0] + "}"

        for el_name in ["AssemblyName", "RootNamespace", "Description", "TargetFramework", "TargetFrameworkVersion"]:
            el = root.find(f".//{ns}{el_name}")
            if el is not None and el.text:
                info[el_name] = el.text

        refs = []
        for ref in root.findall(f".//{ns}Reference"):
            inc = ref.get("Include", "")
            if inc and "microsoft" in inc.lower():
                refs.append(inc.split(",")[0])
        if refs:
            info["CrmReferences"] = refs

    except Exception:
        pass
    return info


def _class_to_friendly(class_name: str) -> str:
    name = re.sub(r'(Activity|Plugin|Step)$', '', class_name)
    name = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)
    return name.strip() or class_name
