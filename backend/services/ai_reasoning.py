import os
import json
import requests
from backend.models.schemas import (
    KnowledgeGraph, DocSection, GeneratedDocs, VerificationResult, VerificationIssue
)
from backend.services.chunking_engine import create_chunks, chunks_to_context
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
    "overview": {
        "title": "Solution Overview",
        "prompt": "Generate a comprehensive solution overview including purpose, scope, and high-level description."
    },
    "features": {
        "title": "Features",
        "prompt": "List and describe all features identified in the solution, organized by functional area."
    },
    "architecture": {
        "title": "Technical Architecture",
        "prompt": "Describe the technical architecture including entities, plugins, workflows, and how they interact."
    },
    "entities": {
        "title": "Entity Documentation",
        "prompt": "Document each entity with its fields, relationships, associated forms, workflows, and plugins."
    },
    "workflows": {
        "title": "Workflow Documentation",
        "prompt": "Document each workflow with its trigger, steps, conditions, and associated plugins."
    },
    "functional_flow": {
        "title": "Functional Flows",
        "prompt": "Document the functional flows showing how data moves through the system from user action to completion."
    },
    "integrations": {
        "title": "Integration Points",
        "prompt": "Identify and document all integration points, plugins, and external system connections."
    }
}


def generate_documentation(
    solution_id: str,
    graph: KnowledgeGraph,
    requested_sections: list[str] | None = None
) -> GeneratedDocs:
    chunks = create_chunks(graph)
    context = chunks_to_context(chunks)

    sections_to_generate = requested_sections if requested_sections else list(SECTION_CONFIGS.keys())

    doc_sections = []
    for idx, section_key in enumerate(sections_to_generate):
        if section_key not in SECTION_CONFIGS:
            continue

        config = SECTION_CONFIGS[section_key]
        section = _generate_section(context, config, graph, idx)
        doc_sections.append(section)

    return GeneratedDocs(
        solutionId=solution_id,
        generatedAt=datetime.now(timezone.utc).isoformat(),
        sections=doc_sections,
        verified=False
    )


def _generate_section(
    context: str,
    config: dict,
    graph: KnowledgeGraph,
    order: int
) -> DocSection:
    system_prompt = """You are a Microsoft Dynamics Architect and technical writer.

Based on the provided solution metadata and relationships,
generate enterprise-grade documentation.

Rules:
- Only use the provided metadata. Do not invent information.
- Return well-structured markdown documentation.
- Use headers, bullet points, and tables where appropriate.
- Be thorough but concise.
- Include specific field names, workflow steps, and plugin details from the metadata."""

    graph_summary = json.dumps({
        "entity_count": len(graph.entities),
        "workflow_count": len(graph.workflows),
        "plugin_count": len(graph.plugins),
        "entity_names": list(graph.entities.keys()),
        "workflow_names": list(graph.workflows.keys()),
        "plugin_names": list(graph.plugins.keys())
    }, indent=2)

    user_prompt = f"""{config['prompt']}

## Solution Knowledge Graph Summary
{graph_summary}

## Detailed Metadata Chunks
{context}"""

    try:
        content = _call_pwc_genai(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=8192
        )

        if not content or not content.strip():
            content = f"# {config['title']}\n\nNo content generated."
    except Exception as e:
        content = f"# {config['title']}\n\nDocumentation generation encountered an error: {str(e)}"

    slug = config['title'].lower().replace(" ", "_")
    return DocSection(
        title=config['title'],
        slug=slug,
        content=content,
        order=order
    )


def verify_documentation(
    solution_id: str,
    graph: KnowledgeGraph,
    docs: GeneratedDocs
) -> VerificationResult:
    graph_data = graph.model_dump()
    doc_content = "\n\n---\n\n".join([s.content for s in docs.sections])

    system_prompt = """You are a documentation verification specialist for Microsoft Dynamics solutions.

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

    user_prompt = f"""## Knowledge Graph Data
{json.dumps(graph_data, indent=2, default=str)[:30000]}

## Generated Documentation
{doc_content[:30000]}"""

    try:
        result_text = _call_pwc_genai(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=4096
        )

        result_text = result_text.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[1]
            result_text = result_text.rsplit("```", 1)[0]

        result = json.loads(result_text)

        issues = [
            VerificationIssue(
                severity=i.get("severity", "info"),
                section=i.get("section", "general"),
                message=i.get("message", "")
            )
            for i in result.get("issues", [])
        ]

        return VerificationResult(
            solutionId=solution_id,
            verified=result.get("verified", False),
            score=result.get("score", 0.0),
            issues=issues,
            summary=result.get("summary", "Verification completed")
        )
    except Exception as e:
        return VerificationResult(
            solutionId=solution_id,
            verified=False,
            score=0.0,
            issues=[VerificationIssue(
                severity="error",
                section="general",
                message=f"Verification failed: {str(e)}"
            )],
            summary=f"Verification process encountered an error: {str(e)}"
        )
