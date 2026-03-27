import os
import json
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.models.schemas import (
    KnowledgeGraph, DocSection, GeneratedDocs, VerificationResult, VerificationIssue
)
from backend.services.chunking_engine import (
    create_chunks, chunks_to_context, group_chunks_into_batches
)
from backend.services.knowledge_graph import VALID_RELATIONSHIP_TYPES
from datetime import datetime, timezone

logger = logging.getLogger("ai_reasoning")

PWC_MODEL = os.environ.get("PWC_GENAI_MODEL", "vertex_ai.gemini-2.5-pro")


def _get_pwc_config():
    endpoint = os.environ.get("PWC_GENAI_ENDPOINT_URL", "")
    api_key = os.environ.get("PWC_GENAI_API_KEY", "")
    bearer_token = os.environ.get("PWC_GENAI_BEARER_TOKEN", "")

    if not endpoint:
        raise RuntimeError("PWC_GENAI_ENDPOINT_URL not configured")

    chat_endpoint = endpoint.replace("/completions", "/chat/completions")

    return {
        "endpoint": chat_endpoint,
        "api_key": api_key,
        "bearer_token": bearer_token,
    }


def _call_pwc_genai(
    messages: list[dict],
    max_tokens: int = 8192,
    temperature: float = 0.3,
    timeout: float = 180,
) -> str:
    config = _get_pwc_config()

    headers = {
        "Content-Type": "application/json",
    }
    if config["bearer_token"]:
        headers["Authorization"] = f"Bearer {config['bearer_token']}"
    if config["api_key"]:
        headers["x-api-key"] = config["api_key"]

    payload = {
        "model": PWC_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    response = requests.post(
        config["endpoint"],
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        body = ""
        try:
            body = (e.response.text or "")[:8000] if e.response is not None else ""
        except Exception:
            pass
        logger.error(
            "PWC GenAI HTTP %s: %s",
            e.response.status_code if e.response is not None else "?",
            body or "(no body)",
        )
        raise RuntimeError(
            f"PWC GenAI HTTP {e.response.status_code if e.response is not None else 'error'}: "
            f"{body[:1200] if body else str(e)}"
        ) from e

    result = response.json()

    if "choices" in result and len(result["choices"]) > 0:
        choice = result["choices"][0]
        if "message" in choice and "content" in choice["message"]:
            return choice["message"]["content"]
        if "text" in choice:
            return choice["text"]

    if "content" in result:
        return result["content"]
    if "text" in result:
        return result["text"]
    if "response" in result:
        return result["response"]
    if "output" in result:
        return result["output"]

    return json.dumps(result)


def is_pwc_genai_configured() -> bool:
    """True when PWC GenAI env has an endpoint URL (auth may still fail if keys are wrong)."""
    return bool(os.environ.get("PWC_GENAI_ENDPOINT_URL", "").strip())


_TECH_SPECS_SYSTEM = """You are a principal solutions architect. You receive structured artifacts from an automated codebase analysis (knowledge graph, features, diagrams, file index, optional doc draft).

MANDATORY RULES:
1) Ground every statement in the ARTIFACTS section. Do not invent features, files, APIs, entities, or integrations that are not clearly supported by those artifacts.
2) Preserve coverage: map knowledge_graph nodes/edges and features into the appropriate sections; cite file_path values from artifacts when listing components. Do not silently drop named items—if unsure where they fit, place them in the closest section or list under key_capabilities / layers.
3) Where artifacts are silent, use null, empty arrays [], or the exact string "Not evidenced in provided artifacts" for required string fields—never fabricate detail.
4) You are summarizing evidence from the artifacts, not executing tools or reading the repo.
5) Return ONE JSON object only: no markdown code fences, no commentary—the first character must be { and the last must be }.

Reuse or adapt Mermaid text from FLOW_DIAGRAMS when it fits; otherwise you may synthesize simple Mermaid (graph TD / erDiagram) using only names that appear in the artifacts.
"""


def _bundle_section(name: str, data, max_chars: int) -> tuple[str, int]:
    if max_chars <= 0:
        return "", 0
    header = f"### {name}\n"
    if data is None:
        body = "null\n"
        return header + body, len(header) + len(body)
    if isinstance(data, str):
        body = data if len(data) <= max_chars else data[: max_chars - 80] + "\n…(truncated)…\n"
    else:
        try:
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
        except TypeError:
            raw = str(data)
        if len(raw) > max_chars:
            body = raw[: max_chars - 80] + f"\n…(truncated, was {len(raw)} chars)…\n"
        else:
            body = raw + "\n"
    block = header + body
    return block, len(block)


def _build_tech_specs_artifacts_markdown(bundle: dict, total_cap: int = 72_000) -> str:
    # Budgets are per-section ceilings; total_cap keeps the whole request under typical gateway limits.
    order = [
        ("KNOWLEDGE_GRAPH", "knowledge_graph", 26_000),
        ("FEATURES", "features", 20_000),
        ("PROJECT_TREE", "project_tree", 14_000),
        ("FEATURE_CONNECTIONS", "feature_connections", 12_000),
        ("FLOW_DIAGRAMS", "flow_diagrams", 14_000),
        ("CROSS_VALIDATION", "cross_validation", 8_000),
        ("GENERATED_MARKDOWN_DOCS", "documentation", 12_000),
        ("CODE_SNIPPET_SEED", "context_seed", 12_000),
        ("FILE_INDEX", "files", 10_000),
    ]
    parts: list[str] = []
    used = 0
    for label, key, budget in order:
        remain = total_cap - used
        if remain <= 200:
            break
        chunk, clen = _bundle_section(label, bundle.get(key), min(budget, remain))
        if clen:
            parts.append(chunk)
            used += clen
    return "## ARTIFACTS\n\n" + "\n".join(parts)


_TECH_SPECS_JSON_SHAPE = r"""
The JSON object MUST follow this shape (all top-level keys required; use [] or null where nothing is evidenced):
{
  "scope_definition": { "in_scope": [], "out_of_scope": [], "summary": "" },
  "solution_overview": { "summary": "", "tech_stack": [], "deployment_model": "", "key_capabilities": [] },
  "high_level_architecture": {
    "description": "",
    "layers": [ { "name": "", "description": "", "components": [] } ],
    "mermaid_diagram": ""
  },
  "erd": {
    "description": "",
    "entities": [ {
      "name": "", "type": "standard|custom",
      "fields": [ { "name": "", "type": "", "description": "", "is_key": false, "is_required": false } ],
      "relationships": []
    } ],
    "mermaid_diagram": ""
  },
  "standard_and_custom_entities": {
    "standard_entities": [ { "name": "", "purpose": "", "customizations": [] } ],
    "custom_entities": [ { "name": "", "purpose": "", "fields_summary": "" } ]
  },
  "business_rules": {
    "workflows": [ { "name": "", "trigger": "", "description": "", "steps": [] } ],
    "validation_rules": [],
    "automation": []
  },
  "javascript_customizations": {
    "client_scripts": [ { "name": "", "file_path": "", "purpose": "", "events_handled": [] } ],
    "web_resources": [],
    "libraries_used": []
  },
  "auth_model": {
    "authentication_method": "",
    "authorization_model": "",
    "roles": [ { "name": "", "permissions": [] } ],
    "security_features": [],
    "file_paths": []
  },
  "module_components": {
    "sales": { "components": [ { "name": "", "type": "", "description": "", "file_path": "" } ], "mermaid_diagram": "" },
    "service": { "components": [ { "name": "", "type": "", "description": "", "file_path": "" } ], "mermaid_diagram": "" },
    "marketing": { "components": [ { "name": "", "type": "", "description": "", "file_path": "" } ], "mermaid_diagram": "" }
  },
  "integration_architecture": {
    "description": "",
    "integrations": [ {
      "name": "", "type": "", "direction": "",
      "external_system": "", "description": "",
      "endpoints": [], "file_paths": []
    } ],
    "mermaid_diagram": ""
  },
  "integration_auth": {
    "mechanisms": [ {
      "integration_name": "", "auth_type": "", "description": "",
      "token_management": "", "file_paths": []
    } ]
  }
}
"""


def _tech_specs_pwc_artifact_cap() -> int:
    try:
        return max(24_000, min(int(os.environ.get("PWC_TECH_SPECS_MAX_INPUT_CHARS", "72000")), 200_000))
    except ValueError:
        return 72_000


def _tech_specs_pwc_output_tokens() -> int:
    """PWC gateways often reject very large max_tokens (e.g. 65536). Default 8192."""
    try:
        return max(1024, min(int(os.environ.get("PWC_TECH_SPECS_MAX_OUTPUT_TOKENS", "8192")), 32_768))
    except ValueError:
        return 8192


def generate_technical_specs_from_advanced_artifacts(bundle: dict) -> dict:
    """Synthesize technical-specs JSON from Advanced Docs artifacts via PWC GenAI (no repo tools).

    ``bundle`` may include: project_name, project_tree, knowledge_graph, features,
    feature_connections, flow_diagrams, cross_validation, documentation, context_seed, files.
    """
    if not is_pwc_genai_configured():
        raise RuntimeError("PWC GenAI is not configured (set PWC_GENAI_ENDPOINT_URL)")

    from backend.services.claude_analyzer import (  # noqa: PLC0415
        _parse_json_response,
    )

    artifact_cap = _tech_specs_pwc_artifact_cap()
    max_out = _tech_specs_pwc_output_tokens()

    def _one_call(
        cap: int,
        out_tokens: int,
        *,
        single_user_message: bool,
    ) -> str:
        artifacts_md = _build_tech_specs_artifacts_markdown(bundle, total_cap=cap)
        pname = bundle.get("project_name") or "Project"
        user_prompt = f"""Project name: {pname}

{artifacts_md}

## REQUIRED OUTPUT

{_TECH_SPECS_JSON_SHAPE}

Respond with ONLY the JSON object."""
        if single_user_message:
            messages = [
                {
                    "role": "user",
                    "content": _TECH_SPECS_SYSTEM + "\n\n---\n\n" + user_prompt,
                }
            ]
        else:
            messages = [
                {"role": "system", "content": _TECH_SPECS_SYSTEM},
                {"role": "user", "content": user_prompt},
            ]
        prompt_chars = sum(len(m["content"]) for m in messages)
        logger.info(
            "Technical specs (PWC): prompt ~%d chars (artifacts cap=%d), max_tokens=%d, model=%s, single_user=%s",
            prompt_chars,
            cap,
            out_tokens,
            PWC_MODEL,
            single_user_message,
        )
        return _call_pwc_genai(
            messages,
            max_tokens=out_tokens,
            temperature=0.12,
            timeout=600,
        )

    try:
        raw = _one_call(artifact_cap, max_out, single_user_message=False)
    except RuntimeError as first_err:
        cause = first_err.__cause__
        is_400 = isinstance(cause, requests.HTTPError) and getattr(
            cause.response, "status_code", None
        ) == 400
        if not is_400 and "HTTP 400" not in str(first_err):
            raise
        logger.warning(
            "Technical specs (PWC): retrying after 400 with reduced input and single user message"
        )
        smaller_cap = max(28_000, artifact_cap // 2)
        smaller_out = max(2048, min(max_out, 8192))
        raw = _one_call(smaller_cap, smaller_out, single_user_message=True)

    try:
        return _parse_json_response(
            raw,
            expected_keys=(
                ("scope_definition", "solution_overview", "high_level_architecture"),
                ("scope_definition", "solution_overview"),
            ),
        )
    except ValueError:
        repair_out = min(max_out, 8192)
        repair = _call_pwc_genai(
            [
                {"role": "user", "content": "Return only valid JSON. No markdown code fences.\n\n"
                    "Fix into a single JSON object (technical spec schema). Preserve facts.\n\n" + raw[:28000]},
            ],
            max_tokens=repair_out,
            temperature=0,
            timeout=420,
        )
        return _parse_json_response(
            repair,
            expected_keys=(
                ("scope_definition", "solution_overview", "high_level_architecture"),
                ("scope_definition", "solution_overview"),
            ),
        )


# ---------------------------------------------------------------------------
# LLM‑based knowledge‑graph enrichment
# ---------------------------------------------------------------------------

# Caps to keep the LLM prompt within context-window limits
_MAX_FIELDS_IN_PROMPT = 20
_MAX_METHODS_IN_PROMPT = 20
_PROMPT_MAX_CHARS = 60_000
_LLM_GRAPH_MAX_TOKENS = 4096


def enrich_knowledge_graph_with_llm(
    graph: "KnowledgeGraph",
    ax_classes_data: list[dict] | None = None,
    ax_report_data: list[dict] | None = None,
) -> "KnowledgeGraph":
    """Call the LLM to discover relationships between components and merge
    them into the existing KnowledgeGraph.

    The LLM receives a compact summary of ALL nodes (entities, plugins,
    reports) and their metadata.  It returns a JSON array of relationships
    that are grounded strictly in what is present in the metadata — nothing
    fabricated.
    """
    from backend.models.schemas import Relationship  # local import to avoid circular

    ax_classes_data = ax_classes_data or []
    ax_report_data = ax_report_data or []

    # ---- Build compact component summary for the LLM ----
    components: dict = {
        "entities": {},
        "plugins": {},
        "reports": [],
    }

    for name, info in graph.entities.items():
        components["entities"][name] = {
            "fields": info.fields[:_MAX_FIELDS_IN_PROMPT],
        }

    for name, info in graph.plugins.items():
        entry: dict = {
            "triggerEntity": info.triggerEntity,
            "description": info.description,
            "operation": info.operation,
            "stage": info.stage,
        }
        components["plugins"][name] = entry

    # Merge rich class data into plugin entries
    cls_lookup = {c["name"]: c for c in ax_classes_data if c.get("name")}
    for pname in list(components["plugins"]):
        cls = cls_lookup.get(pname)
        if cls:
            components["plugins"][pname]["references"] = cls.get("references", [])
            methods = [m["name"] for m in cls.get("methods", [])]
            components["plugins"][pname]["methods"] = methods[:_MAX_METHODS_IN_PROMPT]
            if cls.get("base_class"):
                components["plugins"][pname]["base_class"] = cls["base_class"]

    for report in ax_report_data:
        components["reports"].append({
            "name": report.get("name", ""),
            "data_sources": report.get("data_sources", []),
            "parameters": report.get("parameters", []),
        })

    # Also include the existing heuristic relationships so the LLM knows
    # what is already covered and can focus on what's missing.
    existing_rels = [
        {"source": r.source, "target": r.target, "type": r.type}
        for r in graph.relationships
    ]

    component_json = json.dumps(components, indent=2, default=str)
    existing_rels_json = json.dumps(existing_rels, indent=2)

    # Cap the prompt if the component json is very large
    if len(component_json) > _PROMPT_MAX_CHARS:
        component_json = component_json[:_PROMPT_MAX_CHARS] + "\n... (truncated)"

    valid_types_str = ", ".join(VALID_RELATIONSHIP_TYPES)

    system_prompt = f"""You are an expert D365 / X++ code analyst.  You are given a JSON
summary of ALL components in a solution (entities/tables, classes/plugins, reports).
Your job is to identify EVERY meaningful relationship between these components.

RULES:
- ONLY use component names that appear in the provided JSON. Do NOT invent components.
- Each relationship must have: source, target, type, label.
  - source / target: EXACT component name from the JSON.
  - type: one of: {valid_types_str}
  - label: a short (3-6 word) human-readable description of the relationship.
- Base your relationships on:
  - "extension_of" references (class extends another class or entity)
  - "table_ref" references (class reads/writes a table/entity)
  - "base_class" inheritance
  - Controller ↔ Report naming conventions
  - Contract ↔ Service naming conventions (e.g. *Contract → *Service)
  - Common-prefix grouping (e.g. PwcSLM* classes form one integration)
  - Report data_sources referencing tables
- DO NOT fabricate business logic. Only describe structural relationships.
- Include ALL relationships — do not skip any. Every component should have
  at least one relationship if possible.

Return ONLY a JSON object with this structure (no markdown fences, no explanation):
{{"relationships": [{{"source":"...", "target":"...", "type":"...", "label":"..."}}]}}"""

    user_prompt = f"""## Components
{component_json}

## Existing relationships (already detected by heuristics)
{existing_rels_json}

Identify ALL additional relationships not yet in the existing list.
Also include any corrections to the existing relationships if needed.
Return the COMPLETE set of relationships (existing + new)."""

    try:
        raw = _call_pwc_genai(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=_LLM_GRAPH_MAX_TOKENS,
        )

        # Parse the LLM response
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        data = json.loads(raw)
        llm_rels = data.get("relationships", [])
    except Exception:
        # If LLM call fails, just return the graph as-is with heuristic edges
        return graph

    # ---- Merge LLM relationships into the graph ----
    # Build set of valid node names (entities + plugins + workflows)
    valid_nodes: set[str] = set()
    valid_nodes.update(graph.entities.keys())
    valid_nodes.update(graph.plugins.keys())
    valid_nodes.update(graph.workflows.keys())

    seen: set[tuple[str, str, str]] = {
        (r.source, r.target, r.type) for r in graph.relationships
    }
    new_rels: list[Relationship] = list(graph.relationships)

    for rel in llm_rels:
        src = rel.get("source", "")
        tgt = rel.get("target", "")
        rtype = rel.get("type", "related_to")
        if not src or not tgt or src == tgt:
            continue
        # Only accept relationships between known nodes
        if src not in valid_nodes or tgt not in valid_nodes:
            continue
        key = (src, tgt, rtype)
        if key not in seen:
            seen.add(key)
            new_rels.append(Relationship(source=src, target=tgt, type=rtype))

    # Return a new graph with the enriched relationships
    from backend.models.schemas import KnowledgeGraph as KG
    return KG(
        entities=graph.entities,
        workflows=graph.workflows,
        plugins=graph.plugins,
        roles=graph.roles,
        webResources=graph.webResources,
        relationships=new_rels,
    )


SECTION_CONFIGS = {
    # ===== 1. Overview =====
    "doc_purpose": {
        "title": "1.1 Purpose of the Document",
        "order": 1,
        "prompt": (
            "Generate a 'Purpose of the Document' section for this Microsoft Dynamics solution technical design document.\n\n"
            "Write 2-3 concise paragraphs covering:\n"
            "- The purpose of this document (to describe the technical design of the solution)\n"
            "- What the solution does at a high level (derive from the component names and metadata)\n"
            "- What this document covers (entities/tables, workflows, plugins/classes, forms, security roles, integrations)\n\n"
            "Keep it professional, concise, and formal — suitable for an enterprise technical design document.\n"
            "Base ALL statements on the actual solution metadata provided. Do NOT fabricate capabilities."
        ),
    },
    "intended_audience": {
        "title": "1.2 Intended Audience",
        "order": 2,
        "prompt": (
            "Generate an 'Intended Audience' section for this technical design document.\n\n"
            "List the target readers in a table:\n"
            "| Role | Purpose |\n"
            "Include roles such as: Solution Architects, Developers, Functional Consultants, "
            "QA/Test Engineers, Project Managers, and Support Team.\n"
            "For each role, write one sentence explaining why they would read this document.\n\n"
            "Keep it concise and professional."
        ),
    },
    # ===== 2. Design Overview =====
    "requirement_description": {
        "title": "2.1 Requirement Description",
        "order": 3,
        "prompt": (
            "Generate a 'Requirement Description' section based on the solution metadata.\n\n"
            "Analyze ALL components (entities, plugins, workflows, forms, reports) and derive the "
            "business requirements that this solution addresses. Present them as:\n\n"
            "### Business Requirements\n"
            "| Req # | Requirement | Components Involved | Priority |\n\n"
            "Derive each requirement from the actual components — e.g., if there are custom entities "
            "for tracking something, that implies a requirement to track that data.\n\n"
            "### Functional Requirements\n"
            "List the functional requirements as bullet points grouped by area "
            "(Data Management, Process Automation, Reporting, Integration, Security).\n\n"
            "RULES:\n"
            "- Derive requirements ONLY from actual components in the metadata.\n"
            "- Use EXACT component names when referencing them.\n"
            "- Keep descriptions concise and business-oriented.\n"
            "- Do NOT fabricate requirements that have no supporting components."
        ),
    },
    "functional_design_overview": {
        "title": "2.2 Functional Design Overview",
        "order": 4,
        "prompt": (
            "Generate a 'Functional Design Overview' for this solution.\n\n"
            "### Solution Summary\n"
            "State the solution name, total component counts (entities, workflows, plugins, "
            "roles, forms, web resources) using EXACT numbers from the metadata.\n\n"
            "### Functional Areas\n"
            "Group the solution's components into logical functional areas. For each area:\n"
            "- **Area Name** (derived from component naming patterns)\n"
            "- **Purpose**: 1-2 sentences explaining what this area does\n"
            "- **Key Components**: List the entities, plugins, and workflows in this area\n\n"
            "### Component Interaction Summary\n"
            "Create a table showing how the main components interact:\n"
            "| Component | Type | Interacts With | Interaction Description |\n\n"
            "RULES:\n"
            "- Use EXACT component names from the metadata.\n"
            "- Group components logically based on naming patterns and relationships.\n"
            "- Do NOT invent interactions not supported by the metadata."
        ),
    },
    "architectural_diagram": {
        "title": "2.3 Process Detailed Architectural Diagram",
        "order": 5,
        "prompt": (
            "Generate a 'Process Detailed Architectural Diagram' section.\n\n"
            "### System Architecture Overview\n"
            "Describe the solution architecture in 2-3 paragraphs: what layers exist, "
            "how data flows between entities/tables, plugins/classes, and workflows.\n\n"
            "### Architecture Diagram\n"
            "Generate a Mermaid diagram (`flowchart TD`) showing the solution architecture:\n"
            "- Group components into layers: Data Layer (entities/tables), Business Logic (plugins/classes), "
            "Process Layer (workflows), and Presentation (forms)\n"
            "- Show connections between layers\n"
            "- Use QUOTED labels: `nodeId[\"Label text\"]`\n"
            "- NEVER use `{{ }}` or `<br>` in labels\n"
            "- Wrap in a ```mermaid code fence\n\n"
            "### Component Architecture Table\n"
            "| Layer | Component | Type | Dependencies | Description |\n\n"
            "RULES:\n"
            "- Use EXACT component names from the metadata.\n"
            "- Keep the diagram clean and readable — group related items.\n"
            "- Base architecture ONLY on the actual solution structure."
        ),
    },
    "process_flow_description": {
        "title": "2.4 Process Flow Description",
        "order": 6,
        "prompt": (
            "Generate a 'Process Flow Description' section for this solution.\n\n"
            "Identify the main business processes in the solution by analyzing workflows, "
            "plugins, and their trigger entities. For EACH process:\n\n"
            "### Process: [Process Name]\n"
            "- **Trigger**: What initiates this process\n"
            "- **Flow Steps**:\n"
            "| Step | Action | Component | Data Affected |\n"
            "- **Output**: What the process produces\n\n"
            "### Mermaid Flow Diagram\n"
            "Generate a Mermaid flowchart for the main process flow:\n"
            "- Use QUOTED labels: `nodeId[\"Label text\"]`\n"
            "- Label every edge with what happens\n"
            "- NEVER use `{{ }}` or `<br>` in labels\n"
            "- Wrap in a ```mermaid code fence\n\n"
            "RULES:\n"
            "- Base flows ONLY on actual component relationships in the metadata.\n"
            "- Use EXACT component names.\n"
            "- Do NOT fabricate processing logic — describe at the component interaction level."
        ),
    },
    # ===== 3. Detailed Technical Design =====
    "action_menu_items": {
        "title": "3.1 Action Menu Items",
        "order": 7,
        "prompt": (
            "Generate an 'Action Menu Items' section for this solution.\n\n"
            "List all action menu items, buttons, and entry points detected in the solution metadata. "
            "If the metadata contains forms, list any action panes or menu items associated with them.\n\n"
            "| # | Menu Item Name | Type (Action / Display / Output) | Target Form/Entity | Description |\n\n"
            "If no explicit menu items are detected, derive likely menu items from:\n"
            "- Forms that exist in the solution (each form implies navigation menu items)\n"
            "- Reports (each report implies a menu item to run it)\n"
            "- Batch jobs / workflows with manual triggers\n\n"
            "RULES:\n"
            "- Use EXACT names from the metadata.\n"
            "- Clearly mark derived items as 'Inferred from [component]'.\n"
            "- Keep descriptions concise."
        ),
    },
    "tables": {
        "title": "3.2 Tables",
        "order": 8,
        "prompt": (
            "Generate a detailed 'Tables' section listing ALL entities/tables in this solution.\n\n"
            "### Table Summary\n"
            "| # | Table Name | Display Name | Field Count | Related Workflows | Related Plugins |\n\n"
            "### Table Details\n"
            "For EACH entity/table, create a sub-section:\n"
            "#### [Table Name]\n"
            "- **Display Name**: (if available)\n"
            "- **Purpose**: 1 sentence derived from the table name and its fields\n"
            "- **Fields**:\n"
            "| # | Field Name | Display Name | Data Type | Description |\n"
            "List ALL fields with their types. For 'Description', write a brief purpose based on the field name.\n"
            "- **Related Components**: List any workflows, plugins, or forms that reference this table\n\n"
            "RULES:\n"
            "- List EVERY entity/table in the metadata — nothing should be missing.\n"
            "- Use EXACT field names and types from the metadata.\n"
            "- Keep descriptions factual and derived from names only."
        ),
    },
    "forms": {
        "title": "3.3 Forms",
        "order": 9,
        "prompt": (
            "Generate a 'Forms' section documenting all forms in this solution.\n\n"
            "### Form Summary\n"
            "| # | Form Name | Related Entity | Type (Main / Lookup / Dialog) | Description |\n\n"
            "### Form Details\n"
            "For each form, describe:\n"
            "- **Entity**: Which table/entity this form is bound to\n"
            "- **Purpose**: What the form is used for (derived from name and entity)\n"
            "- **Key Fields Displayed**: List the main fields shown on this form (based on entity fields)\n"
            "- **Related Plugins/Workflows**: Any business logic triggered from this form\n\n"
            "If no explicit forms exist in the metadata, state 'No custom forms detected in the solution. "
            "Standard system forms are used for the entities listed in Section 3.2.'\n\n"
            "RULES:\n"
            "- Use EXACT names from the metadata.\n"
            "- Do NOT fabricate form layouts — only describe what the metadata shows."
        ),
    },
    "classes": {
        "title": "3.4 Classes",
        "order": 10,
        "prompt": (
            "Generate a detailed 'Classes' section documenting all plugins, extensions, and classes.\n\n"
            "### Class Summary\n"
            "| # | Class Name | Type (Plugin / Extension / Controller / Contract / Service / Helper) "
            "| Target Entity | Trigger (Create / Update / Delete) | Stage (Pre / Post) |\n\n"
            "### Class Details\n"
            "For EACH class/plugin, create a sub-section:\n"
            "#### [Class Name]\n"
            "- **Type**: Plugin / Extension / etc.\n"
            "- **Target Entity**: Which entity this operates on\n"
            "- **Operation**: Create / Update / Delete / Custom\n"
            "- **Stage**: Pre-operation / Post-operation\n"
            "- **Description**: 2-3 sentences explaining what this class does (derived from its name, "
            "target, and any metadata description)\n"
            "- **Dependencies**: Other classes or entities this depends on\n\n"
            "RULES:\n"
            "- List EVERY plugin/class in the metadata.\n"
            "- Use EXACT names from the metadata.\n"
            "- Keep descriptions derived from available metadata — do NOT fabricate business logic."
        ),
    },
    "digital_signature_utility": {
        "title": "3.5 Digital Signature Utility",
        "order": 11,
        "prompt": (
            "Generate a 'Digital Signature Utility' section.\n\n"
            "Analyze the solution metadata for any components related to:\n"
            "- Digital signatures or electronic signatures\n"
            "- Approval workflows\n"
            "- Document signing or verification\n"
            "- Authentication or authorization plugins\n"
            "- Audit trail or compliance tracking\n\n"
            "If such components exist, document them:\n"
            "| # | Component | Type | Purpose | Related Entity |\n\n"
            "Describe how the digital signature / approval process works based on the component flow.\n\n"
            "If NO digital signature or approval components are detected, state:\n"
            "'No dedicated digital signature utility components were detected in this solution. "
            "If digital signature functionality is required, it would need to be implemented as a "
            "separate customization or through a third-party integration.'\n\n"
            "RULES:\n"
            "- Only document what exists in the metadata.\n"
            "- Do NOT fabricate digital signature capabilities."
        ),
    },
    # ===== 4. Component Overview =====
    "component_overview": {
        "title": "4. Component Overview",
        "order": 12,
        "prompt": (
            "Generate a 'Component Overview' section for this solution.\n\n"
            "Provide a comprehensive overview of every component in the solution. "
            "Group them by type (Entities/Tables, Plugins/Classes, Workflows, Forms, "
            "Web Resources, Security Roles, Reports, etc.).\n\n"
            "### Component Inventory\n"
            "| # | Component Name | Type | Purpose | Status (Custom / Extended / Standard) |\n\n"
            "### Component Statistics\n"
            "Provide a summary table with counts:\n"
            "| Component Type | Count | Custom | Extended |\n\n"
            "### Key Components\n"
            "Highlight the most important components (those with the most relationships "
            "or that serve as central integration points) and explain their significance "
            "in 1-2 sentences each.\n\n"
            "RULES:\n"
            "- List EVERY component found in the metadata — nothing should be omitted.\n"
            "- Use EXACT component names from the metadata.\n"
            "- Derive purpose descriptions from component names and relationships only.\n"
            "- Do NOT fabricate components or capabilities."
        ),
    },
    # ===== 5. How Everything Links Together =====
    "how_everything_links": {
        "title": "5. How Everything Links Together",
        "order": 13,
        "prompt": (
            "Generate a 'How Everything Links Together' section for this solution.\n\n"
            "Analyze the relationships between ALL components and explain how the solution "
            "works as a cohesive whole.\n\n"
            "### Relationship Map\n"
            "Generate a Mermaid diagram (`flowchart LR`) showing how the major components "
            "connect to each other:\n"
            "- Entities linked to their plugins and workflows\n"
            "- Forms linked to their backing entities\n"
            "- Plugins linked to other plugins they depend on\n"
            "- Use QUOTED labels: `nodeId[\"Label text\"]`\n"
            "- NEVER use `{{ }}` or `<br>` in labels\n"
            "- Wrap in a ```mermaid code fence\n\n"
            "### Integration Points\n"
            "| Source Component | Target Component | Relationship Type | Description |\n\n"
            "### Data Flow Summary\n"
            "Describe the end-to-end data flow through the solution: \n"
            "where data enters, how it is processed by plugins/classes, "
            "which workflows act on it, and where results are stored or displayed.\n\n"
            "### Dependency Chain\n"
            "List the critical dependency chains — if component A depends on B which depends on C, "
            "show those chains so developers understand the impact of changes.\n\n"
            "RULES:\n"
            "- Base ALL relationships on actual metadata — do NOT invent connections.\n"
            "- Use EXACT component names.\n"
            "- Keep the Mermaid diagram clean and readable."
        ),
    },
    # ===== 6. Features =====
    "feature_list": {
        "title": "6. Features",
        "order": 14,
        "prompt": (
            "Generate a 'Features' section with a comprehensive Feature List for this solution.\n\n"
            "Analyze all components (entities, plugins, workflows, forms, web resources, roles) "
            "and derive the distinct features / capabilities this solution provides.\n\n"
            "### Feature List\n"
            "| # | Feature Name | Description | Components Involved | Category |\n\n"
            "Group features by category (e.g., Data Management, Automation, Reporting, "
            "Security, Integration, User Interface).\n\n"
            "### Feature Details\n"
            "For each feature, provide:\n"
            "#### [Feature Name]\n"
            "- **Category**: (e.g., Data Management, Automation)\n"
            "- **Description**: 2-3 sentences explaining what this feature does for the business\n"
            "- **Components**: List the specific entities, plugins, workflows involved\n"
            "- **User Impact**: How end-users interact with or benefit from this feature\n\n"
            "RULES:\n"
            "- Derive features ONLY from actual components in the metadata.\n"
            "- Use EXACT component names when referencing them.\n"
            "- Each feature must be traceable to at least one component.\n"
            "- Do NOT fabricate features that have no supporting components."
        ),
    },
    # ===== 7. Feature Flows =====
    "feature_flows": {
        "title": "7. Feature Flows",
        "order": 15,
        "prompt": (
            "Generate a 'Feature Flows' section documenting how each feature operates end-to-end.\n\n"
            "For EACH major feature identified in the solution, create a detailed flow:\n\n"
            "### Feature Flow: [Feature Name]\n"
            "- **Trigger**: What initiates this feature (user action, system event, scheduled job)\n"
            "- **Preconditions**: What must be true before this feature executes\n"
            "- **Flow Steps**:\n"
            "| Step # | Action | Component | Input | Output |\n"
            "- **Postconditions**: What is true after the feature completes\n"
            "- **Error Handling**: How failures are handled (if detectable from metadata)\n\n"
            "### Mermaid Sequence Diagram\n"
            "For each major feature, generate a Mermaid sequence diagram:\n"
            "```mermaid\n"
            "sequenceDiagram\n"
            "    participant User\n"
            "    participant Form\n"
            "    participant Plugin\n"
            "    participant Entity\n"
            "```\n\n"
            "Show the interaction between the user, forms, plugins/classes, and entities/tables "
            "for each feature flow.\n\n"
            "RULES:\n"
            "- Base ALL flows on actual component relationships in the metadata.\n"
            "- Use EXACT component names.\n"
            "- Use QUOTED labels in Mermaid: `nodeId[\"Label text\"]`\n"
            "- NEVER use `{{ }}` or `<br>` in Mermaid labels.\n"
            "- Do NOT fabricate processing steps — describe at the component interaction level."
        ),
    },
}


def generate_documentation(
    solution_id: str,
    graph: KnowledgeGraph,
    requested_sections: list[str] | None = None
) -> GeneratedDocs:
    chunks = create_chunks(graph)
    batches = group_chunks_into_batches(chunks)

    sections_to_generate = requested_sections if requested_sections else list(SECTION_CONFIGS.keys())

    doc_sections = []
    for section_key in sections_to_generate:
        if section_key not in SECTION_CONFIGS:
            continue

        config = {**SECTION_CONFIGS[section_key], "key": section_key}
        order = config.get("order", 99)
        section = _generate_section_batched(batches, config, graph, order)
        doc_sections.append(section)

    return GeneratedDocs(
        solutionId=solution_id,
        generatedAt=datetime.now(timezone.utc).isoformat(),
        sections=doc_sections,
        verified=False
    )


def generate_single_section(
    solution_id: str,
    graph: KnowledgeGraph,
    section_key: str,
) -> DocSection:
    """Generate a single documentation section with full chunking support.

    This is designed to be called per-section via a separate API call,
    enabling incremental / parallel doc generation from the frontend.
    """
    if section_key not in SECTION_CONFIGS:
        raise ValueError(f"Unknown section key: {section_key}. Available: {list(SECTION_CONFIGS.keys())}")

    config = {**SECTION_CONFIGS[section_key], "key": section_key}
    order = config.get("order", 99)

    # Build chunks & batches specifically for this section
    chunks = create_chunks(graph, section_key=section_key)
    batches = group_chunks_into_batches(chunks)

    return _generate_section_batched(batches, config, graph, order)


# ---------------------------------------------------------------------------
# Section suppression: return minimal content when no evidence exists
# ---------------------------------------------------------------------------

def _has_real_workflow_steps(graph: KnowledgeGraph) -> bool:
    """Check if ANY workflow in the graph has real extractable steps (not just file refs)."""
    for wdata in graph.workflows.values():
        for step in wdata.steps:
            step_lower = step.lower().strip()
            if step_lower.startswith("[no_detailed_steps]"):
                continue
            if step_lower.startswith("process defined in "):
                continue
            if step_lower.startswith("xaml definition:"):
                continue
            if step_lower.startswith("workflow defined in "):
                continue
            if step_lower.startswith("xaml workflow in "):
                continue
            if step_lower == "workflow processing":
                continue
            if step_lower.startswith("business process flow"):
                continue
            return True
    return False


def _has_real_plugin_descriptions(graph: KnowledgeGraph) -> bool:
    """Check if ANY plugin has a meaningful description beyond its name."""
    for pname, pdata in graph.plugins.items():
        if (pdata.description and pdata.description.strip()
                and pdata.description.strip().lower() != pname.lower()
                and pdata.description.strip() != "N/A"):
            return True
    return False


def _check_section_suppression(section_key: str, graph: KnowledgeGraph) -> str | None:
    """Return minimal content string if section should be suppressed, None otherwise.

    With the 15-section / 7-group documentation structure, all sections are always generated.
    """
    return None


# ---------------------------------------------------------------------------
# Max workers for parallel batch calls — keep modest to avoid rate-limits
# ---------------------------------------------------------------------------
_MAX_PARALLEL_BATCHES = 4


def _generate_section_batched(
    batches: list[list[dict]],
    config: dict,
    graph: KnowledgeGraph,
    order: int,
) -> DocSection:
    """Generate a doc section handling arbitrary-size solutions.

    - If everything fits in a single batch → one AI call (fast path).
    - If multiple batches → process each batch in parallel, then run a
      synthesis pass that merges partial results into one coherent section.
    - If the section has NO supporting evidence, return a minimal "no data" response.
    """

    section_key = config.get("key", "")

    # --- Section suppression: skip AI call if no data supports this section ---
    suppressed = _check_section_suppression(section_key, graph)
    if suppressed:
        slug = config.get("key", config["title"].lower().replace(" ", "_"))
        return DocSection(
            title=config["title"], slug=slug,
            content=suppressed, order=order,
        )

    graph_summary = _compact_graph_summary(graph)

    if len(batches) <= 1:
        # ---------- Fast path: single batch, no merging needed ----------
        context = chunks_to_context(batches[0]) if batches else ""
        return _call_section_ai(context, config, graph_summary, order)

    # ---------- Multi-batch: parallel partial generation ----------------
    partial_results: list[tuple[int, str]] = []

    def _process_batch(batch_idx: int, batch: list[dict]) -> tuple[int, str]:
        context = chunks_to_context(batch)
        batch_label = f"(batch {batch_idx + 1}/{len(batches)})"
        prompt = (
            f"{config['prompt']}\n\n"
            f"NOTE: You are processing {batch_label} of the full solution metadata. "
            f"Focus only on the components present in this batch. "
            f"Do NOT add an introduction or conclusion — the output will be merged later.\n\n"
            f"## Solution Knowledge Graph Summary\n{graph_summary}\n\n"
            f"## Detailed Metadata Chunks {batch_label}\n{context}"
        )
        try:
            content = _call_pwc_genai(
                messages=[
                    {"role": "system", "content": _SECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=8192,
            )
            return (batch_idx, content or "")
        except Exception as e:
            return (batch_idx, f"<!-- batch {batch_idx + 1} error: {e} -->")

    with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_BATCHES, len(batches))) as pool:
        futures = {
            pool.submit(_process_batch, i, b): i
            for i, b in enumerate(batches)
        }
        for future in as_completed(futures):
            partial_results.append(future.result())

    # Sort by batch index to maintain deterministic order
    partial_results.sort(key=lambda x: x[0])
    partial_texts = [text for _, text in partial_results if text.strip()]

    if not partial_texts:
        slug = config.get("key", config["title"].lower().replace(" ", "_"))
        return DocSection(
            title=config["title"], slug=slug,
            content=f"# {config['title']}\n\nNo content generated.", order=order,
        )

    # If we only ended up with one non-empty result, skip synthesis
    if len(partial_texts) == 1:
        slug = config.get("key", config["title"].lower().replace(" ", "_"))
        return DocSection(
            title=config["title"], slug=slug,
            content=partial_texts[0], order=order,
        )

    # ---------- Synthesis pass: merge partial results --------------------
    merged_content = _synthesize_partials(config, partial_texts, graph_summary)

    slug = config.get("key", config["title"].lower().replace(" ", "_"))
    return DocSection(
        title=config["title"], slug=slug,
        content=merged_content, order=order,
    )


def _call_section_ai(
    context: str, config: dict, graph_summary: str, order: int
) -> DocSection:
    """Single-call section generation (used when context fits one batch)."""
    user_prompt = (
        f"{config['prompt']}\n\n"
        f"## Solution Knowledge Graph Summary\n{graph_summary}\n\n"
        f"## Detailed Metadata Chunks\n{context}"
    )
    try:
        content = _call_pwc_genai(
            messages=[
                {"role": "system", "content": _SECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=8192,
        )
        if not content or not content.strip():
            content = f"# {config['title']}\n\nNo content generated."
    except Exception as e:
        content = f"# {config['title']}\n\nDocumentation generation encountered an error: {str(e)}"

    slug = config.get("key", config["title"].lower().replace(" ", "_"))
    return DocSection(title=config["title"], slug=slug, content=content, order=order)


def _synthesize_partials(
    config: dict, partials: list[str], graph_summary: str
) -> str:
    """Merge multiple partial doc outputs into one coherent section."""
    numbered_parts = "\n\n---\n\n".join(
        f"### Partial Result {i + 1}\n{text}" for i, text in enumerate(partials)
    )

    synthesis_prompt = f"""You were given a large solution that was split into {len(partials)} batches.
Below are the partial documentation outputs for the "{config['title']}" section.

Your job:
1. Merge all partial outputs into ONE coherent, well-structured markdown section.
2. Remove duplicate content — if the same entity/workflow/plugin appears in multiple partials, consolidate.
3. Keep ALL unique information — do not drop any details from any partial.
4. Maintain a logical ordering (e.g., group entities together, workflows together).
5. Do NOT add an introduction or conclusion that contains any claims not present in the partials.
6. Do NOT invent any information not present in the partials.
7. Do NOT add generic advice, best practices, or recommendations not in the partials.

## Solution Knowledge Graph Summary
{graph_summary}

## Partial Outputs to Merge
{numbered_parts}"""

    try:
        content = _call_pwc_genai(
            messages=[
                {"role": "system", "content": _SECTION_SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_prompt},
            ],
            max_tokens=8192,
        )
        return content or f"# {config['title']}\n\nMerge produced no output."
    except Exception as e:
        # Fallback: just concatenate the partials with separators
        return f"# {config['title']}\n\n" + "\n\n---\n\n".join(partials)


def _compact_graph_summary(graph: KnowledgeGraph) -> str:
    """Build a detailed JSON summary of the full knowledge graph including field metadata and evidence flags."""

    # Helper to check if workflow steps are just file references (no real step data)
    def _has_real_steps(steps: list[str]) -> bool:
        if not steps:
            return False
        for step in steps:
            step_lower = step.lower().strip()
            if step_lower.startswith("[no_detailed_steps]"):
                continue
            if step_lower.startswith("process defined in "):
                continue
            if step_lower.startswith("xaml definition:"):
                continue
            if step_lower.startswith("workflow defined in "):
                continue
            if step_lower.startswith("xaml workflow in "):
                continue
            if step_lower == "workflow processing":
                continue
            if step_lower.startswith("business process flow"):
                continue
            # If we get here, at least one step is real
            return True
        return False

    # Helper to classify step content
    def _classify_steps(steps: list[str]) -> str:
        if not steps:
            return "EMPTY"
        for step in steps:
            step_lower = step.lower().strip()
            if step_lower.startswith("[no_detailed_steps]"):
                return "FILE_REFERENCE_ONLY"
            if step_lower.startswith("process defined in "):
                return "FILE_REFERENCE_ONLY"
            if step_lower.startswith("xaml definition:"):
                return "FILE_REFERENCE_ONLY"
        # Steps exist but they are just NAMES, not actual business logic
        return "STEP_NAMES_ONLY"

    # Count actual forms across all entities
    total_forms = set()
    entity_details = {}
    for ename, edata in graph.entities.items():
        entity_forms = list(set(edata.forms))  # deduplicate
        for f in entity_forms:
            total_forms.add(f)

        entity_details[ename] = {
            "field_count": len(edata.fields),
            "fields": edata.fields,
            "field_details": [
                {
                    "name": fd.name,
                    "type": fd.type,
                    "displayName": fd.displayName,
                    "required": fd.required,
                }
                for fd in edata.fieldDetails
            ] if edata.fieldDetails else [],
            "forms": entity_forms,
            "form_details": [
                {
                    "name": fdet.name,
                    "entity": fdet.entity,
                    "tabs": fdet.tabs,
                    "sections": fdet.sections,
                    "controls": fdet.controls,
                    "sourceFile": fdet.sourceFile,
                }
                for fdet in edata.formDetails
            ] if edata.formDetails else [],
            "workflows": edata.workflows,
            "plugins": edata.plugins,
        }

    workflow_details = {}
    workflows_with_real_steps = 0
    workflows_with_only_file_refs = 0
    for wname, wdata in graph.workflows.items():
        has_real = _has_real_steps(wdata.steps)
        step_type = _classify_steps(wdata.steps)
        if has_real:
            workflows_with_real_steps += 1
        else:
            workflows_with_only_file_refs += 1

        workflow_details[wname] = {
            "trigger": wdata.trigger,
            "triggerEntity": wdata.triggerEntity,
            "mode": wdata.mode,
            "scope": wdata.scope,
            "steps": wdata.steps,
            "step_content_type": step_type,
            "_CRITICAL_step_warning": (
                "These 'steps' are LABELS/NAMES extracted from XML, NOT actual business logic. "
                "Do NOT elaborate them into detailed processes. "
                "A step named 'Validate data' just means there is a step called 'Validate data' — "
                "you do NOT know WHAT it validates, HOW it validates, or WHAT conditions it checks. "
                "Do NOT invent validation rules, thresholds, or specific field checks."
            ) if step_type == "STEP_NAMES_ONLY" else (
                "Steps contain only file references — NO step logic was extractable from metadata."
            ),
            "conditions": wdata.conditions,
            "_CRITICAL_condition_warning": (
                "These 'conditions' are LABELS extracted from XML, NOT actual business rules. "
                "A condition named 'Revenue exceeds $100,000' is just the NAME of a condition branch — "
                "it does NOT prove this is the actual threshold. Do NOT cite this as a real business rule."
            ) if wdata.conditions else None,
            "plugins": wdata.plugins,
            "relatedEntities": wdata.relatedEntities,
        }

    plugin_details = {}
    plugins_with_descriptions = 0
    for pname, pdata in graph.plugins.items():
        has_desc = bool(pdata.description and pdata.description.strip()
                       and pdata.description.strip().lower() != pname.lower()
                       and pdata.description.strip() != "N/A")

        if has_desc:
            plugins_with_descriptions += 1

        plugin_details[pname] = {
            "triggerEntity": pdata.triggerEntity,
            "operation": pdata.operation,
            "stage": pdata.stage,
            "executionMode": pdata.executionMode,
            "executionOrder": pdata.executionOrder,
            "filteringAttributes": pdata.filteringAttributes,
            "assemblyName": pdata.assemblyName,
            "description": pdata.description,
            "_CRITICAL_description_warning": (
                "This description is REGISTRATION METADATA, not source code. "
                "It may mention topics like 'revenue checks' or 'validation' but you do NOT know: "
                "(1) the actual validation rules, (2) the thresholds, (3) the specific fields checked, "
                "(4) the error messages, or (5) any other implementation details. "
                "The plugin .dll source code is NOT available. Do NOT fabricate implementation details."
            ),
        }

    role_details = {}
    for rname, rdata in graph.roles.items():
        role_details[rname] = {
            "privileges": rdata.privileges,
            "relatedEntities": rdata.relatedEntities,
            "description": rdata.description,
        }

    return json.dumps({
        "_documentation_accuracy_rules": {
            "ABSOLUTE_RULE_1": "You have METADATA only. You do NOT have source code, business requirements docs, or implementation details.",
            "ABSOLUTE_RULE_2": "Step NAMES are not step LOGIC. 'Validate account data' is a LABEL — you do NOT know what it validates.",
            "ABSOLUTE_RULE_3": "Condition NAMES are not business RULES. 'Revenue > $100k' as a condition NAME does NOT prove that threshold exists.",
            "ABSOLUTE_RULE_4": "Plugin descriptions summarize purpose — they do NOT reveal implementation. Do NOT invent validation rules or field checks.",
            "ABSOLUTE_RULE_5": "When documenting workflows, state: 'The workflow contains steps named X, Y, Z' — NOT 'The workflow performs X, Y, Z'.",
            "form_count_exact": f"EXACTLY {len(total_forms)} unique forms: {sorted(total_forms)}",
            "workflow_evidence": f"{workflows_with_real_steps} workflow(s) have step names. {workflows_with_only_file_refs} workflow(s) have only file references.",
            "plugin_evidence": f"{plugins_with_descriptions} plugin(s) have descriptions. Descriptions are summaries, NOT implementation details.",
        },
        "entity_count": len(graph.entities),
        "workflow_count": len(graph.workflows),
        "plugin_count": len(graph.plugins),
        "form_count": len(total_forms),
        "form_names": sorted(total_forms),
        "role_count": len(graph.roles),
        "webresource_count": len(graph.webResources),
        "entities": entity_details,
        "workflows": workflow_details,
        "plugins": plugin_details,
        "roles": role_details,
        "webresource_names": list(graph.webResources.keys()),
    }, indent=2)


_SECTION_SYSTEM_PROMPT = """You are a Microsoft Dynamics 365 Solution Architect and documentation specialist.

You are generating one section of a professional Technical Design Document
that follows this 11-section / 3-group structure:

1. Overview
   1.1 Purpose of the Document
   1.2 Intended Audience
2. Design Overview
   2.1 Requirement Description
   2.2 Functional Design Overview
   2.3 Process Detailed Architectural Diagram
   2.4 Process Flow Description
3. Detailed Technical Design
   3.1 Action Menu Items
   3.2 Tables
   3.3 Forms
   3.4 Classes
   3.5 Digital Signature Utility

Based on the provided solution metadata and knowledge graph relationships,
generate clear, well-structured documentation for the requested section.
Write in a way that is accessible to non-technical readers while remaining accurate.

███████████████████████████████████████████████████████████████████████████████
█ CRITICAL: STEP NAMES ARE NOT BUSINESS LOGIC — DO NOT ELABORATE                █
███████████████████████████████████████████████████████████████████████████████

The metadata contains STEP NAMES and LABELS, NOT actual business logic:

• A step named "Validate account data" means there EXISTS a step with that NAME.
  You do NOT know: WHAT it validates, HOW it validates, or WHAT the rules are.
  WRONG: "The workflow validates account revenue exceeds threshold before approval"
  RIGHT: "The workflow contains a step named 'Validate account data'"

• A condition named "Revenue > $100,000" is a LABEL, not a proven business rule.
  WRONG: "The system requires revenue to exceed $100,000 for approval"
  RIGHT: "The workflow contains a condition branch labeled 'Revenue > $100,000'"

• A plugin named "AccountValidationPlugin" with description "validates account data"
  tells you its PURPOSE, not its IMPLEMENTATION.
  WRONG: "Validates that all required fields are populated and revenue meets threshold"
  RIGHT: "Plugin executes on Account Update (Pre-operation). Description: 'validates account data'"

• You have METADATA, not SOURCE CODE. You cannot see inside plugins or workflows.

═══════════════════════════════════════════════════════════════
ABSOLUTE RULES — VIOLATION OF ANY RULE IS A CRITICAL FAILURE
═══════════════════════════════════════════════════════════════

RULE 1 — ZERO FABRICATION POLICY:
  You must ONLY document what is EXPLICITLY present in the metadata JSON.
  If a piece of information does not appear as a concrete value in the metadata,
  you MUST NOT include it. This means:
  - Do NOT invent business logic (e.g., "revenue > $100,000 triggers approval")
  - Do NOT invent workflow steps beyond what is listed in the "steps" array
  - Do NOT invent plugin behavior beyond what the "description" field says
  - Do NOT invent email notifications, approval processes, task creation, or any
    specific actions unless they appear in the steps/conditions arrays
  - Do NOT invent integration endpoints, APIs, authentication methods, or payloads
  - Do NOT invent test conditions or thresholds not found in the metadata

RULE 2 — EVIDENCE CITATION:
  Every claim must be traceable to a specific metadata field. When describing
  a component, reference ONLY the actual metadata values provided.

RULE 3 — STEP NAMES ARE LABELS, NOT LOGIC:
  The "steps" array contains step NAMES/LABELS extracted from XML.
  These are NOT executable business logic. You must NEVER elaborate on them.
  If step = "Validate account data" → write: "Contains step labeled 'Validate account data'"
  Do NOT write: "Validates account fields" or "Checks that revenue exceeds threshold"
  You do NOT know WHAT the step does, HOW it works, or WHAT rules it applies.
  If steps contain only "[NO_DETAILED_STEPS]..." or a file reference,
  state: "Workflow step details not available in solution metadata."

RULE 4 — CONDITION NAMES ARE LABELS, NOT RULES:
  The "conditions" array contains condition LABELS, NOT proven business rules.
  If condition = "Revenue exceeds $100,000" → write: "Contains condition labeled 'Revenue exceeds $100,000'"
  Do NOT write: "Revenue must exceed $100,000" or "The threshold is $100,000"
  The label might not reflect the actual implementation.

RULE 5 — PLUGIN DESCRIPTIONS ARE SUMMARIES, NOT SOURCE CODE:
  Plugin metadata contains ONLY registration data (entity, message, stage).
  The "description" field is a registration summary, NOT code analysis.
  If description = "validates revenue and duplicates" →
  write: "Description states: 'validates revenue and duplicates'"
  Do NOT write: "Validates that revenue field exceeds minimum value and
  checks for duplicate records based on account name"
  You do NOT know the validation rules, thresholds, or fields involved.

RULE 6 — FORM ACCURACY:
  List ONLY forms whose names appear in the metadata. The exact form count
  and form names MUST match the metadata. Do NOT create forms that don't exist
  (e.g., do NOT add "Quick Create" forms unless they appear in the metadata).

RULE 7 — CONDITIONAL SECTION GENERATION:
  If a section has NO supporting evidence in the metadata, generate ONLY
  a brief statement: "No [component type] detected in the solution metadata."
  Do NOT fill empty sections with speculative content.

RULE 8 — ENTITY FIELD ACCURACY:
  Use the EXACT field details from the metadata including name, type,
  required status, and display name. Do NOT change any field's required
  status from what the metadata states.

RULE 9 — INTEGRATION HONESTY:
  For integrations, ONLY document what is EXPLICITLY evidenced by:
  (a) a plugin description that mentions an external system by name, OR
  (b) a workflow step that explicitly references an external system.
  Component NAMES alone (e.g., "notification_service") are NOT sufficient
  evidence of an external integration. If no concrete evidence exists,
  state: "No external integration points detected in solution metadata."

RULE 10 — TEST CASE CONSTRAINTS:
  Test cases must ONLY verify that components trigger and execute without error.
  CORRECT: "Test that [workflow name] triggers on [entity] [event] and completes"
  WRONG: "Test that revenue validation rejects amounts below $100,000"
  You do NOT know what any component does internally. You can only test that it fires.

RULE 11 — COUNTS MUST BE EXACT:
  All component counts (entities, workflows, plugins, forms, roles, web
  resources) MUST exactly match the counts in the knowledge graph summary.
  Count from the metadata, do not estimate or round.

RULE 12 — NO GENERIC CONTENT:
  Do NOT include standard templates, generic best practices, or boilerplate
  advice that is not specific to the actual components in this solution.
  Every sentence must reference a specific component found in the metadata.
  If there is nothing metadata-backed to say, state: "No data available in solution metadata."

GENERAL FORMATTING:
- Return well-structured markdown with headers (##, ###), bullet points, and tables.
- Be thorough — cover EVERY component present in the metadata.
- Use professional CRM consulting language.
- Every statement must reference specific metadata. Do NOT write generic introductions or conclusions.
- NEVER use "[TO BE COMPLETED]" or placeholder text.
- NEVER include generic advice, best practices, or recommendations not tied to a specific component."""


def verify_documentation(
    solution_id: str,
    graph: KnowledgeGraph,
    docs: GeneratedDocs
) -> VerificationResult:
    graph_data = graph.model_dump()
    doc_content = "\n\n---\n\n".join([s.content for s in docs.sections])
    graph_json = json.dumps(graph_data, indent=2, default=str)

    # If the combined payload is small enough, verify in one shot.
    # Otherwise split into per-section verification and aggregate.
    single_shot_limit = 80000  # chars

    if len(graph_json) + len(doc_content) <= single_shot_limit:
        return _verify_single(solution_id, graph_json, doc_content)

    # ---------- Chunked verification ----------------------------------
    section_results: list[dict] = []

    def _verify_one_section(section) -> dict:
        section_json = json.dumps(graph_data, indent=2, default=str)[:40000]
        prompt = f"""## Knowledge Graph Data (abridged)
{section_json}

## Documentation Section: {section.title}
{section.content}"""
        try:
            result_text = _call_pwc_genai(
                messages=[
                    {"role": "system", "content": _VERIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2048,
            )
            return _parse_verification_json(result_text, section.title)
        except Exception as e:
            return {
                "score": 0.0, "verified": False,
                "issues": [{"severity": "error", "section": section.title, "message": str(e)}],
                "summary": str(e),
            }

    with ThreadPoolExecutor(max_workers=min(4, len(docs.sections))) as pool:
        futures = {pool.submit(_verify_one_section, s): s for s in docs.sections}
        for future in as_completed(futures):
            section_results.append(future.result())

    # Aggregate scores and issues
    all_issues = []
    total_score = 0.0
    for r in section_results:
        total_score += r.get("score", 0.0)
        all_issues.extend(r.get("issues", []))

    avg_score = total_score / len(section_results) if section_results else 0.0
    verified = avg_score >= 0.7 and not any(i.get("severity") == "error" for i in all_issues)

    issues = [
        VerificationIssue(
            severity=i.get("severity", "info"),
            section=i.get("section", "general"),
            message=i.get("message", ""),
        )
        for i in all_issues
    ]

    return VerificationResult(
        solutionId=solution_id,
        verified=verified,
        score=round(avg_score, 3),
        issues=issues,
        summary=f"Verification completed across {len(section_results)} sections. Average score: {avg_score:.2f}",
    )


_VERIFICATION_SYSTEM_PROMPT = """You are a documentation verification specialist for Microsoft Dynamics solutions.

Compare the generated documentation against the knowledge graph data.
Identify any inconsistencies, missing information, or inaccuracies.

SPECIFICALLY CHECK FOR THESE HALLUCINATION PATTERNS:
1. **Fabricated business logic**: Does the documentation describe specific business rules, thresholds,
   conditions, or data transformations that do NOT appear anywhere in the knowledge graph data?
   Examples: revenue thresholds, approval hierarchies, email notification triggers, scoring formulas.
2. **Incorrect counts**: Does the documentation state a different number of forms, fields, entities,
   workflows, or plugins than what the knowledge graph shows?
3. **Fabricated integrations**: Does the documentation describe specific API endpoints, external system
   connections, authentication methods, or data payloads that are NOT evidenced in the knowledge graph?
4. **Invented workflow steps**: Does the documentation describe detailed workflow steps when the knowledge
   graph shows only file references or '[NO_DETAILED_STEPS]' placeholders?
5. **Synthetic test cases with invented data**: Do test cases reference specific field values, thresholds,
   or business conditions not present in any workflow step or plugin description?

For each hallucination found, create an issue with severity "error".

Return your analysis as JSON with this structure:
{
    "score": 0.0 to 1.0 (accuracy score — deduct 0.1 for each hallucination found),
    "verified": true/false (false if any hallucinations detected),
    "issues": [
        {"severity": "error|warning|info", "section": "section_name", "message": "description"}
    ],
    "summary": "Overall assessment including hallucination count"
}"""


def _verify_single(solution_id: str, graph_json: str, doc_content: str) -> VerificationResult:
    """Verify everything in a single AI call (small solutions)."""
    user_prompt = f"""## Knowledge Graph Data
{graph_json}

## Generated Documentation
{doc_content}"""

    try:
        result_text = _call_pwc_genai(
            messages=[
                {"role": "system", "content": _VERIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
        )
        parsed = _parse_verification_json(result_text)
        issues = [
            VerificationIssue(
                severity=i.get("severity", "info"),
                section=i.get("section", "general"),
                message=i.get("message", ""),
            )
            for i in parsed.get("issues", [])
        ]
        return VerificationResult(
            solutionId=solution_id,
            verified=parsed.get("verified", False),
            score=parsed.get("score", 0.0),
            issues=issues,
            summary=parsed.get("summary", "Verification completed"),
        )
    except Exception as e:
        return VerificationResult(
            solutionId=solution_id,
            verified=False,
            score=0.0,
            issues=[VerificationIssue(severity="error", section="general", message=f"Verification failed: {str(e)}")],
            summary=f"Verification process encountered an error: {str(e)}",
        )


def _parse_verification_json(result_text: str, default_section: str = "general") -> dict:
    """Extract the JSON object from the AI's verification response."""
    result_text = result_text.strip()
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1]
        result_text = result_text.rsplit("```", 1)[0]
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        return {
            "score": 0.0,
            "verified": False,
            "issues": [{"severity": "warning", "section": default_section, "message": "Could not parse verification response"}],
            "summary": "Verification response was not valid JSON",
        }
