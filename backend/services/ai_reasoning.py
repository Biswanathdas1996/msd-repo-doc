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
            "Generate an Executive Summary for this Microsoft Dynamics CRM solution. Include:\n"
            "- Business problem statement (inferred from the entities, workflows, and plugins present)\n"
            "- Objectives of the CRM solution\n"
            "- High-level architecture overview\n"
            "- Key stakeholders (inferred from security roles)\n"
            "- Key benefits delivered\n"
            "Present this as a professional executive-level overview suitable for leadership review."
        ),
    },
    # --- 2. Business Requirements Document (BRD) ---
    "business_requirements": {
        "title": "Business Requirements Document (BRD)",
        "order": 2,
        "prompt": (
            "Generate a Business Requirements Document based on the solution metadata. Include:\n"
            "- Business objectives (inferred from entities, workflows, and functional areas)\n"
            "- Stakeholders (derived from security roles and business units)\n"
            "- Business workflows (derived from workflow definitions)\n"
            "- Functional requirements table (FR-01, FR-02, etc.) mapping each workflow/plugin to a requirement\n"
            "- Non-functional requirements (performance, security, availability based on solution structure)\n"
            "Format functional requirements as: ID | Description | Related Component"
        ),
    },
    # --- 3. Functional Design Document (FDD) ---
    "functional_design": {
        "title": "Functional Design Document (FDD)",
        "order": 3,
        "prompt": (
            "Generate a Functional Design Document explaining how Dynamics CRM fulfills the business requirements. Include:\n"
            "- Module breakdown (group entities into functional modules)\n"
            "- Entity relationships with a description of each relationship\n"
            "- Business process flows (derived from workflows)\n"
            "- Forms and views documentation\n"
            "- Dashboards (if any detected)\n"
            "- Security roles mapping to features\n"
            "Use a table format: Feature | Dynamics Component | Description"
        ),
    },
    # --- 4. Technical Design Document (TDD) ---
    "technical_design": {
        "title": "Technical Design Document (TDD)",
        "order": 4,
        "prompt": (
            "Generate a Technical Design Document. This is the most critical section for developers. Include:\n"
            "### Solution Architecture\n"
            "- CRM environment topology\n"
            "- Integration architecture\n"
            "- Data flow diagram description\n"
            "### Components Table\n"
            "| Component Type | Name | Description |\n"
            "Cover: Plugins, Workflows, Power Automate flows, JavaScript web resources, UI components\n"
            "### Integration Details\n"
            "- REST APIs, Azure services, Middleware, Authentication methods\n"
            "Document every plugin with its message, stage, entity, and purpose."
        ),
    },
    # --- 5. Data Model Documentation ---
    "data_model": {
        "title": "Data Model Documentation",
        "order": 5,
        "prompt": (
            "Generate comprehensive Data Model Documentation. Include:\n"
            "- Entity purpose table: Entity | Purpose | Key Fields\n"
            "- Complete field definitions for each entity (name, type, required, display name)\n"
            "- Lookup relationships between entities\n"
            "- Option sets / choice fields\n"
            "- Calculated fields (if detected)\n"
            "- Entity relationship diagram description (textual representation)\n"
            "Group entities by functional area and describe how they relate to each other."
        ),
    },
    # --- 6. Integration Documentation ---
    "integration": {
        "title": "Integration Documentation",
        "order": 6,
        "prompt": (
            "Generate Integration Documentation for this CRM solution. Include:\n"
            "- External systems integrated (inferred from plugins, web resources, and workflows)\n"
            "- API endpoints and connection points\n"
            "- Authentication methods\n"
            "- Message formats and data payloads\n"
            "- Integration flow descriptions: Source → Target, Method, Auth, Trigger\n"
            "- Any Azure service connections detected\n"
            "Document each integration point with: System | Method | Auth | Payload | Trigger"
        ),
    },
    # --- 7. Customization Documentation ---
    "customization": {
        "title": "Customization Documentation",
        "order": 7,
        "prompt": (
            "Generate Customization Documentation detailing ALL custom code and configuration. Include:\n"
            "- Plugins: Name, Target Entity, Message, Stage, Purpose\n"
            "- Custom workflows: Name, Trigger, Steps, Purpose\n"
            "- JavaScript form scripts (from web resources): Name, Related Entity/Form, Purpose\n"
            "- Custom entities (non-OOB entities)\n"
            "- Custom APIs\n"
            "- Web resources by type (JS, HTML, CSS, images)\n"
            "Use a table: Component | Type | Target | Purpose"
        ),
    },
    # --- 8. Security Model ---
    "security_model": {
        "title": "Security Model",
        "order": 8,
        "prompt": (
            "Generate Security Model documentation. Include:\n"
            "- Business Units (inferred from roles)\n"
            "- Security roles with their full privilege breakdown\n"
            "- Field security profiles (if detected)\n"
            "- Teams (if detected)\n"
            "- Access control model / role hierarchy\n"
            "- Role-to-entity permission matrix: Role | Entity | Create | Read | Write | Delete\n"
            "Document every role with its privileges and the entities it can access."
        ),
    },
    # --- 9. Deployment Documentation ---
    "deployment": {
        "title": "Deployment Documentation",
        "order": 9,
        "prompt": (
            "Generate Deployment Documentation. Include:\n"
            "- Solution packages (managed vs unmanaged)\n"
            "- Environment strategy: DEV → SIT → UAT → PROD\n"
            "- Deployment steps and checklist\n"
            "- Configuration settings per environment\n"
            "- Pre-deployment and post-deployment checks\n"
            "- Rollback procedures\n"
            "- Solution version information from the metadata"
        ),
    },
    # --- 10. Testing Documentation ---
    "testing": {
        "title": "Testing Documentation",
        "order": 10,
        "prompt": (
            "Generate Testing Documentation. Include:\n"
            "### Test Plan\n"
            "- Test scope and strategy\n"
            "### Test Cases (generated from workflows and plugins)\n"
            "| Test Case ID | Scenario | Entity | Expected Result | Type |\n"
            "Generate at least one test case for each workflow and plugin.\n"
            "### Test Types Coverage\n"
            "- Unit testing (plugins)\n"
            "- Integration testing (cross-entity workflows)\n"
            "- UAT testing (business process flows)\n"
            "- Performance testing considerations"
        ),
    },
    # --- 11. Support & Operations Guide ---
    "support_operations": {
        "title": "Support & Operations Guide",
        "order": 11,
        "prompt": (
            "Generate a Support & Operations Guide for production support teams. Include:\n"
            "- Monitoring strategy for the CRM solution\n"
            "- Known issues and common problems (inferred from solution complexity)\n"
            "- Troubleshooting guide for each plugin and workflow\n"
            "- Logging approach (CRM plugin trace logs, Azure logs, Application Insights)\n"
            "- Escalation procedures\n"
            "- Health check procedures\n"
            "Create a troubleshooting table: Issue | Component | Resolution Steps"
        ),
    },
    # --- 12. User Guide ---
    "user_guide": {
        "title": "User Guide",
        "order": 12,
        "prompt": (
            "Generate a User Guide for business users. Include:\n"
            "- How to use each major entity (create, update, view records)\n"
            "- Step-by-step guides for each business process flow\n"
            "- How to navigate forms\n"
            "- Tips and best practices\n"
            "- FAQ section\n"
            "Write in end-user-friendly language, avoiding technical jargon. "
            "Organize by functional area (e.g., Lead Management, Case Tracking)."
        ),
    },
    # --- 13. Solution Inventory ---
    "solution_inventory": {
        "title": "Solution Inventory",
        "order": 13,
        "prompt": (
            "Generate a complete Solution Inventory listing ALL CRM components. Include tables for:\n"
            "- Entities: | Component Type | Name | Display Name | Field Count |\n"
            "- Plugins: | Component Type | Name | Target Entity | Message | Stage |\n"
            "- Workflows: | Component Type | Name | Trigger Entity | Trigger Event |\n"
            "- Web Resources: | Component Type | Name | Type | Related Entity |\n"
            "- Security Roles: | Component Type | Name | Privilege Count |\n"
            "- Forms: | Component Type | Name | Entity |\n"
            "This must be an exhaustive list of EVERY component found in the solution."
        ),
    },
    # --- 14. Configuration & Environment Details ---
    "environment_config": {
        "title": "Configuration & Environment Details",
        "order": 14,
        "prompt": (
            "Generate Configuration & Environment Details documentation. Include:\n"
            "- Environment URLs template (Dev, Test, UAT, Prod)\n"
            "- Integration endpoints\n"
            "- Secrets management approach\n"
            "- Azure resources used (if detected)\n"
            "- Solution-specific configuration settings\n"
            "- Publisher information and solution version\n"
            "Use the solution metadata to populate version, publisher, and description fields."
        ),
    },
    # --- 15. Change Log / Version History ---
    "change_log": {
        "title": "Change Log / Version History",
        "order": 15,
        "prompt": (
            "Generate a Change Log / Version History section. Include:\n"
            "- Current version information from the solution metadata\n"
            "- Version history table template: | Version | Date | Changes | Author |\n"
            "- Release notes template\n"
            "- Change management process description\n"
            "Pre-populate v1.0 with the current solution components as the initial release. "
            "Include guidance on how to maintain this log going forward."
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
    """Build a compact JSON summary of the full knowledge graph."""
    return json.dumps({
        "entity_count": len(graph.entities),
        "workflow_count": len(graph.workflows),
        "plugin_count": len(graph.plugins),
        "role_count": len(graph.roles),
        "webresource_count": len(graph.webResources),
        "entity_names": list(graph.entities.keys()),
        "workflow_names": list(graph.workflows.keys()),
        "plugin_names": list(graph.plugins.keys()),
        "role_names": list(graph.roles.keys()),
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

Based on the provided solution metadata and knowledge graph relationships,
generate enterprise-grade documentation for the requested section.

Rules:
- Only use the provided metadata. Do not invent information.
- Return well-structured markdown documentation.
- Use headers (##, ###), bullet points, and tables where appropriate.
- Be thorough but concise — cover every component present in the metadata.
- Include specific field names, workflow steps, plugin details, and role privileges.
- Use professional CRM consulting language suitable for Microsoft Dynamics projects.
- When data is insufficient for a section, provide a template with placeholders marked as [TO BE COMPLETED].
- Start with a brief section introduction, then provide detailed content."""


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
