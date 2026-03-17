import os
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.models.schemas import (
    KnowledgeGraph, DocSection, GeneratedDocs, VerificationResult, VerificationIssue
)
from backend.services.chunking_engine import (
    create_chunks, chunks_to_context, group_chunks_into_batches
)
from datetime import datetime, timezone


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


def _call_pwc_genai(messages: list[dict], max_tokens: int = 8192) -> str:
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
        "temperature": 0.3,
    }

    response = requests.post(
        config["endpoint"],
        headers=headers,
        json=payload,
        timeout=180,
    )
    response.raise_for_status()

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


SECTION_CONFIGS = {
    # --- 1. Executive Summary / Solution Overview ---
    "executive_summary": {
        "title": "Executive Summary / Solution Overview",
        "order": 1,
        "prompt": (
            "Generate an Executive Summary for this Microsoft Dynamics CRM solution based STRICTLY on the metadata provided. Include:\n"
            "- Solution name, version, and publisher (from solution metadata)\n"
            "- Summary of what the solution contains: list the exact entity count, workflow count, plugin count, form count, role count\n"
            "- Business context inferred ONLY from entity names and workflow names actually present\n"
            "- High-level architecture: list the actual components (entities, plugins, workflows) by name\n"
            "- Key stakeholders: ONLY if security roles are present in the metadata, list them; otherwise state 'No security roles detected'\n"
            "- DO NOT fabricate business objectives or benefits not supported by the metadata\n"
            "Present this as a professional executive-level overview."
        ),
    },
    # --- 2. Business Requirements Document (BRD) ---
    "business_requirements": {
        "title": "Business Requirements Document (BRD)",
        "order": 2,
        "prompt": (
            "Generate a Business Requirements Document based STRICTLY on the solution metadata. Include:\n"
            "- Business objectives inferred ONLY from the entities, workflows, and plugins actually present\n"
            "- Stakeholders: list ONLY from security roles found in metadata; if none, state 'No security roles detected'\n"
            "- Business workflows: describe ONLY the workflows found, with their exact trigger entity and trigger event\n"
            "- Functional requirements table: FR-01, FR-02, etc. — derive requirements ONLY from actual workflows and plugins\n"
            "  Format: | ID | Description | Related Component | Source |\n"
            "  The Source column must reference the specific workflow/plugin name from the metadata\n"
            "- Non-functional requirements: state ONLY what can be determined from the solution structure\n"
            "  (e.g., if plugins use Pre-operation stage, note data validation requirement)\n"
            "DO NOT fabricate requirements not evidenced by the metadata."
        ),
    },
    # --- 3. Functional Design Document (FDD) ---
    "functional_design": {
        "title": "Functional Design Document (FDD)",
        "order": 3,
        "prompt": (
            "Generate a Functional Design Document based STRICTLY on the metadata. Include:\n"
            "- Module breakdown: group entities by their relationships (shared workflows/plugins)\n"
            "- Entity relationships: describe ONLY lookup fields (Type=Lookup) found in entity field metadata\n"
            "- Business process flows: describe ONLY the actual workflows with their exact steps and conditions from metadata\n"
            "- Forms: list ONLY forms that appear in the metadata. For each form, state which entity it belongs to and its source file\n"
            "  DO NOT fabricate forms for entities that have no forms listed\n"
            "- Dashboards: ONLY if detected in metadata; otherwise state 'No dashboards detected in solution'\n"
            "- Security roles mapping: ONLY if roles are present in metadata\n"
            "Use table format: | Feature | Dynamics Component | Component Type | Description |"
        ),
    },
    # --- 4. Technical Design Document (TDD) ---
    "technical_design": {
        "title": "Technical Design Document (TDD)",
        "order": 4,
        "prompt": (
            "Generate a Technical Design Document based STRICTLY on the metadata. Include:\n"
            "### Plugin Registration Table (CRITICAL — use EXACT metadata values)\n"
            "| Plugin Name | Assembly | Target Entity | Message | Stage | Execution Mode | Execution Order | Filtering Attributes | Description |\n"
            "For each plugin, populate ALL columns from the metadata. If a value is not present, write 'N/A'.\n\n"
            "### Workflow Definition Table\n"
            "| Workflow Name | Trigger Entity | Trigger Event | Mode | Steps | Conditions |\n"
            "List ALL workflows with their exact steps and conditions from the metadata.\n\n"
            "### Components Summary\n"
            "| Component Type | Name | Target Entity | Description |\n"
            "List every plugin, workflow, web resource found in the metadata.\n\n"
            "### Integration Points\n"
            "Document ONLY integrations explicitly evidenced in plugin descriptions or workflow step names.\n"
            "For each, state: 'Inferred from: [exact component name and description]'\n"
            "DO NOT fabricate REST API endpoints, Azure services, authentication methods, or middleware unless they appear in the metadata.\n"
            "If no integration evidence is found, state: 'No external integration endpoints detected in solution metadata.'"
        ),
    },
    # --- 5. Data Model Documentation ---
    "data_model": {
        "title": "Data Model Documentation",
        "order": 5,
        "prompt": (
            "Generate comprehensive Data Model Documentation based STRICTLY on the metadata. Include:\n"
            "### Entity Schema Tables (CRITICAL — use EXACT field metadata)\n"
            "For EACH entity, generate a table with these EXACT columns:\n"
            "| Field Name | Display Name | Type | Required | Description |\n"
            "Populate from the field_details in the metadata. The 'Required' column MUST match the metadata exactly (true/false).\n"
            "DO NOT change any field's required status from what the metadata states.\n\n"
            "### Entity Purpose Summary\n"
            "| Entity | Display Name | Field Count | Forms | Related Workflows | Related Plugins |\n\n"
            "### Lookup Relationships\n"
            "List ONLY fields with Type=Lookup as entity relationships. Show: Source Entity → Field Name → Target Entity (inferred from field name).\n"
            "DO NOT fabricate relationships not evidenced by lookup fields.\n\n"
            "### Option Set Fields\n"
            "List ONLY fields with Type=Picklist or Type=OptionSet found in the metadata.\n"
            "If none found, state: 'No option set fields detected.'"
        ),
    },
    # --- 6. Integration Documentation ---
    "integration": {
        "title": "Integration Documentation",
        "order": 6,
        "prompt": (
            "Generate Integration Documentation based STRICTLY on what the metadata evidences. Include:\n"
            "### Detected Integration Points\n"
            "Scan plugin descriptions and workflow step names for references to external systems.\n"
            "For EACH detected reference, document:\n"
            "| Integration Point | Evidence Source | Component Type | Component Name | Inferred System |\n\n"
            "For example, if a workflow step says 'Sync to external CRM', document it as:\n"
            "| External CRM Sync | Workflow step name | Workflow | Contact Synchronization | External CRM |\n\n"
            "### Important Disclaimer\n"
            "State clearly: 'The following integration points are INFERRED from component names and descriptions. "
            "Actual integration details (endpoints, authentication, payloads) are not present in the solution metadata "
            "and must be verified with the development team.'\n\n"
            "DO NOT fabricate:\n"
            "- API endpoint URLs\n"
            "- Authentication methods or credentials\n"
            "- Message formats or data payloads\n"
            "- Azure service connections\n"
            "- Middleware configurations\n"
            "If no integration evidence is found, state: 'No external integration points detected in solution metadata.'"
        ),
    },
    # --- 7. Customization Documentation ---
    "customization": {
        "title": "Customization Documentation",
        "order": 7,
        "prompt": (
            "Generate Customization Documentation listing ALL custom components from the metadata. Include:\n"
            "### Plugins\n"
            "| Plugin Name | Target Entity | Message | Stage | Execution Mode | Execution Order | Assembly | Description |\n"
            "Populate ALL columns from metadata. Use 'N/A' for missing values.\n\n"
            "### Custom Workflows\n"
            "| Workflow Name | Trigger Entity | Trigger Event | Mode | Step Count | Has Conditions |\n\n"
            "### Forms\n"
            "| Form Name | Entity | Source File | Tab Count | Section Count | Control Count |\n"
            "List ONLY forms found in the metadata. DO NOT add forms for entities that have none.\n\n"
            "### Web Resources\n"
            "| Resource Name | Type | Related Entity | Description |\n"
            "List ONLY from metadata. If none detected, state 'No web resources detected.'\n\n"
            "### Custom Entities\n"
            "List all entities found in the solution with their field counts."
        ),
    },
    # --- 8. Security Model ---
    "security_model": {
        "title": "Security Model",
        "order": 8,
        "prompt": (
            "Generate Security Model documentation based STRICTLY on the metadata.\n\n"
            "### Security Roles Found in Solution\n"
            "If security roles are present in the metadata, for each role document:\n"
            "| Role Name | Description | Privilege Count | Privileges |\n"
            "List the actual privileges extracted from the metadata.\n\n"
            "### Role-Entity Access (from metadata only)\n"
            "If role privilege names reference entity names, create a mapping table:\n"
            "| Role | Entity | Privileges |\n"
            "ONLY populate this if the privilege names in the metadata contain entity references.\n\n"
            "If NO security roles are found in the metadata, state clearly:\n"
            "'No security roles were detected in the solution metadata. "
            "Security configuration may exist in the Dynamics environment but is not included in this solution package.'\n\n"
            "DO NOT fabricate:\n"
            "- Business units\n"
            "- Teams\n"
            "- Field security profiles\n"
            "- Access control models\n"
            "unless they are explicitly present in the metadata."
        ),
    },
    # --- 9. Deployment Documentation ---
    "deployment": {
        "title": "Deployment Documentation",
        "order": 9,
        "prompt": (
            "Generate Deployment Documentation. Include:\n"
            "### Solution Package Details (from metadata)\n"
            "- Solution Name / Unique Name: from solution metadata\n"
            "- Version: from solution metadata\n"
            "- Publisher: from solution metadata\n"
            "- Managed/Unmanaged: from solution metadata (if detected)\n"
            "- Dependencies: from solution metadata (if detected)\n\n"
            "### Solution Components Count\n"
            "| Component Type | Count |\n"
            "List exact counts from the metadata: entities, workflows, plugins, forms, roles, web resources.\n\n"
            "### Standard Deployment Checklist\n"
            "Provide a standard Dynamics CRM deployment checklist applicable to this solution.\n\n"
            "### Pre-deployment Validation\n"
            "Based on the ACTUAL plugins and workflows found, list specific pre-deployment checks:\n"
            "- For each plugin: verify assembly registration\n"
            "- For each workflow: verify trigger entity exists\n"
            "Use actual component names from the metadata."
        ),
    },
    # --- 10. Testing Documentation ---
    "testing": {
        "title": "Testing Documentation",
        "order": 10,
        "prompt": (
            "Generate Testing Documentation with structured test cases derived from ACTUAL solution components.\n\n"
            "### Test Plan Overview\n"
            "- Scope: testing of the components found in this solution\n"
            "- List the specific entities, workflows, and plugins to be tested by name\n\n"
            "### Test Cases from Workflows\n"
            "For EACH workflow in the metadata, generate 1-2 test cases:\n\n"
            "| Test ID | Workflow | Scenario | Preconditions | Steps | Expected Result | Type |\n"
            "|---------|----------|----------|---------------|-------|-----------------|------|\n\n"
            "Use the actual workflow name, trigger entity, trigger event, steps, and conditions.\n"
            "The test steps should reference the actual workflow steps from the metadata.\n\n"
            "### Test Cases from Plugins\n"
            "For EACH plugin in the metadata, generate 1-2 test cases:\n\n"
            "| Test ID | Plugin | Scenario | Entity | Message | Expected Result | Type |\n"
            "|---------|--------|----------|--------|---------|-----------------|------|\n\n"
            "Use the actual plugin name, target entity, message, and stage.\n\n"
            "### Test Coverage Matrix\n"
            "| Component | Component Type | Test IDs | Coverage |\n"
            "Map each workflow and plugin to its test case IDs.\n\n"
            "IMPORTANT: Use clean, well-formatted markdown tables. Do NOT generate malformed or truncated tables."
        ),
    },
    # --- 11. Support & Operations Guide ---
    "support_operations": {
        "title": "Support & Operations Guide",
        "order": 11,
        "prompt": (
            "Generate a Support & Operations Guide based on the ACTUAL solution components.\n\n"
            "### Component Health Monitoring\n"
            "For each plugin found in the metadata, provide monitoring guidance:\n"
            "| Plugin Name | Entity | Message | Stage | What to Monitor |\n\n"
            "### Workflow Monitoring\n"
            "For each workflow, provide operational guidance:\n"
            "| Workflow Name | Trigger Entity | Trigger | What to Monitor |\n\n"
            "### Troubleshooting Guide\n"
            "For each plugin and workflow, generate a troubleshooting entry:\n"
            "| Component | Type | Common Issue | Resolution Steps |\n"
            "Base the issues on the actual component type (e.g., Pre-operation plugin → validation failures,\n"
            "Post-operation plugin → async processing issues).\n\n"
            "### Logging\n"
            "Describe standard Dynamics CRM logging applicable to the detected components:\n"
            "- Plugin Trace Log for plugins\n"
            "- System Jobs for async workflows\n"
            "DO NOT fabricate Azure or Application Insights references unless evidenced in metadata."
        ),
    },
    # --- 12. User Guide ---
    "user_guide": {
        "title": "User Guide",
        "order": 12,
        "prompt": (
            "Generate a User Guide for business users based on the ACTUAL solution components.\n\n"
            "For EACH entity in the metadata:\n"
            "### [Entity Name]\n"
            "- Purpose (inferred from entity name and fields)\n"
            "- Key fields: list the ACTUAL fields from metadata with their display names and whether they are required\n"
            "- Available forms: list ONLY forms found in the metadata for this entity. If no forms, state 'No custom forms detected'\n"
            "- Related workflows: list the ACTUAL workflows that trigger on this entity\n\n"
            "### Business Process Guides\n"
            "For each workflow, provide a user-friendly step-by-step guide using the ACTUAL workflow steps from metadata.\n\n"
            "### FAQ\n"
            "Generate FAQs based on ACTUAL solution components (e.g., 'What happens when I update an Account?' → reference the actual plugins/workflows that trigger on Account Update).\n\n"
            "Write in end-user-friendly language. Use actual field names and form names from the metadata."
        ),
    },
    # --- 13. Solution Inventory ---
    "solution_inventory": {
        "title": "Solution Inventory",
        "order": 13,
        "prompt": (
            "Generate a complete Solution Inventory listing EVERY component from the metadata.\n\n"
            "### Entities\n"
            "| # | Entity Name | Display Name | Field Count | Required Fields | Forms |\n"
            "For each entity, count fields with required=true and list form names.\n\n"
            "### Entity Field Details\n"
            "For EACH entity, generate a sub-table:\n"
            "#### [Entity Name] Fields\n"
            "| Field Name | Display Name | Type | Required |\n"
            "Use EXACT values from field_details in the metadata.\n\n"
            "### Plugins\n"
            "| # | Plugin Name | Target Entity | Message | Stage | Execution Mode | Description |\n\n"
            "### Workflows\n"
            "| # | Workflow Name | Trigger Entity | Trigger Event | Mode | Step Count | Condition Count |\n\n"
            "### Forms\n"
            "| # | Form Name | Entity | Source File |\n"
            "List ONLY forms from the metadata.\n\n"
            "### Web Resources\n"
            "| # | Name | Type | Related Entity | Description |\n"
            "If none, state 'No web resources detected.'\n\n"
            "### Security Roles\n"
            "| # | Role Name | Privilege Count | Description |\n"
            "If none, state 'No security roles detected.'\n\n"
            "This inventory must be EXHAUSTIVE — every component in the metadata must appear."
        ),
    },
    # --- 14. Configuration & Environment Details ---
    "environment_config": {
        "title": "Configuration & Environment Details",
        "order": 14,
        "prompt": (
            "Generate Configuration & Environment Details based on the solution metadata.\n\n"
            "### Solution Package Information\n"
            "| Property | Value |\n"
            "| Solution Name | [from metadata] |\n"
            "| Version | [from metadata] |\n"
            "| Publisher | [from metadata] |\n"
            "| Managed | [from metadata, or 'Not specified'] |\n"
            "| Description | [from metadata] |\n"
            "| Dependencies | [from metadata, or 'None detected'] |\n\n"
            "### Component Summary\n"
            "| Component Type | Count | Details |\n"
            "Use actual counts from the metadata.\n\n"
            "### Environment Deployment Matrix\n"
            "Provide a standard Dev/Test/UAT/Prod matrix template.\n\n"
            "### Plugin Registration Requirements\n"
            "For each plugin found, document the registration requirements using actual metadata:\n"
            "| Plugin | Assembly | Entity | Message | Stage | Mode |\n\n"
            "DO NOT fabricate Azure configuration, secrets, or integration endpoints."
        ),
    },
    # --- 15. Change Log / Version History ---
    "change_log": {
        "title": "Change Log / Version History",
        "order": 15,
        "prompt": (
            "Generate a Change Log / Version History section.\n\n"
            "### Current Version\n"
            "| Property | Value |\n"
            "| Version | [from solution metadata] |\n"
            "| Publisher | [from solution metadata] |\n\n"
            "### Initial Release Inventory\n"
            "Document the current solution components as version 1.0:\n"
            "| Version | Date | Component Type | Component Name | Change Description |\n"
            "List every entity, workflow, plugin, form found in the metadata as 'Initial release'.\n\n"
            "### Version History Template\n"
            "Provide a template for future change tracking:\n"
            "| Version | Date | Author | Changes | Impact |\n\n"
            "### Change Management Process\n"
            "Provide standard Dynamics CRM change management guidance."
        ),
    },
    # --- 16. Solution Flow Diagram (Mermaid) ---
    "solution_flow_diagram": {
        "title": "Solution Flow Diagram",
        "order": 16,
        "prompt": (
            "Generate a comprehensive Mermaid diagram that visualises the ENTIRE solution flow. "
            "The output MUST be ONLY valid Mermaid markup inside a single ```mermaid code fence — no prose before or after the diagram.\n\n"
            "Use a `flowchart TD` (top-down) layout with the following structure:\n\n"
            "1. **Entity subgraphs** — create a `subgraph` for each entity detected in the metadata. "
            "Inside each subgraph list the entity's key required fields as nodes.\n"
            "2. **Plugin nodes** — for every plugin, add a node labelled `PluginName\\n(Message · Stage)` and draw an edge FROM the target entity TO the plugin.\n"
            "3. **Workflow nodes** — for every workflow, add a node labelled `WorkflowName\\n(Trigger · Mode)` and draw an edge FROM the trigger entity TO the workflow.\n"
            "4. **Form nodes** — for every form, add a node and link it to its parent entity with a dashed edge.\n"
            "5. **Security role nodes** — if roles exist, add them in a separate subgraph and link to the entities whose privileges they reference.\n"
            "6. **Cross-entity relationships** — if a workflow or plugin references or updates a SECOND entity, draw an edge between them.\n\n"
            "Styling rules:\n"
            "- Use QUOTED labels for every node: `nodeId[\"Label text\"]`.\n"
            "- Differentiate component types by prefix/emoji in the label, NOT by special bracket shapes. "
            "  Examples: `ent_acct[\"Entity: Account\"]`, `plg_val[\"Plugin: ValidateAccount\"]`, `wf_welcome[\"Workflow: Welcome Email\"]`, `frm_main[\"Form: Main Form\"]`, `role_admin[\"Role: System Admin\"]`.\n"
            "- NEVER use double-brace hexagon syntax `{{ }}` — it causes parser errors.\n"
            "- NEVER use `<br>` or `<br/>` HTML tags inside node labels. Keep each label on a single line. Use a dash or pipe to separate details (e.g., `plg_val[\"Plugin: Validate - Create - PreOp\"]`).\n"
            "- Label every edge with the relationship type (e.g., `-->|triggers|`, `-.->|has form|`).\n"
            "- Keep node IDs short, unique, and alphanumeric with underscores (e.g., `ent_account`, `plg_acct_val`, `wf_welcome`).\n"
            "- Do NOT add any text, headings, or explanations outside the mermaid code fence.\n\n"
            "IMPORTANT CONSTRAINTS:\n"
            "- Include ONLY components that exist in the metadata. Do NOT fabricate nodes.\n"
            "- The diagram must be syntactically valid Mermaid — no broken arrows, no unmatched quotes, no duplicate node IDs, no HTML tags inside labels.\n"
            "- If the solution is large, focus on the main flow and add a comment `%% Simplified for readability` at the top.\n"
            "- Wrap the ENTIRE response in a single ```mermaid ... ``` code fence."
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
    """
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
5. Add a brief introduction and conclusion appropriate for this section.
6. Do NOT invent any information not present in the partials.

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
    """Build a detailed JSON summary of the full knowledge graph including field metadata."""
    entity_details = {}
    for ename, edata in graph.entities.items():
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
            "forms": edata.forms,
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
    for wname, wdata in graph.workflows.items():
        workflow_details[wname] = {
            "trigger": wdata.trigger,
            "triggerEntity": wdata.triggerEntity,
            "mode": wdata.mode,
            "scope": wdata.scope,
            "steps": wdata.steps,
            "conditions": wdata.conditions,
            "plugins": wdata.plugins,
            "relatedEntities": wdata.relatedEntities,
        }

    plugin_details = {}
    for pname, pdata in graph.plugins.items():
        plugin_details[pname] = {
            "triggerEntity": pdata.triggerEntity,
            "operation": pdata.operation,
            "stage": pdata.stage,
            "executionMode": pdata.executionMode,
            "executionOrder": pdata.executionOrder,
            "filteringAttributes": pdata.filteringAttributes,
            "assemblyName": pdata.assemblyName,
            "description": pdata.description,
        }

    role_details = {}
    for rname, rdata in graph.roles.items():
        role_details[rname] = {
            "privileges": rdata.privileges,
            "relatedEntities": rdata.relatedEntities,
            "description": rdata.description,
        }

    return json.dumps({
        "entity_count": len(graph.entities),
        "workflow_count": len(graph.workflows),
        "plugin_count": len(graph.plugins),
        "role_count": len(graph.roles),
        "webresource_count": len(graph.webResources),
        "entities": entity_details,
        "workflows": workflow_details,
        "plugins": plugin_details,
        "roles": role_details,
        "webresource_names": list(graph.webResources.keys()),
    }, indent=2)


_SECTION_SYSTEM_PROMPT = """You are a Microsoft Dynamics CRM Solution Architect and enterprise technical writer.

You are generating one section of a comprehensive CRM project documentation package
that follows the standard Dynamics CRM documentation hierarchy:
1. Executive Summary  2. Business Requirements  3. Functional Design
4. Technical Design   5. Data Model             6. Integration
7. Customization      8. Security Model         9. Deployment
10. Testing           11. Support & Operations  12. User Guide
13. Solution Inventory 14. Environment Config   15. Change Log
16. Solution Flow Diagram (Mermaid)

Based on the provided solution metadata and knowledge graph relationships,
generate enterprise-grade documentation for the requested section.

CRITICAL RULES — FOLLOW STRICTLY:
1. ONLY document what is explicitly present in the metadata. NEVER invent, assume, or speculate about components, integrations, endpoints, or features not found in the data.
2. NEVER use "[TO BE COMPLETED]" or any placeholder text. If information is not available in the metadata, explicitly state "Not detected in solution metadata" or omit the sub-section entirely.
3. For entity fields: use the EXACT field details from the metadata including name, type, required status, and display name. Do NOT change required/optional status — it must match the metadata exactly.
4. For forms: ONLY list forms that appear in the metadata. Do NOT fabricate forms for entities that have no forms listed.
5. For integrations: ONLY document integration points that are explicitly evidenced in plugin descriptions, workflow steps, or web resources. Mark them as "Inferred from [component name]" and do NOT fabricate API endpoints, auth methods, or payloads.
6. For plugins: include ALL available metadata — entity, message, stage, execution mode, execution order, filtering attributes, assembly, and description.
7. For workflows: include trigger event, trigger entity, mode (sync/async if available), all steps, and all conditions.
8. For security roles: ONLY document roles and privileges actually present in the metadata. Do NOT generate a hypothetical role-entity permission matrix unless the privilege data supports it.
9. For test cases: generate structured, realistic test cases based ONLY on actual workflows and plugins found in the metadata. Use a clean markdown table format.
10. Return well-structured markdown documentation with headers (##, ###), bullet points, and tables.
11. Be thorough — cover EVERY component present in the metadata with specific names, field names, and details.
12. Use professional CRM consulting language suitable for Microsoft Dynamics projects.
13. Start with a brief section introduction, then provide detailed content."""


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

Return your analysis as JSON with this structure:
{
    "score": 0.0 to 1.0 (accuracy score),
    "verified": true/false,
    "issues": [
        {"severity": "error|warning|info", "section": "section_name", "message": "description"}
    ],
    "summary": "Overall assessment"
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
