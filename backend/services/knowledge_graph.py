"""
Knowledge-graph builder.

Constructs a KnowledgeGraph from parsed solution artefacts (entities, plugins,
workflows, forms, roles, web-resources) and -- for AX / F&O solutions -- raw class
data, report data and query data.

The graph is first built with deterministic heuristics so that the core
relationships are always present.  An optional LLM enrichment step can then
be called to discover deeper, semantic relationships.
"""

from __future__ import annotations

import re
from backend.models.schemas import (
    Entity, Workflow, Plugin, Role, WebResource, KnowledgeGraph,
    KnowledgeGraphEntity, KnowledgeGraphWorkflow,
    KnowledgeGraphPlugin, KnowledgeGraphRole,
    KnowledgeGraphWebResource, KnowledgeGraphFieldDetail,
    FormDetail, Relationship
)


# ---------------------------------------------------------------------------
# Constants – single source of truth for reference-type prefixes,
# suffix-to-relationship mappings, and fuzzy-matching thresholds.
# ---------------------------------------------------------------------------

# Reference-type prefixes emitted by xml_parser.py
_REF_EXTENSION_OF = "extension_of:"
_REF_TABLE_REF = "table_ref:"
_REF_IMPLEMENTS = "implements:"

# Strips any trailing dot-segment (e.g. .Pwc, .Contoso, .Extension, .Ext)
_STRIP_DOT_SUFFIX = re.compile(r'\.\w+$')

# Maps suffix keywords (lower-cased) to relationship types.
# Used by _build_prefix_group_relationships and _connect_unlinked_by_name.
_SUFFIX_REL_TYPE_MAP: list[tuple[str, str]] = [
    ("contract",   "data_contract_for"),
    ("controller", "controlled_by"),
    ("extension",  "extends"),
    ("response",   "response_for"),
    ("request",    "request_for"),
]

# Regex to strip known role-suffixes for fuzzy name matching (derived from
# _SUFFIX_REL_TYPE_MAP so there is only one place to maintain the list).
_ROLE_SUFFIX_PATTERN = re.compile(
    r'[_.](' + '|'.join(kw for kw, _ in _SUFFIX_REL_TYPE_MAP) + r')$',
    re.IGNORECASE,
)

# All valid relationship types produced by the graph builder.
VALID_RELATIONSHIP_TYPES: list[str] = [
    "triggers", "used_in", "invokes", "has_form", "grants_access",
    "has_webresource", "uses_plugin", "extends", "reads_writes",
    "controls", "uses", "implements", "queries", "data_contract_for",
    "controlled_by", "response_for", "request_for", "related_to",
    "modifies", "produces_data_for",
]


# ---------------------------------------------------------------------------
# Fuzzy entity-name helpers
# ---------------------------------------------------------------------------

def _normalise_name(name: str) -> str:
    """Lower-case and strip trailing dot-suffix (e.g. .Pwc, .Contoso)."""
    return _STRIP_DOT_SUFFIX.sub('', name).strip().lower()


def _build_entity_lookup(entities: dict[str, KnowledgeGraphEntity]) -> dict[str, str]:
    """Return a dict mapping every plausible lower-case alias -> canonical name.

    For entity ``InventAgingTmp.Pwc`` the lookup contains:
        inventagingtmp.pwc   ->  InventAgingTmp.Pwc
        inventagingtmp       ->  InventAgingTmp.Pwc
    """
    lookup: dict[str, str] = {}
    for canonical in entities:
        low = canonical.lower()
        lookup[low] = canonical
        normalised = _normalise_name(canonical)
        if normalised and normalised != low:
            lookup.setdefault(normalised, canonical)
    return lookup


def _resolve_entity(name: str, lookup: dict[str, str]) -> str | None:
    """Try to resolve *name* to a canonical entity using the lookup."""
    if not name:
        return None
    low = name.lower()
    if low in lookup:
        return lookup[low]
    normalised = _normalise_name(name)
    if normalised in lookup:
        return lookup[normalised]
    # partial containment (entity name inside reference or vice-versa)
    for alias, canonical in lookup.items():
        if alias in normalised or normalised in alias:
            return canonical
    return None


def _build_plugin_lookup(plugins: dict[str, KnowledgeGraphPlugin]) -> dict[str, str]:
    """Lower-case plugin name -> canonical name."""
    return {p.lower(): p for p in plugins}


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_knowledge_graph(
    entities: list[Entity],
    workflows: list[Workflow],
    plugins: list[Plugin],
    forms: list[str],
    roles: list[Role] | None = None,
    webresources: list[WebResource] | None = None,
    form_details: list[FormDetail] | None = None,
    # ----- AX / F&O extras -----
    ax_classes_data: list[dict] | None = None,
    ax_report_data: list[dict] | None = None,
    ax_query_data: list[dict] | None = None,
) -> KnowledgeGraph:
    roles = roles or []
    webresources = webresources or []
    form_details = form_details or []
    ax_classes_data = ax_classes_data or []
    ax_report_data = ax_report_data or []
    ax_query_data = ax_query_data or []

    kg_entities: dict[str, KnowledgeGraphEntity] = {}
    kg_workflows: dict[str, KnowledgeGraphWorkflow] = {}
    kg_plugins: dict[str, KnowledgeGraphPlugin] = {}
    kg_roles: dict[str, KnowledgeGraphRole] = {}
    kg_webresources: dict[str, KnowledgeGraphWebResource] = {}
    relationships: list[Relationship] = []

    # ------------------------------------------------------------------
    # 1. Populate node dicts
    # ------------------------------------------------------------------

    for entity in entities:
        kg_entities[entity.name] = KnowledgeGraphEntity(
            fields=[f.name for f in entity.fields],
            fieldDetails=[
                KnowledgeGraphFieldDetail(
                    name=f.name,
                    type=f.type,
                    displayName=f.displayName,
                    required=f.required,
                )
                for f in entity.fields
            ],
            forms=entity.forms[:],
            workflows=[],
            plugins=[],
        )

    for wf in workflows:
        kg_workflows[wf.name] = KnowledgeGraphWorkflow(
            trigger=wf.trigger,
            triggerEntity=wf.triggerEntity,
            mode=wf.mode,
            scope=wf.scope,
            steps=wf.steps,
            conditions=wf.conditions,
            plugins=wf.plugins[:],
            relatedEntities=wf.relatedEntities[:],
        )

    for plugin in plugins:
        kg_plugins[plugin.name] = KnowledgeGraphPlugin(
            triggerEntity=plugin.triggerEntity,
            operation=plugin.operation,
            stage=plugin.stage,
            executionMode=plugin.executionMode,
            executionOrder=plugin.executionOrder,
            filteringAttributes=plugin.filteringAttributes,
            assemblyName=plugin.assemblyName,
            description=plugin.description,
        )

    # --- AX Reports -> added as Entity nodes so they appear on the graph ---
    for report in ax_report_data:
        rname = report.get("name", "")
        if rname and rname not in kg_entities:
            ds_fields = [
                KnowledgeGraphFieldDetail(name=ds, type="DataSource")
                for ds in report.get("data_sources", [])
            ]
            param_fields = [
                KnowledgeGraphFieldDetail(name=p, type="Parameter")
                for p in report.get("parameters", [])
            ]
            kg_entities[rname] = KnowledgeGraphEntity(
                fields=[f.name for f in ds_fields + param_fields],
                fieldDetails=ds_fields + param_fields,
                forms=[],
                workflows=[],
                plugins=[],
            )

    # --- AX Queries -> added as Entity nodes ---
    for query in ax_query_data:
        qname = query.get("name", "")
        if qname and qname not in kg_entities:
            ds_fields = [
                KnowledgeGraphFieldDetail(name=ds, type="DataSource")
                for ds in query.get("data_sources", [])
            ]
            kg_entities[qname] = KnowledgeGraphEntity(
                fields=[f.name for f in ds_fields],
                fieldDetails=ds_fields,
                forms=[],
                workflows=[],
                plugins=[],
            )

    # ------------------------------------------------------------------
    # 2. Build lookups for relationship matching
    # ------------------------------------------------------------------

    entity_lookup = _build_entity_lookup(kg_entities)
    plugin_lookup = _build_plugin_lookup(kg_plugins)

    # ------------------------------------------------------------------
    # 3. CRM-style relationships (workflow <-> entity, plugin <-> entity ...)
    # ------------------------------------------------------------------

    for wf in workflows:
        linked_entities: set[str] = set()

        if wf.triggerEntity:
            resolved = _resolve_entity(wf.triggerEntity, entity_lookup)
            if resolved:
                if resolved in kg_entities and wf.name not in kg_entities[resolved].workflows:
                    kg_entities[resolved].workflows.append(wf.name)
                relationships.append(Relationship(source=resolved, target=wf.name, type="triggers"))
                linked_entities.add(resolved)

        for rel_entity in (wf.relatedEntities or []):
            if not rel_entity:
                continue
            resolved = _resolve_entity(rel_entity, entity_lookup)
            if not resolved or resolved in linked_entities:
                continue
            linked_entities.add(resolved)
            if resolved in kg_entities and wf.name not in kg_entities[resolved].workflows:
                kg_entities[resolved].workflows.append(wf.name)
            relationships.append(Relationship(source=resolved, target=wf.name, type="used_in"))

    for plugin in plugins:
        if plugin.triggerEntity:
            resolved = _resolve_entity(plugin.triggerEntity, entity_lookup)
            if resolved and resolved in kg_entities:
                kg_entities[resolved].plugins.append(plugin.name)
                relationships.append(Relationship(
                    source=resolved, target=plugin.name, type="uses_plugin",
                ))

    # Workflow <-> Plugin (same trigger entity)
    for wf in workflows:
        for plugin in plugins:
            if (plugin.triggerEntity and wf.triggerEntity and
                    plugin.triggerEntity.lower() == wf.triggerEntity.lower()):
                if plugin.name not in kg_workflows[wf.name].plugins:
                    kg_workflows[wf.name].plugins.append(plugin.name)
                relationships.append(Relationship(
                    source=wf.name, target=plugin.name, type="invokes",
                ))

    for form_name in forms:
        for ename in list(kg_entities):
            if ename.lower() in form_name.lower() or form_name.lower() in ename.lower():
                if form_name not in kg_entities[ename].forms:
                    kg_entities[ename].forms.append(form_name)
                relationships.append(Relationship(
                    source=ename, target=form_name, type="has_form",
                ))

    for fd in form_details:
        matched_entity = None
        if fd.entity:
            matched_entity = _resolve_entity(fd.entity, entity_lookup)
        if not matched_entity:
            matched_entity = _resolve_entity(fd.name, entity_lookup)
        if matched_entity and matched_entity in kg_entities:
            if fd not in kg_entities[matched_entity].formDetails:
                kg_entities[matched_entity].formDetails.append(fd)

    for role in roles:
        related_entities: list[str] = []
        for priv in role.privileges:
            priv_lower = priv.lower()
            for alias, canonical in entity_lookup.items():
                if alias in priv_lower:
                    if canonical not in related_entities:
                        related_entities.append(canonical)
                    relationships.append(Relationship(
                        source=role.name, target=canonical, type="grants_access",
                    ))
        kg_roles[role.name] = KnowledgeGraphRole(
            privileges=role.privileges[:],
            relatedEntities=related_entities,
            description=role.description,
        )

    for wr in webresources:
        related_entity = None
        if wr.relatedEntity:
            related_entity = _resolve_entity(wr.relatedEntity, entity_lookup)
            if related_entity:
                relationships.append(Relationship(
                    source=related_entity, target=wr.name, type="has_webresource",
                ))
        if not related_entity:
            wr_tokens = wr.name.lower().replace("/", " ").replace("_", " ").replace(".", " ")
            for alias, canonical in entity_lookup.items():
                if alias in wr_tokens:
                    related_entity = canonical
                    relationships.append(Relationship(
                        source=canonical, target=wr.name, type="has_webresource",
                    ))
                    break
        kg_webresources[wr.name] = KnowledgeGraphWebResource(
            type=wr.type,
            relatedEntity=related_entity,
            description=wr.description,
        )

    # ------------------------------------------------------------------
    # 4. AX / F&O heuristic relationships  (class -> class, class -> table,
    #    controller -> report, contracts -> service, name-prefix grouping)
    # ------------------------------------------------------------------

    if ax_classes_data:
        _build_ax_class_relationships(
            ax_classes_data, kg_entities, kg_plugins,
            relationships, entity_lookup, plugin_lookup,
        )

    if ax_report_data:
        _build_ax_report_relationships(
            ax_report_data, kg_plugins, relationships, plugin_lookup,
        )

    if ax_query_data:
        _build_ax_query_relationships(
            ax_query_data, kg_entities, relationships, entity_lookup,
        )

    # Name-prefix grouping for related contracts / controllers / extensions
    _build_prefix_group_relationships(kg_plugins, relationships)

    # ------------------------------------------------------------------
    # 4b. Connect still-unconnected nodes via semantic name matching
    # ------------------------------------------------------------------
    _connect_unlinked_by_name(
        kg_entities, kg_plugins, relationships, ax_classes_data,
    )

    # ------------------------------------------------------------------
    # 5. Deduplicate relationships
    # ------------------------------------------------------------------

    seen_rels: set[tuple[str, str, str]] = set()
    deduped: list[Relationship] = []
    for r in relationships:
        key = (r.source, r.target, r.type)
        if key not in seen_rels and r.source != r.target:
            seen_rels.add(key)
            deduped.append(r)
    relationships = deduped

    return KnowledgeGraph(
        entities=kg_entities,
        workflows=kg_workflows,
        plugins=kg_plugins,
        roles=kg_roles,
        webResources=kg_webresources,
        relationships=relationships,
    )


# ---------------------------------------------------------------------------
# AX class -> relationship extraction
# ---------------------------------------------------------------------------

def _build_ax_class_relationships(
    ax_classes_data: list[dict],
    kg_entities: dict[str, KnowledgeGraphEntity],
    kg_plugins: dict[str, KnowledgeGraphPlugin],
    relationships: list[Relationship],
    entity_lookup: dict[str, str],
    plugin_lookup: dict[str, str],
):
    """Extract relationships from AX class metadata (references, base_class)."""

    for cls in ax_classes_data:
        cls_name = cls.get("name", "")
        if not cls_name:
            continue

        refs = cls.get("references", [])
        base_class = cls.get("base_class")

        # --- base_class relationship ---
        if base_class:
            resolved_plugin = plugin_lookup.get(base_class.lower())
            if resolved_plugin:
                relationships.append(Relationship(
                    source=cls_name, target=resolved_plugin, type="extends",
                ))

        for ref in refs:
            if ref.startswith(_REF_EXTENSION_OF):
                target = ref.split(":", 1)[1].strip()
                # Does the extension target exist as a plugin in this solution?
                resolved_plugin = plugin_lookup.get(target.lower())
                if resolved_plugin:
                    relationships.append(Relationship(
                        source=cls_name, target=resolved_plugin, type="extends",
                    ))
                # Does the target match an entity (table/view)?
                resolved_entity = _resolve_entity(target, entity_lookup)
                if resolved_entity:
                    relationships.append(Relationship(
                        source=cls_name, target=resolved_entity, type="extends",
                    ))

            elif ref.startswith(_REF_TABLE_REF):
                table_name = ref.split(":", 1)[1].strip()
                resolved_entity = _resolve_entity(table_name, entity_lookup)
                if resolved_entity:
                    relationships.append(Relationship(
                        source=cls_name, target=resolved_entity, type="reads_writes",
                    ))
                else:
                    # table_ref might actually point to another class (e.g. new ContractClass())
                    resolved_plugin = plugin_lookup.get(table_name.lower())
                    if resolved_plugin:
                        relationships.append(Relationship(
                            source=cls_name, target=resolved_plugin, type="uses",
                        ))

            elif ref.startswith(_REF_IMPLEMENTS):
                iface = ref.split(":", 1)[1].strip()
                resolved_entity = _resolve_entity(iface, entity_lookup)
                if resolved_entity:
                    relationships.append(Relationship(
                        source=cls_name, target=resolved_entity, type="implements",
                    ))


def _build_ax_report_relationships(
    ax_report_data: list[dict],
    kg_plugins: dict[str, KnowledgeGraphPlugin],
    relationships: list[Relationship],
    plugin_lookup: dict[str, str],
):
    """Connect reports to their controllers / related classes."""
    for report in ax_report_data:
        rname = report.get("name", "")
        if not rname:
            continue

        # Convention: report "PwcInventAging" -> controller "PwcInventAgingController"
        for pname_lower, pname_canonical in plugin_lookup.items():
            if pname_lower == f"{rname.lower()}controller":
                relationships.append(Relationship(
                    source=pname_canonical, target=rname, type="controls",
                ))
            elif rname.lower() in pname_lower and "controller" in pname_lower:
                relationships.append(Relationship(
                    source=pname_canonical, target=rname, type="controls",
                ))


def _build_ax_query_relationships(
    ax_query_data: list[dict],
    kg_entities: dict[str, KnowledgeGraphEntity],
    relationships: list[Relationship],
    entity_lookup: dict[str, str],
):
    """Connect queries to their data source tables."""
    for query in ax_query_data:
        qname = query.get("name", "")
        if not qname or qname not in kg_entities:
            continue
        for table_name in query.get("related_tables", []):
            resolved = _resolve_entity(table_name, entity_lookup)
            if resolved and resolved != qname:
                relationships.append(Relationship(
                    source=qname, target=resolved, type="queries",
                ))


# ---------------------------------------------------------------------------
# Name-prefix grouping -- connect classes that share a common prefix
# ---------------------------------------------------------------------------

def _build_prefix_group_relationships(
    kg_plugins: dict[str, KnowledgeGraphPlugin],
    relationships: list[Relationship],
):
    """Group plugins by common name prefix and create edges between the
    root plugin and its related contracts / extensions.

    Example: PwcSLMInventTransSync, PwcSLMInventTransSyncContract,
    PwcSLMInventTransSyncController -> all related.
    """
    if len(kg_plugins) < 2:
        return

    plugin_names = sorted(kg_plugins.keys(), key=len)

    # Build prefix groups: short name -> list of longer names that start with it
    groups: dict[str, list[str]] = {}
    for i, base_name in enumerate(plugin_names):
        base_lower = base_name.lower()
        group: list[str] = []
        for j, other_name in enumerate(plugin_names):
            if i == j:
                continue
            other_lower = other_name.lower()
            if other_lower.startswith(base_lower) and len(other_name) > len(base_name):
                suffix = other_name[len(base_name):]
                # Accept if suffix starts at a word boundary (uppercase letter
                # or underscore) — works for any PascalCase naming convention.
                if suffix[0] == '_' or suffix[0].isupper():
                    group.append(other_name)
        if group:
            groups[base_name] = group

    # Remove groups that are subsets of larger groups
    final_groups: dict[str, list[str]] = {}
    sorted_bases = sorted(groups.keys(), key=len, reverse=True)
    used: set[str] = set()
    for base in sorted_bases:
        if base in used:
            continue
        members = groups[base]
        final_groups[base] = members
        used.add(base)
        for m in members:
            used.add(m)

    # Create relationships
    for base_name, members in final_groups.items():
        for member in members:
            suffix = member[len(base_name):]
            suffix_lower = suffix.lower().lstrip("_")
            rel_type = "related_to"  # default
            for keyword, rtype in _SUFFIX_REL_TYPE_MAP:
                if keyword in suffix_lower:
                    rel_type = rtype
                    break

            relationships.append(Relationship(
                source=member, target=base_name, type=rel_type,
            ))


# ---------------------------------------------------------------------------
# Semantic name matching -- connect unlinked nodes by shared name fragments
# ---------------------------------------------------------------------------

# Minimum length of a shared substring to consider two components related.
# 8 chars avoids false positives from short common prefixes (e.g. "Invent")
# while still matching meaningful shared stems (e.g. "inventaging" = 11 chars).
_MIN_SHARED_LEN = 8


def _split_camel(name: str) -> list[str]:
    """Split a CamelCase / PascalCase name into tokens."""
    tokens = re.sub(r'([A-Z])', r' \1', name).split()
    return [t.lower() for t in tokens if len(t) > 1]


def _connect_unlinked_by_name(
    kg_entities: dict[str, KnowledgeGraphEntity],
    kg_plugins: dict[str, KnowledgeGraphPlugin],
    relationships: list[Relationship],
    ax_classes_data: list[dict],
):
    """For any node that has zero edges, try to connect it to the most
    relevant other node using semantic name matching.

    Strategy:
    1. For unconnected plugins: check if extension_of target name shares a
       significant substring with any entity name -> 'modifies' edge.
    2. For unconnected entities: check if any plugin name contains a portion
       of the entity name -> 'modifies' edge.
    """
    connected: set[str] = set()
    for r in relationships:
        connected.add(r.source)
        connected.add(r.target)

    all_entity_names = list(kg_entities.keys())
    all_plugin_names = list(kg_plugins.keys())

    # Build extension-target map
    ext_target_map: dict[str, str] = {}  # cls_name -> extension_of target
    for cls in (ax_classes_data or []):
        cname = cls.get("name", "")
        for ref in cls.get("references", []):
            if ref.startswith(_REF_EXTENSION_OF):
                ext_target_map[cname] = ref.split(":", 1)[1].strip()

    # --- Unconnected plugins -> try to connect to entities ---
    for pname in all_plugin_names:
        if pname in connected:
            continue

        best_entity = None
        best_score = 0

        # Names to check: the plugin name itself + its extension target
        names_to_check = [pname]
        if pname in ext_target_map:
            names_to_check.append(ext_target_map[pname])

        for check_name in names_to_check:
            check_lower = _ROLE_SUFFIX_PATTERN.sub('', _normalise_name(check_name)).replace("_", "")
            for ename in all_entity_names:
                ename_base = _normalise_name(ename).replace("_", "")
                # Find longest common substring
                shared_len = _longest_common_substring_len(check_lower, ename_base)
                if shared_len >= _MIN_SHARED_LEN and shared_len > best_score:
                    best_score = shared_len
                    best_entity = ename

        if best_entity:
            relationships.append(Relationship(
                source=pname, target=best_entity, type="modifies",
            ))
            connected.add(pname)
            connected.add(best_entity)

    # --- Unconnected entities -> try to connect to plugins ---
    for ename in all_entity_names:
        if ename in connected:
            continue

        best_plugin = None
        best_score = 0

        ename_base = _normalise_name(ename).replace("_", "")
        for pname in all_plugin_names:
            pname_lower = _normalise_name(pname).replace("_extension", "").replace("_", "")
            shared_len = _longest_common_substring_len(pname_lower, ename_base)
            if shared_len >= _MIN_SHARED_LEN and shared_len > best_score:
                best_score = shared_len
                best_plugin = pname

        if best_plugin:
            relationships.append(Relationship(
                source=best_plugin, target=ename, type="modifies",
            ))
            connected.add(ename)
            connected.add(best_plugin)


def _longest_common_substring_len(a: str, b: str) -> int:
    """Return the length of the longest common substring of *a* and *b*."""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    # Optimised rolling-row DP
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best
