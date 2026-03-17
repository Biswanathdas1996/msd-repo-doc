import os
import re
import json
import shutil
import tempfile
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from backend.models.schemas import (
    Solution, SolutionSummary, SolutionMetadata, SolutionStatus,
    KnowledgeGraph, Entity, Workflow, Plugin, Role, WebResource, FunctionalFlow,
    GeneratedDocs, GenerateDocsRequest, VerificationResult,
    GitHubImportRequest
)
from backend.services.extractor import extract_solution
from backend.services.github_downloader import download_github_repo
from backend.services.xml_parser import (
    parse_solution_xml, parse_entity_file, parse_workflow_file,
    parse_plugin_file, parse_form_files, parse_form_files_detailed,
    parse_role_files,
    parse_webresource_files, parse_other_xml_files,
    parse_ax_class_file, parse_ax_table_file, parse_ax_view_file,
    ax_classes_to_plugins, parse_ax_query_file, parse_ax_report_file,
    parse_customizations_xml, parse_xaml_workflow_file
)
from backend.services.source_code_parser import parse_source_code_repo
from backend.services.knowledge_graph import build_knowledge_graph
from backend.services.flow_generator import generate_functional_flows
from backend.services.ai_reasoning import generate_documentation, generate_single_section, verify_documentation, SECTION_CONFIGS
from backend.services.doc_exporter import export_to_docx, export_to_pdf


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SOLUTIONS_DIR = os.path.join(DATA_DIR, "solutions")
METADATA_DIR = os.path.join(DATA_DIR, "metadata")

solutions_store: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(SOLUTIONS_DIR, exist_ok=True)
    os.makedirs(METADATA_DIR, exist_ok=True)
    _load_persisted_solutions()
    yield


app = FastAPI(
    title="AI Documentation Generator",
    version="1.0.0",
    root_path="/py-api",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_persisted_solutions():
    if not os.path.exists(METADATA_DIR):
        return
    for fname in os.listdir(METADATA_DIR):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(METADATA_DIR, fname)) as f:
                    data = json.load(f)
                    solutions_store[data["id"]] = data
            except Exception:
                pass


def _save_solution_metadata(sol_id: str, data: dict):
    os.makedirs(METADATA_DIR, exist_ok=True)
    with open(os.path.join(METADATA_DIR, f"{sol_id}.json"), "w") as f:
        json.dump(data, f, indent=2, default=str)


def _get_solution_or_404(sol_id: str) -> dict:
    if sol_id not in solutions_store:
        raise HTTPException(status_code=404, detail="Solution not found")
    return solutions_store[sol_id]


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


def _process_solution(zip_path: str, solution_name: str) -> Solution:
    try:
        result = extract_solution(zip_path, SOLUTIONS_DIR)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(zip_path):
            os.unlink(zip_path)

    sol_id = result["id"]
    final_name = solution_name or f"Solution {sol_id}"
    structure = result["structure"]
    is_source_code = result.get("is_source_code", False)

    sol_metadata = parse_solution_xml(structure.get("solution_xml", ""))

    entities: list[Entity] = []
    workflows: list[Workflow] = []
    plugins: list[Plugin] = []
    forms: list[str] = []
    roles: list[Role] = []
    webresources: list[WebResource] = []
    source_info = {}
    form_details = []

    if is_source_code:
        sc_result = parse_source_code_repo(result["output_folder"])
        entities = sc_result["entities"]
        workflows = sc_result["workflows"]
        plugins = sc_result["plugins"]
        source_info = {
            "type": "source_code",
            "source_files": sc_result["source_files_count"],
            "projects": sc_result["projects_count"],
            "project_info": sc_result.get("project_info", {}),
        }
        if not sol_metadata:
            sol_metadata = source_info
        else:
            sol_metadata.update(source_info)
    elif structure.get("is_ax_fo", False):
        ax_classes_data = []
        for cf in structure.get("ax_classes", []):
            ax_classes_data.append(parse_ax_class_file(cf))

        for tf in structure.get("ax_tables", []):
            entities.append(parse_ax_table_file(tf))

        for vf in structure.get("ax_views", []):
            entities.append(parse_ax_view_file(vf))

        for de in structure.get("ax_data_entities", []):
            entities.append(parse_ax_table_file(de))

        plugins.extend(ax_classes_to_plugins(ax_classes_data))

        for wf in structure.get("workflows", []):
            workflows.extend(parse_workflow_file(wf))

        forms = parse_form_files(structure.get("forms", []))
        form_details = parse_form_files_detailed(structure.get("forms", []))

        # --- AX queries → feed related tables as entities ---
        ax_query_data = []
        for qf in structure.get("ax_queries", []):
            q = parse_ax_query_file(qf)
            ax_query_data.append(q)
            for table_name in q.get("related_tables", []):
                if not any(e.name.lower() == table_name.lower() for e in entities):
                    entities.append(Entity(name=table_name, displayName=f"{table_name} (from query: {q['name']})"))

        # --- AX reports → feed data sources as references ---
        ax_report_data = []
        for rf in structure.get("ax_reports", []):
            r = parse_ax_report_file(rf)
            ax_report_data.append(r)

        # --- Roles/Security ---
        roles = parse_role_files(structure.get("roles", []))

        if not sol_metadata:
            sol_metadata = {}
        sol_metadata["type"] = "ax_fo"
        sol_metadata["description"] = "Dynamics 365 Finance & Operations (X++) solution"
        sol_metadata["ax_class_count"] = len(ax_classes_data)
        sol_metadata["ax_table_count"] = len(structure.get("ax_tables", []))
        sol_metadata["ax_view_count"] = len(structure.get("ax_views", []))
        sol_metadata["ax_query_count"] = len(ax_query_data)
        sol_metadata["ax_report_count"] = len(ax_report_data)
    else:
        # ---- Parse monolithic customizations.xml if present (real CRM ZIPs) ----
        cust_xml_path = structure.get("customizations_xml")
        if cust_xml_path:
            cust = parse_customizations_xml(cust_xml_path)
            entities.extend(cust["entities"])
            workflows.extend(cust["workflows"])
            plugins.extend(cust["plugins"])
            forms.extend(cust["forms"])
            form_details.extend(cust["form_details"])
            roles.extend(cust.get("roles", []))

            # Merge XAML workflow steps into matching workflow objects
            xaml_map: dict[str, str] = cust.get("workflow_xaml_map", {})
            solution_folder = result["output_folder"]
            for wf_obj in workflows:
                rel_xaml = xaml_map.get(wf_obj.name)
                if not rel_xaml:
                    continue
                xaml_path = os.path.join(solution_folder, rel_xaml)
                if not os.path.isfile(xaml_path):
                    # Try normalised separators
                    xaml_path = os.path.join(solution_folder, rel_xaml.replace("/", os.sep))
                if os.path.isfile(xaml_path):
                    xaml_steps, xaml_conds = parse_xaml_workflow_file(xaml_path)
                    if xaml_steps:
                        wf_obj.steps = xaml_steps
                    if xaml_conds:
                        wf_obj.conditions = xaml_conds

        # ---- Parse standalone XAML workflow files not yet matched ----
        matched_xaml_paths = set()
        if cust_xml_path:
            for rel in cust.get("workflow_xaml_map", {}).values():
                matched_xaml_paths.add(os.path.normpath(os.path.join(result["output_folder"], rel)))
                matched_xaml_paths.add(os.path.normpath(os.path.join(result["output_folder"], rel.replace("/", os.sep))))

        for xaml_path in structure.get("xaml_workflows", []):
            if os.path.normpath(xaml_path) in matched_xaml_paths:
                continue
            xaml_steps, xaml_conds = parse_xaml_workflow_file(xaml_path)
            wf_name = os.path.splitext(os.path.basename(xaml_path))[0]
            workflows.append(Workflow(
                name=wf_name,
                steps=xaml_steps,
                conditions=xaml_conds,
            ))

        # ---- Parse individual XML files (folder-based structure) ----
        for ef in structure.get("entities", []):
            entities.extend(parse_entity_file(ef))

        for wf in structure.get("workflows", []):
            workflows.extend(parse_workflow_file(wf))

        for pf in structure.get("plugins", []):
            plugins.extend(parse_plugin_file(pf))

        forms.extend(parse_form_files(structure.get("forms", [])))
        form_details.extend(parse_form_files_detailed(structure.get("forms", [])))
        roles.extend(parse_role_files(structure.get("roles", [])))
        webresources = parse_webresource_files(structure.get("webresources", []))

    # --- Parse unclassified XML (other_xml) through fallback parser ---
    other_xml_files = structure.get("other_xml", [])
    if other_xml_files:
        extra_entities, extra_workflows, extra_plugins = parse_other_xml_files(other_xml_files)
        entities.extend(extra_entities)
        workflows.extend(extra_workflows)
        plugins.extend(extra_plugins)

    # --- Deduplicate forms to ensure accurate counts ---
    forms = list(dict.fromkeys(forms))  # preserve order, remove duplicates

    # --- Deduplicate form_details by (name, entity) ---
    seen_form_details = set()
    deduped_form_details = []
    for fd in form_details:
        key = (fd.name, fd.entity or "")
        if key not in seen_form_details:
            seen_form_details.add(key)
            deduped_form_details.append(fd)
    form_details = deduped_form_details

    knowledge_graph = build_knowledge_graph(entities, workflows, plugins, forms, roles, webresources, form_details)
    functional_flows = generate_functional_flows(knowledge_graph)

    sol_data = {
        "id": sol_id,
        "name": final_name,
        "uploadedAt": result["uploadedAt"],
        "status": "ready",
        "entityCount": len(entities),
        "workflowCount": len(workflows),
        "pluginCount": len(plugins),
        "formCount": len(forms),
        "roleCount": len(roles) or len(structure.get("roles", [])),
        "webResourceCount": len(webresources) or len(structure.get("webresources", [])),
        "hasDocumentation": False,
        "metadata": sol_metadata,
        "entities": [e.model_dump() for e in entities],
        "workflows": [w.model_dump() for w in workflows],
        "plugins": [p.model_dump() for p in plugins],
        "forms": forms,
        "roles": [r.model_dump() for r in roles],
        "webresources": [wr.model_dump() for wr in webresources],
        "knowledge_graph": knowledge_graph.model_dump(),
        "functional_flows": [f.model_dump() for f in functional_flows],
        "docs": None,
        "output_folder": result["output_folder"]
    }

    solutions_store[sol_id] = sol_data
    _save_solution_metadata(sol_id, sol_data)

    return Solution(
        id=sol_id,
        name=final_name,
        uploadedAt=result["uploadedAt"],
        status=SolutionStatus.ready,
        entityCount=len(entities),
        workflowCount=len(workflows),
        pluginCount=len(plugins),
        formCount=len(forms),
        roleCount=len(structure.get("roles", [])),
        webResourceCount=len(structure.get("webresources", [])),
        hasDocumentation=False,
        metadata=SolutionMetadata(**sol_metadata) if sol_metadata else None
    )


@app.post("/solutions/upload")
async def upload_solution(
    file: UploadFile = File(...),
    name: str = Form(default="")
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP file")

    MAX_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024
    CHUNK_SIZE = 8 * 1024 * 1024

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        total_written = 0
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(status_code=400, detail="File too large. Max 10GB.")
            tmp.write(chunk)
        tmp_path = tmp.name

    solution_name = name or file.filename or ""
    return _process_solution(tmp_path, solution_name)


@app.post("/solutions/import-github")
async def import_from_github(body: GitHubImportRequest):
    try:
        zip_path = download_github_repo(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download repository: {str(e)}")

    repo_name = body.url.rstrip("/").split("/")[-1].replace(".git", "")
    solution_name = body.name or repo_name
    return _process_solution(zip_path, solution_name)


@app.get("/solutions")
def list_solutions():
    summaries = []
    for sol_id, data in solutions_store.items():
        summaries.append(SolutionSummary(
            id=data["id"],
            name=data["name"],
            uploadedAt=data["uploadedAt"],
            entityCount=data.get("entityCount", 0),
            workflowCount=data.get("workflowCount", 0),
            pluginCount=data.get("pluginCount", 0),
            hasDocumentation=data.get("hasDocumentation", False),
            status=SolutionStatus(data.get("status", "ready"))
        ))
    return summaries


@app.get("/solutions/{sol_id}")
def get_solution(sol_id: str):
    data = _get_solution_or_404(sol_id)
    return Solution(
        id=data["id"],
        name=data["name"],
        uploadedAt=data["uploadedAt"],
        status=SolutionStatus(data.get("status", "ready")),
        entityCount=data.get("entityCount", 0),
        workflowCount=data.get("workflowCount", 0),
        pluginCount=data.get("pluginCount", 0),
        formCount=data.get("formCount", 0),
        roleCount=data.get("roleCount", 0),
        webResourceCount=data.get("webResourceCount", 0),
        hasDocumentation=data.get("hasDocumentation", False),
        metadata=SolutionMetadata(**data["metadata"]) if data.get("metadata") else None
    )


@app.delete("/solutions/{sol_id}")
def delete_solution(sol_id: str):
    data = _get_solution_or_404(sol_id)

    output_folder = data.get("output_folder")
    if output_folder and os.path.exists(output_folder):
        shutil.rmtree(output_folder, ignore_errors=True)

    meta_file = os.path.join(METADATA_DIR, f"{sol_id}.json")
    if os.path.exists(meta_file):
        os.unlink(meta_file)

    del solutions_store[sol_id]
    return {"message": f"Solution {sol_id} deleted"}


@app.post("/solutions/{sol_id}/reprocess")
def reprocess_solution(sol_id: str):
    data = _get_solution_or_404(sol_id)
    output_folder = data.get("output_folder")
    if not output_folder or not os.path.exists(output_folder):
        raise HTTPException(status_code=400, detail="Solution source files not found on disk")

    is_source_code = data.get("metadata", {}).get("type") == "source_code"
    entities: list[Entity] = []
    workflows: list[Workflow] = []
    plugins: list[Plugin] = []
    roles: list[Role] = []
    webresources: list[WebResource] = []
    forms: list[str] = data.get("forms", [])

    if is_source_code:
        sc_result = parse_source_code_repo(output_folder)
        entities = sc_result["entities"]
        workflows = sc_result["workflows"]
        plugins = sc_result["plugins"]
    else:
        for ef in data.get("entities", []):
            entities.append(Entity(**ef))
        for wf in data.get("workflows", []):
            workflows.append(Workflow(**wf))
        for pf in data.get("plugins", []):
            plugins.append(Plugin(**pf))
        for rf in data.get("roles", []):
            roles.append(Role(**rf))
        for wr in data.get("webresources", []):
            webresources.append(WebResource(**wr))

    knowledge_graph = build_knowledge_graph(entities, workflows, plugins, forms, roles, webresources)
    functional_flows = generate_functional_flows(knowledge_graph)

    data["entities"] = [e.model_dump() for e in entities]
    data["workflows"] = [w.model_dump() for w in workflows]
    data["plugins"] = [p.model_dump() for p in plugins]
    data["knowledge_graph"] = knowledge_graph.model_dump()
    data["functional_flows"] = [f.model_dump() for f in functional_flows]
    data["entityCount"] = len(entities)
    data["workflowCount"] = len(workflows)
    data["pluginCount"] = len(plugins)

    solutions_store[sol_id] = data
    _save_solution_metadata(sol_id, data)
    return {"message": "Reprocessed successfully", "relationships": len(knowledge_graph.relationships)}


@app.get("/solutions/{sol_id}/knowledge-graph")
def get_knowledge_graph(sol_id: str):
    data = _get_solution_or_404(sol_id)
    kg = data.get("knowledge_graph", {})
    return KnowledgeGraph(**kg)


@app.get("/solutions/{sol_id}/entities")
def get_entities(sol_id: str):
    data = _get_solution_or_404(sol_id)
    return [Entity(**e) for e in data.get("entities", [])]


@app.get("/solutions/{sol_id}/workflows")
def get_workflows(sol_id: str):
    data = _get_solution_or_404(sol_id)
    return [Workflow(**w) for w in data.get("workflows", [])]


@app.get("/solutions/{sol_id}/plugins")
def get_plugins(sol_id: str):
    data = _get_solution_or_404(sol_id)
    return [Plugin(**p) for p in data.get("plugins", [])]


@app.get("/solutions/{sol_id}/functional-flows")
def get_functional_flows(sol_id: str):
    data = _get_solution_or_404(sol_id)
    return [FunctionalFlow(**f) for f in data.get("functional_flows", [])]


@app.post("/solutions/{sol_id}/generate-docs")
def generate_docs(sol_id: str, request: GenerateDocsRequest = None):
    data = _get_solution_or_404(sol_id)
    kg = KnowledgeGraph(**data.get("knowledge_graph", {}))

    sections = request.sections if request and request.sections else None

    docs = generate_documentation(sol_id, kg, sections)

    data["docs"] = docs.model_dump()
    data["hasDocumentation"] = True
    solutions_store[sol_id] = data
    _save_solution_metadata(sol_id, data)

    return docs


@app.get("/solutions/{sol_id}/doc-sections")
def list_doc_sections(sol_id: str):
    """List all available documentation sections with their generation status."""
    data = _get_solution_or_404(sol_id)
    existing_docs = data.get("docs")
    generated_slugs = set()
    if existing_docs and existing_docs.get("sections"):
        for sec in existing_docs["sections"]:
            generated_slugs.add(sec.get("slug", ""))

    result = []
    for key, config in SECTION_CONFIGS.items():
        slug = config["title"].lower().replace(" ", "_").replace("/", "").replace("&", "and")
        result.append({
            "key": key,
            "title": config["title"],
            "order": config.get("order", 99),
            "generated": slug in generated_slugs or key in generated_slugs,
        })
    return sorted(result, key=lambda x: x["order"])


@app.post("/solutions/{sol_id}/generate-section/{section_key}")
def generate_section(sol_id: str, section_key: str):
    """Generate a single documentation section with chunking support.

    This allows incremental doc generation — one section per API call.
    The generated section is merged into the existing docs for the solution.
    """
    data = _get_solution_or_404(sol_id)
    kg = KnowledgeGraph(**data.get("knowledge_graph", {}))

    if section_key not in SECTION_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown section: {section_key}. Available: {list(SECTION_CONFIGS.keys())}"
        )

    section = generate_single_section(sol_id, kg, section_key)

    # Merge into existing docs
    existing_docs = data.get("docs")
    if existing_docs:
        # Replace existing section with same slug, or append
        new_sections = [
            s for s in existing_docs.get("sections", [])
            if s.get("slug") != section.slug
        ]
        new_sections.append(section.model_dump())
        existing_docs["sections"] = new_sections
        existing_docs["generatedAt"] = datetime.now(timezone.utc).isoformat()
        existing_docs["verified"] = False
    else:
        existing_docs = {
            "solutionId": sol_id,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sections": [section.model_dump()],
            "verified": False,
        }

    data["docs"] = existing_docs
    data["hasDocumentation"] = True
    solutions_store[sol_id] = data
    _save_solution_metadata(sol_id, data)

    return section


@app.get("/solutions/{sol_id}/docs")
def get_docs(sol_id: str):
    data = _get_solution_or_404(sol_id)
    if not data.get("docs"):
        raise HTTPException(status_code=404, detail="Documentation not yet generated")
    return GeneratedDocs(**data["docs"])


@app.post("/solutions/{sol_id}/verify")
def verify_docs(sol_id: str):
    data = _get_solution_or_404(sol_id)
    if not data.get("docs"):
        raise HTTPException(status_code=404, detail="Documentation not yet generated")

    kg = KnowledgeGraph(**data.get("knowledge_graph", {}))
    docs = GeneratedDocs(**data["docs"])

    result = verify_documentation(sol_id, kg, docs)

    if result.verified:
        data["docs"]["verified"] = True
        solutions_store[sol_id] = data
        _save_solution_metadata(sol_id, data)

    return result


@app.get("/solutions/{sol_id}/download/{fmt}")
def download_docs(sol_id: str, fmt: str):
    data = _get_solution_or_404(sol_id)
    if not data.get("docs"):
        raise HTTPException(status_code=404, detail="Documentation not yet generated")

    docs = GeneratedDocs(**data["docs"])
    solution_name = data.get("name", "Solution")
    safe_name = re.sub(r"[^\w\s-]", "", solution_name).strip().replace(" ", "_")
    sections_data = [s.model_dump() for s in docs.sections]

    if fmt == "docx":
        content = export_to_docx(sections_data, solution_name)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_Documentation.docx"'},
        )
    elif fmt == "pdf":
        content = export_to_pdf(sections_data, solution_name)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_Documentation.pdf"'},
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use 'docx' or 'pdf'.")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PYTHON_API_PORT", "5001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
