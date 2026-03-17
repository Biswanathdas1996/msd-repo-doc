import os
import json
import shutil
import tempfile
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.models.schemas import (
    Solution, SolutionSummary, SolutionMetadata, SolutionStatus,
    KnowledgeGraph, Entity, Workflow, Plugin, FunctionalFlow,
    GeneratedDocs, GenerateDocsRequest, VerificationResult,
    GitHubImportRequest
)
from backend.services.extractor import extract_solution
from backend.services.github_downloader import download_github_repo
from backend.services.xml_parser import (
    parse_solution_xml, parse_entity_file, parse_workflow_file,
    parse_plugin_file, parse_form_files,
    parse_ax_class_file, parse_ax_table_file, parse_ax_view_file,
    ax_classes_to_plugins
)
from backend.services.source_code_parser import parse_source_code_repo
from backend.services.knowledge_graph import build_knowledge_graph
from backend.services.flow_generator import generate_functional_flows
from backend.services.ai_reasoning import generate_documentation, verify_documentation


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
    source_info = {}

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

        if not sol_metadata:
            sol_metadata = {}
        sol_metadata["type"] = "ax_fo"
        sol_metadata["description"] = "Dynamics 365 Finance & Operations (X++) solution"
        sol_metadata["ax_class_count"] = len(ax_classes_data)
        sol_metadata["ax_table_count"] = len(structure.get("ax_tables", []))
        sol_metadata["ax_view_count"] = len(structure.get("ax_views", []))
    else:
        for ef in structure.get("entities", []):
            entities.extend(parse_entity_file(ef))

        for wf in structure.get("workflows", []):
            workflows.extend(parse_workflow_file(wf))

        for pf in structure.get("plugins", []):
            plugins.extend(parse_plugin_file(pf))

        forms = parse_form_files(structure.get("forms", []))

    knowledge_graph = build_knowledge_graph(entities, workflows, plugins, forms)
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
        "roleCount": len(structure.get("roles", [])),
        "webResourceCount": len(structure.get("webresources", [])),
        "hasDocumentation": False,
        "metadata": sol_metadata,
        "entities": [e.model_dump() for e in entities],
        "workflows": [w.model_dump() for w in workflows],
        "plugins": [p.model_dump() for p in plugins],
        "forms": forms,
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

    knowledge_graph = build_knowledge_graph(entities, workflows, plugins, forms)
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PYTHON_API_PORT", "5001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
