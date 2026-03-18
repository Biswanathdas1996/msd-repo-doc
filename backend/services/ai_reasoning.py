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
from backend.services.knowledge_graph import VALID_RELATIONSHIP_TYPES
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
    # --- 1. Component Overview ---
    "component_overview": {
        "title": "Component Overview",
        "order": 1,
        "prompt": (
            "Generate a COMPLETE Component Overview for this solution. "
            "This section must list EVERY component found in the solution metadata so that "
            "anyone reading it (including a non-technical person) knows exactly what building blocks exist.\n\n"
            "### Solution Summary\n"
            "State the solution name/type, total component counts from the knowledge graph summary "
            "(entity_count, workflow_count, plugin_count, role_count, form_count, webresource_count). "
            "Use the EXACT numbers — do not round or estimate.\n\n"
            "### Tables / Entities / Views\n"
            "List every entity/table/view in a table:\n"
            "| # | Name | Display Name | Type (Table / View / Data Entity) | Field Count | Purpose (1 sentence based on name) |\n\n"
            "### Classes / Plugins / Extensions\n"
            "List every class, plugin, or extension in a table:\n"
            "| # | Name | Type (Plugin / Extension / Controller / Contract / Service) | Target / Related Entity | Description |\n"
            "For 'Description': use the metadata description if available; otherwise write a ONE-sentence purpose derived "
            "strictly from the class name and its extension target — do NOT fabricate business logic.\n\n"
            "### Reports\n"
            "If any SSRS reports or report-related components exist, list them:\n"
            "| # | Report Name | Data Source | Related Entities |\n\n"
            "### Forms\n"
            "| # | Form Name | Entity |\n"
            "List ONLY forms present in the metadata.\n\n"
            "### Security Roles\n"
            "If any roles exist, list them; otherwise state 'No security roles detected.'\n\n"
            "### Web Resources\n"
            "If any exist, list them; otherwise state 'No web resources detected.'\n\n"
            "RULES:\n"
            "- Every single component in the metadata MUST appear in this section — nothing should be missing.\n"
            "- Use the EXACT names from the metadata.\n"
            "- Keep descriptions short and factual — do NOT invent business logic."
        ),
    },
    # --- 2. How Everything Links Together ---
    "component_linkage": {
        "title": "How Everything Links Together",
        "order": 2,
        "prompt": (
            "Generate a section that explains HOW all the components in this solution are connected to each other. "
            "This section is written for a NON-TECHNICAL person — use simple, everyday language. "
            "Avoid jargon. Imagine you are explaining this to a business manager who has never written code.\n\n"
            "### Big Picture\n"
            "Start with a 3-5 sentence plain-English summary of what this solution does overall, "
            "based ONLY on the component names and their relationships in the metadata.\n\n"
            "### How the Pieces Fit Together\n"
            "For EACH logical group of related components, write a short paragraph (3-6 sentences) explaining:\n"
            "- What the group is about (e.g., 'Inventory Aging Report' or 'SLM Integration')\n"
            "- Which tables/entities store the data\n"
            "- Which classes/plugins read or modify that data\n"
            "- Which reports display that data\n"
            "- How a user action (e.g., running a report, syncing data) flows through the components\n"
            "Use analogies where helpful (e.g., 'Think of the Controller as the button that starts the process').\n\n"
            "### Component Relationship Map\n"
            "Create a simple table showing every connection between components:\n"
            "| Component A | Relationship | Component B | What This Means (plain English) |\n"
            "Examples of relationships: 'extends', 'reads data from', 'writes data to', 'controls', 'sends data to', 'receives data from'.\n\n"
            "### Data Flow Summary\n"
            "For each major data flow in the solution, describe it as a numbered sequence of simple steps:\n"
            "1. User does X → 2. Component A processes it → 3. Data goes to B → 4. Result shown in C\n\n"
            "RULES:\n"
            "- Use ONLY component names from the metadata — do NOT invent components.\n"
            "- Keep language simple — no technical terms like 'extension', 'class', 'method' without explaining them.\n"
            "- Base relationships ONLY on what the metadata shows (extension targets, entity references, etc.).\n"
            "- Do NOT fabricate business logic or detailed processing rules."
        ),
    },
    # --- 3. Feature List ---
    "feature_list": {
        "title": "Feature List",
        "order": 3,
        "prompt": (
            "Generate a complete FEATURE LIST for this solution. A 'feature' is a user-facing capability or "
            "system capability that the solution provides — something a business person would recognize as useful.\n\n"
            "Analyze ALL components in the metadata (entities, classes/plugins, reports, views, extensions) "
            "and group them into distinct features.\n\n"
            "### Feature Summary Table\n"
            "| # | Feature Name | Description (1-2 sentences, non-technical) | Components Involved |\n\n"
            "For each feature derive the name from the component names and their relationships. "
            "The description should explain what value this feature provides in plain business language.\n"
            "The 'Components Involved' column must list the EXACT component names from the metadata.\n\n"
            "### Feature Details\n"
            "For EACH feature identified above, create a sub-section:\n"
            "#### Feature: [Feature Name]\n"
            "- **What it does**: 2-3 sentences in plain English explaining the feature's purpose.\n"
            "- **Components**:\n"
            "  | Component | Type | Role in this Feature |\n"
            "  List every component that participates in this feature with its type "
            "(Table, View, Class, Extension, Report, Controller, Contract, etc.) "
            "and what role it plays (e.g., 'Stores aging data', 'Runs the report', 'Adds custom fields').\n"
            "- **How it works (simplified)**: A 3-5 step plain-English description of how the feature works end-to-end, "
            "from the user's perspective.\n\n"
            "RULES:\n"
            "- Every component in the metadata MUST belong to at least one feature.\n"
            "- Feature names should be business-friendly (e.g., 'Inventory Aging Report with Custom Fields', "
            "not 'PwcInventAgingCmdAggregateSelected_Extension').\n"
            "- Do NOT invent features that have no supporting components in the metadata.\n"
            "- Do NOT describe internal code logic — keep it at the 'what does the user see/get' level."
        ),
    },
    # --- 4. Feature Flows ---
    "feature_flows": {
        "title": "Feature Flows",
        "order": 4,
        "prompt": (
            "Generate detailed FLOW DIAGRAMS and STEP-BY-STEP FLOWS for each feature in this solution. "
            "This section maps out exactly what happens when each feature is used.\n\n"
            "First, identify the distinct features by grouping related components from the metadata. "
            "Then for EACH feature:\n\n"
            "### Feature: [Feature Name]\n\n"
            "#### Flow Description\n"
            "Write a numbered step-by-step flow showing what happens from start to finish:\n"
            "| Step # | What Happens | Component Responsible | Data Involved |\n"
            "Each row should describe one action in the flow. "
            "'What Happens' should be in plain English. "
            "'Component Responsible' must be the EXACT component name from metadata. "
            "'Data Involved' should mention the specific tables/fields/entities touched.\n\n"
            "#### Trigger\n"
            "State what starts this flow (e.g., 'User runs the report from a menu item', "
            "'Batch job executes on schedule', 'User clicks Sync button').\n\n"
            "#### Components in this Flow\n"
            "List all components involved in order of execution:\n"
            "1. [Component Name] — [what it does in this flow]\n"
            "2. [Component Name] — [what it does in this flow]\n"
            "...\n\n"
            "#### Mermaid Flow Diagram\n"
            "Generate a Mermaid flowchart (`flowchart TD`) for this feature's flow.\n"
            "- Use QUOTED labels: `nodeId[\"Label text\"]`\n"
            "- Label every edge with what happens\n"
            "- NEVER use `{{ }}` or `<br>` in labels\n"
            "- Keep it simple and readable\n"
            "- Wrap in a ```mermaid code fence\n\n"
            "#### Input / Output\n"
            "- **Input**: What data or user action feeds into this flow\n"
            "- **Output**: What the user gets at the end (e.g., a report, synced data, updated records)\n\n"
            "RULES:\n"
            "- Base flows ONLY on component relationships visible in the metadata.\n"
            "- If a class extends another, show the base being called first, then the extension.\n"
            "- Use EXACT component names from metadata.\n"
            "- Do NOT fabricate processing logic — describe flows at the component interaction level.\n"
            "- Every component must appear in at least one feature flow."
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

    With the new 4-section documentation structure, all sections are always generated.
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

You are generating one section of a focused, easy-to-understand documentation package
that follows this 4-section structure:
1. Component Overview — List every component in the solution
2. How Everything Links Together — Explain connections in plain language for non-technical readers
3. Feature List — Identify and describe all features with their components
4. Feature Flows — Map out step-by-step flows for each feature with diagrams

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
