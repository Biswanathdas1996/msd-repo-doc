import os
import re
import json
import shutil
import tempfile
import asyncio
import hashlib
import logging
import time
import threading
import traceback
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("python_multipart").setLevel(logging.WARNING)

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import Response, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect

logger = logging.getLogger("upload")

from backend.models.schemas import (
    Solution, SolutionSummary, SolutionMetadata, SolutionStatus,
    KnowledgeGraph, Entity, Workflow, Plugin, Role, WebResource, FunctionalFlow,
    GeneratedDocs, GenerateDocsRequest, GenerateInsightRequest, VerificationResult,
    GitHubImportRequest, SolutionChatRequest, SolutionChatResponse,
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
from backend.services.generic_project_parser import parse_generic_project
from backend.services.knowledge_graph import build_knowledge_graph
from backend.services.flow_generator import generate_functional_flows
from backend.services.ai_reasoning import (
    generate_documentation,
    generate_single_section,
    verify_documentation,
    SECTION_CONFIGS,
    enrich_knowledge_graph_with_llm,
    generate_solution_insight,
    answer_solution_chat,
    SOLUTION_INSIGHT_KEYS,
)
from backend.services.doc_exporter import export_to_docx, export_to_pdf
from backend.services.claude_analyzer import run_advanced_analysis, regenerate_technical_specs


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
    root_path="/api/py-api",
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


UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "data", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)


def _process_solution(zip_path: str, solution_name: str, process_mode: str = "auto") -> Solution:
    try:
        result = extract_solution(zip_path, SOLUTIONS_DIR)
    except ValueError as e:
        if os.path.exists(zip_path):
            os.unlink(zip_path)
        raise HTTPException(status_code=400, detail=str(e))

    saved_zip = None
    if os.path.exists(zip_path):
        saved_zip = os.path.join(UPLOADS_DIR, f"{result['id']}.zip")
        shutil.copy2(zip_path, saved_zip)
        os.unlink(zip_path)

    try:
        return _build_solution_data(result, solution_name, process_mode=process_mode)
    except Exception:
        if saved_zip and os.path.exists(saved_zip):
            os.unlink(saved_zip)
        raise


def _build_solution_data(
    result: dict,
    solution_name: str,
    override_id: str | None = None,
    process_mode: str = "auto",
) -> Solution:
    sol_id = override_id or result["id"]
    final_name = solution_name or f"Solution {sol_id}"
    structure = result["structure"]
    is_source_code = result.get("is_source_code", False)
    generic_mode = process_mode == "generic"

    sol_metadata = parse_solution_xml(structure.get("solution_xml", ""))

    entities: list[Entity] = []
    workflows: list[Workflow] = []
    plugins: list[Plugin] = []
    forms: list[str] = []
    roles: list[Role] = []
    webresources: list[WebResource] = []
    source_info = {}
    form_details = []
    ax_classes_data: list[dict] = []
    ax_report_data: list[dict] = []
    ax_query_data: list[dict] = []

    if generic_mode:
        gp = parse_generic_project(result["output_folder"])
        entities = gp["entities"]
        workflows = gp["workflows"]
        plugins = gp["plugins"]
        sol_metadata = gp["metadata"]
    elif is_source_code:
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
        for qf in structure.get("ax_queries", []):
            q = parse_ax_query_file(qf)
            ax_query_data.append(q)
            for table_name in q.get("related_tables", []):
                if not any(e.name.lower() == table_name.lower() for e in entities):
                    entities.append(Entity(name=table_name, displayName=f"{table_name} (from query: {q['name']})"))

        # --- AX reports → feed data sources as references ---
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
    if other_xml_files and not generic_mode:
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

    # --- Build knowledge graph (heuristic relationships first) ---
    knowledge_graph = build_knowledge_graph(
        entities, workflows, plugins, forms, roles, webresources, form_details,
        ax_classes_data=ax_classes_data if structure.get("is_ax_fo") else None,
        ax_report_data=ax_report_data if structure.get("is_ax_fo") else None,
        ax_query_data=ax_query_data if structure.get("is_ax_fo") else None,
    )

    # --- LLM enrichment: discover deeper relationships (skipped for generic repos) ---
    if not generic_mode:
        try:
            knowledge_graph = enrich_knowledge_graph_with_llm(
                knowledge_graph,
                ax_classes_data=ax_classes_data if structure.get("is_ax_fo") else None,
                ax_report_data=ax_report_data if structure.get("is_ax_fo") else None,
            )
        except Exception:
            pass  # LLM enrichment is best-effort; heuristic graph still usable

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


def _background_process_solution(
    tmp_path: str,
    solution_name: str,
    sol_id: str,
    process_mode: str = "auto",
):
    """Run heavy extraction + parsing in a background thread so the upload
    response returns immediately after the file has been streamed to disk."""
    try:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        saved_zip = os.path.join(UPLOADS_DIR, f"{sol_id}.zip")
        if os.path.exists(tmp_path):
            shutil.copy2(tmp_path, saved_zip)

        result = extract_solution(tmp_path, SOLUTIONS_DIR)

        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

        old_extractor_id = result["id"]
        if old_extractor_id != sol_id and old_extractor_id in solutions_store:
            del solutions_store[old_extractor_id]
            old_meta = os.path.join(METADATA_DIR, f"{old_extractor_id}.json")
            if os.path.exists(old_meta):
                os.unlink(old_meta)

        sol_data = _build_solution_data(
            result, solution_name, override_id=sol_id, process_mode=process_mode
        )
        logger.info("Background processing complete for %s", sol_id)
    except Exception:
        logger.error("Background processing failed for %s:\n%s", sol_id, traceback.format_exc())
        if sol_id in solutions_store:
            solutions_store[sol_id]["status"] = "error"
            _save_solution_metadata(sol_id, solutions_store[sol_id])
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


_chunked_uploads: dict[str, dict] = {}
CHUNKED_UPLOAD_MAX_AGE = 7200

def _cleanup_stale_chunked_uploads():
    now = time.time()
    stale = [uid for uid, info in _chunked_uploads.items()
             if now - info["last_activity"] > CHUNKED_UPLOAD_MAX_AGE]
    for uid in stale:
        info = _chunked_uploads.pop(uid, None)
        if info and os.path.exists(info["tmp_path"]):
            os.unlink(info["tmp_path"])


@app.post("/solutions/upload/init")
async def chunked_upload_init(
    request: Request,
):
    body = await request.json()
    filename = body.get("filename", "")
    total_size = body.get("totalSize", 0)
    total_chunks = body.get("totalChunks", 0)
    name = body.get("name", "")
    process_mode = body.get("processMode", "auto")
    if process_mode not in ("auto", "generic"):
        process_mode = "auto"

    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP file")

    MAX_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024
    if total_size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 10 GB.")

    _cleanup_stale_chunked_uploads()

    upload_id = hashlib.md5(f"{filename}-{time.time()}-{os.urandom(8).hex()}".encode()).hexdigest()[:16]

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()

    chunk_size = 50 * 1024 * 1024
    _chunked_uploads[upload_id] = {
        "tmp_path": tmp.name,
        "filename": filename,
        "name": name,
        "total_size": total_size,
        "total_chunks": total_chunks,
        "chunk_size": chunk_size,
        "received_chunks": 0,
        "received_set": set(),
        "bytes_written": 0,
        "last_activity": time.time(),
        "process_mode": process_mode,
    }

    return {"uploadId": upload_id}


@app.post("/solutions/upload/chunk")
async def chunked_upload_chunk(
    request: Request,
    file: UploadFile = File(...),
    uploadId: str = Form(...),
    chunkIndex: int = Form(...),
):
    info = _chunked_uploads.get(uploadId)
    if not info:
        raise HTTPException(status_code=404, detail="Upload session not found or expired")

    if chunkIndex < 0 or chunkIndex >= info["total_chunks"]:
        raise HTTPException(status_code=400, detail=f"Invalid chunk index {chunkIndex}")

    info["last_activity"] = time.time()

    try:
        chunk_data = await file.read()
    except ClientDisconnect:
        raise HTTPException(status_code=499, detail="Client disconnected")

    chunk_size = info.get("chunk_size", 50 * 1024 * 1024)
    offset = chunkIndex * chunk_size
    with open(info["tmp_path"], "r+b" if os.path.getsize(info["tmp_path"]) > 0 else "wb") as f:
        f.seek(offset)
        f.write(chunk_data)

    received = info.setdefault("received_set", set())
    if chunkIndex not in received:
        received.add(chunkIndex)
        info["received_chunks"] = len(received)
    info["bytes_written"] = sum(1 for _ in received) * chunk_size

    return {
        "chunkIndex": chunkIndex,
        "receivedChunks": info["received_chunks"],
        "bytesWritten": info["bytes_written"],
    }


@app.post("/solutions/upload/finalize")
async def chunked_upload_finalize(
    request: Request,
):
    body = await request.json()
    upload_id = body.get("uploadId", "")
    name_override = body.get("name", "")

    info = _chunked_uploads.get(upload_id)
    if not info:
        raise HTTPException(status_code=404, detail="Upload session not found or expired")

    if info["received_chunks"] < info["total_chunks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Upload incomplete: received {info['received_chunks']}/{info['total_chunks']} chunks"
        )

    _chunked_uploads.pop(upload_id, None)

    tmp_path = info["tmp_path"]
    if not os.path.exists(tmp_path):
        raise HTTPException(status_code=500, detail="Temporary upload file missing")

    actual_size = os.path.getsize(tmp_path)
    if actual_size < info["total_size"]:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail="Upload file size mismatch")

    if actual_size > info["total_size"]:
        with open(tmp_path, "r+b") as f:
            f.truncate(info["total_size"])

    solution_name = name_override or info["name"] or info["filename"] or ""
    sol_id = hashlib.md5(f"{solution_name}-{time.time()}".encode()).hexdigest()[:8]
    process_mode = info.get("process_mode", "auto")
    if process_mode not in ("auto", "generic"):
        process_mode = "auto"

    placeholder = {
        "id": sol_id,
        "name": solution_name,
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "status": "processing",
        "entityCount": 0,
        "workflowCount": 0,
        "pluginCount": 0,
        "formCount": 0,
        "roleCount": 0,
        "webResourceCount": 0,
        "hasDocumentation": False,
        "metadata": None,
    }
    solutions_store[sol_id] = placeholder
    _save_solution_metadata(sol_id, placeholder)

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _background_process_solution,
        tmp_path,
        solution_name,
        sol_id,
        process_mode,
    )

    return SolutionSummary(
        id=sol_id,
        name=solution_name,
        uploadedAt=placeholder["uploadedAt"],
        entityCount=0,
        workflowCount=0,
        pluginCount=0,
        hasDocumentation=False,
        status=SolutionStatus.processing,
    )


@app.post("/solutions/upload")
async def upload_solution(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(default=""),
    processMode: str = Form(default="auto"),
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP file")

    MAX_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
    CHUNK_SIZE = 32 * 1024 * 1024  # 32 MB

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
            total_written = 0
            while True:
                try:
                    chunk = await file.read(CHUNK_SIZE)
                except ClientDisconnect:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise HTTPException(status_code=499, detail="Client disconnected")

                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    tmp.close()
                    os.unlink(tmp_path)
                    raise HTTPException(status_code=400, detail="File too large. Max 10 GB.")
                tmp.write(chunk)

                await asyncio.sleep(0)
    except HTTPException:
        raise
    except Exception as exc:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")

    solution_name = name or file.filename or ""
    sol_id = hashlib.md5(f"{solution_name}-{time.time()}".encode()).hexdigest()[:8]
    pm = processMode if processMode in ("auto", "generic") else "auto"

    placeholder = {
        "id": sol_id,
        "name": solution_name,
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "status": "processing",
        "entityCount": 0,
        "workflowCount": 0,
        "pluginCount": 0,
        "formCount": 0,
        "roleCount": 0,
        "webResourceCount": 0,
        "hasDocumentation": False,
        "metadata": None,
    }
    solutions_store[sol_id] = placeholder
    _save_solution_metadata(sol_id, placeholder)

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _background_process_solution,
        tmp_path,
        solution_name,
        sol_id,
        pm,
    )

    return SolutionSummary(
        id=sol_id,
        name=solution_name,
        uploadedAt=placeholder["uploadedAt"],
        entityCount=0,
        workflowCount=0,
        pluginCount=0,
        hasDocumentation=False,
        status=SolutionStatus.processing,
    )


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
    pm = body.processMode if body.processMode in ("auto", "generic") else "auto"
    return _process_solution(zip_path, solution_name, process_mode=pm)


@app.get("/solutions")
def list_solutions(projectKind: str | None = None):
    summaries = []
    for sol_id, data in solutions_store.items():
        meta = data.get("metadata") or {}
        mtype = meta.get("type")
        if projectKind == "generic":
            if mtype != "generic_code":
                continue
        elif projectKind == "dynamics":
            if mtype == "generic_code":
                continue
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


@app.get("/solutions/{sol_id}/download/check")
def check_download_available(sol_id: str):
    _get_solution_or_404(sol_id)
    zip_path = os.path.join(UPLOADS_DIR, f"{sol_id}.zip")
    available = os.path.exists(zip_path)
    return {"available": available, "size": os.path.getsize(zip_path) if available else 0}


@app.get("/solutions/{sol_id}/download")
def download_solution_zip(sol_id: str):
    data = _get_solution_or_404(sol_id)
    zip_path = os.path.join(UPLOADS_DIR, f"{sol_id}.zip")
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="Original ZIP file not available for download")
    safe_name = data.get("name", sol_id).replace(" ", "_") + ".zip"
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=safe_name,
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

    zip_file = os.path.join(UPLOADS_DIR, f"{sol_id}.zip")
    if os.path.exists(zip_file):
        os.unlink(zip_file)

    del solutions_store[sol_id]
    return {"message": f"Solution {sol_id} deleted"}


@app.post("/solutions/{sol_id}/reprocess")
def reprocess_solution(sol_id: str):
    data = _get_solution_or_404(sol_id)
    output_folder = data.get("output_folder")
    if not output_folder or not os.path.exists(output_folder):
        raise HTTPException(status_code=400, detail="Solution source files not found on disk")

    meta_type = (data.get("metadata") or {}).get("type")
    is_source_code = meta_type == "source_code"
    is_generic = meta_type == "generic_code"
    entities: list[Entity] = []
    workflows: list[Workflow] = []
    plugins: list[Plugin] = []
    roles: list[Role] = []
    webresources: list[WebResource] = []
    forms: list[str] = data.get("forms", [])

    if is_generic:
        gp = parse_generic_project(output_folder)
        entities = gp["entities"]
        workflows = gp["workflows"]
        plugins = gp["plugins"]
    elif is_source_code:
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


@app.get("/solutions/{sol_id}/insights")
def get_solution_insights(sol_id: str):
    """Cached PwC Gen AI outputs for Features, Feature connections, and Flow diagrams."""
    data = _get_solution_or_404(sol_id)
    return data.get("solution_insights") or {}


@app.post("/solutions/{sol_id}/insights/generate")
def generate_solution_insight_endpoint(sol_id: str, body: GenerateInsightRequest):
    key = (body.insightType or "").strip()
    if key not in SOLUTION_INSIGHT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown insight type: {key!r}. Use one of: {list(SOLUTION_INSIGHT_KEYS)}",
        )
    data = _get_solution_or_404(sol_id)
    kg = KnowledgeGraph(**data.get("knowledge_graph", {}))
    flows = data.get("functional_flows") or []

    try:
        content = generate_solution_insight(key, kg, flows)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    insights = dict(data.get("solution_insights") or {})
    insights[key] = {
        "content": content,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
    data["solution_insights"] = insights
    solutions_store[sol_id] = data
    _save_solution_metadata(sol_id, data)
    return insights[key]


@app.post("/solutions/{sol_id}/chat", response_model=SolutionChatResponse)
def solution_project_chat(sol_id: str, body: SolutionChatRequest):
    """Answer questions using only this solution's parsed graph, flows, and generated docs/insights."""
    data = _get_solution_or_404(sol_id)
    if data.get("status") != "ready":
        raise HTTPException(
            status_code=400,
            detail="Solution is not ready yet. Wait for parsing to finish.",
        )
    kg = KnowledgeGraph(**data.get("knowledge_graph", {}))
    flows = data.get("functional_flows") or []
    docs_raw = data.get("docs")
    insights = data.get("solution_insights")
    meta = data.get("metadata") or {}
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump()

    history: list[dict] = []
    for m in body.history[-12:]:
        r = (m.role or "").strip().lower()
        if r not in ("user", "assistant"):
            continue
        history.append({"role": r, "content": (m.content or "")[:6000]})

    try:
        answer = answer_solution_chat(
            kg,
            flows,
            body.message.strip(),
            history,
            solution_name=data.get("name") or "",
            metadata=meta if isinstance(meta, dict) else {},
            docs=docs_raw,
            solution_insights=insights if isinstance(insights, dict) else {},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return SolutionChatResponse(answer=answer)


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


# ═══════════════════════════════════════════════════════════════════════════════
# ADVANCED DOCS — Claude Code CLI powered analysis (completely separate feature)
# ═══════════════════════════════════════════════════════════════════════════════

ADVANCED_DOCS_DIR = os.path.join(DATA_DIR, "advanced_docs")
ADVANCED_DOCS_METADATA_DIR = os.path.join(DATA_DIR, "advanced_docs_metadata")
os.makedirs(ADVANCED_DOCS_DIR, exist_ok=True)
os.makedirs(ADVANCED_DOCS_METADATA_DIR, exist_ok=True)

advanced_docs_store: dict[str, dict] = {}


# ── SSE event store (thread-safe) ─────────────────────────────────────────────

class _SSEStore:
    """Thread-safe event buffer shared between the background worker and the
    async SSE endpoint."""

    def __init__(self):
        self._lock = threading.Lock()
        self.events: list[dict] = []
        self.done = False

    def push(self, event_type: str, data: dict):
        with self._lock:
            self.events.append({"event": event_type, "data": data})

    def finish(self):
        with self._lock:
            self.done = True

    def read_from(self, cursor: int) -> tuple[list[dict], bool]:
        with self._lock:
            return self.events[cursor:], self.done


_sse_stores: dict[str, _SSEStore] = {}
_SSE_STORE_TTL = 600  # clean up stores 10 min after done

# One in-flight partial regeneration per advanced-doc (e.g. technical_specs only)
_advanced_section_jobs: dict[str, str] = {}


def _cleanup_old_sse_stores():
    stale = [k for k, v in _sse_stores.items() if v.done]
    for k in stale[:max(0, len(stale) - 5)]:
        _sse_stores.pop(k, None)


# ── Persistence helpers ───────────────────────────────────────────────────────

def _sanitize_advanced_doc_section_jobs(data: dict) -> None:
    """In-memory section jobs cannot survive a restart; stale 'running' blocks the UI."""
    sj = data.get("section_jobs")
    if not isinstance(sj, dict):
        return
    if sj.get("technical_specs") == "running":
        sj.pop("technical_specs", None)
        sj.pop("technical_specs_started_at", None)
        if not sj:
            data.pop("section_jobs", None)


def _load_advanced_docs():
    if not os.path.exists(ADVANCED_DOCS_METADATA_DIR):
        return
    for fname in os.listdir(ADVANCED_DOCS_METADATA_DIR):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(ADVANCED_DOCS_METADATA_DIR, fname)) as f:
                    data = json.load(f)
                    _sanitize_advanced_doc_section_jobs(data)
                    advanced_docs_store[data["id"]] = data
            except Exception:
                pass

_load_advanced_docs()


def _save_advanced_doc(doc_id: str, data: dict):
    os.makedirs(ADVANCED_DOCS_METADATA_DIR, exist_ok=True)
    with open(os.path.join(ADVANCED_DOCS_METADATA_DIR, f"{doc_id}.json"), "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Background worker ─────────────────────────────────────────────────────────

def _background_advanced_analysis(zip_path: str, project_name: str, doc_id: str):
    """Run Claude CLI analysis in background thread, emitting SSE events."""

    store = _sse_stores.get(doc_id)

    def _on_progress(partial: dict):
        partial["id"] = doc_id
        advanced_docs_store[doc_id] = partial
        _save_advanced_doc(doc_id, partial)

    def _on_event(event_type: str, data: dict):
        if store:
            store.push(event_type, data)

    try:
        _on_event("connected", {"id": doc_id, "name": project_name})

        result = run_advanced_analysis(
            zip_path, project_name, ADVANCED_DOCS_DIR,
            on_progress=_on_progress,
            on_event=_on_event,
        )
        result["id"] = doc_id
        advanced_docs_store[doc_id] = result
        _save_advanced_doc(doc_id, result)
        logger.info("Advanced analysis complete for %s (status=%s)", doc_id, result.get("status"))
    except Exception:
        logger.error("Advanced analysis failed for %s:\n%s", doc_id, traceback.format_exc())
        if doc_id in advanced_docs_store:
            advanced_docs_store[doc_id]["status"] = "error"
            advanced_docs_store[doc_id]["error"] = traceback.format_exc()
            _save_advanced_doc(doc_id, advanced_docs_store[doc_id])
        _on_event("done", {"id": doc_id, "status": "error", "error": "Internal error"})
    finally:
        if store:
            store.finish()
        if os.path.exists(zip_path):
            try:
                os.unlink(zip_path)
            except OSError:
                pass


def _background_regen_technical_specs(doc_id: str):
    logger.info("Technical specs regeneration thread started for %s", doc_id)
    try:
        doc = advanced_docs_store.get(doc_id)
        if not doc:
            logger.warning("Technical specs regen: doc %s gone from store", doc_id)
            return
        specs = regenerate_technical_specs(doc)
        doc = advanced_docs_store.get(doc_id)
        if not doc:
            return
        doc["technical_specs"] = specs
        doc.setdefault("step_errors", {}).pop("technical_specs", None)
        completed = doc.setdefault("completed_steps", [])
        if "technical_specs" not in completed:
            completed.append("technical_specs")
        logger.info("Technical specs regenerated for advanced doc %s", doc_id)
    except ValueError as e:
        logger.warning("Technical specs regeneration failed for %s: %s", doc_id, e)
        doc = advanced_docs_store.get(doc_id)
        if doc:
            doc.setdefault("step_errors", {})["technical_specs"] = str(e)
    except Exception:
        logger.error("Technical specs regeneration failed for %s:\n%s", doc_id, traceback.format_exc())
        doc = advanced_docs_store.get(doc_id)
        if doc:
            doc.setdefault("step_errors", {})["technical_specs"] = traceback.format_exc()
    finally:
        _advanced_section_jobs.pop(doc_id, None)
        doc = advanced_docs_store.get(doc_id)
        if doc:
            sj = doc.setdefault("section_jobs", {})
            sj.pop("technical_specs", None)
            sj.pop("technical_specs_started_at", None)
            if not sj:
                doc.pop("section_jobs", None)
            try:
                _save_advanced_doc(doc_id, doc)
            except Exception:
                logger.exception("Failed to persist advanced doc %s after technical specs regen", doc_id)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/advanced-docs/upload/init")
async def advanced_docs_upload_init(request: Request):
    """Initialize chunked upload for advanced Claude-powered analysis."""
    body = await request.json()
    filename = body.get("filename", "")
    total_size = body.get("totalSize", 0)
    total_chunks = body.get("totalChunks", 0)
    name = body.get("name", "")

    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP file")

    _cleanup_stale_chunked_uploads()

    upload_id = hashlib.md5(f"adv-{filename}-{time.time()}-{os.urandom(8).hex()}".encode()).hexdigest()[:16]

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()

    chunk_size = 5 * 1024 * 1024
    _chunked_uploads[upload_id] = {
        "tmp_path": tmp.name,
        "filename": filename,
        "name": name,
        "total_size": total_size,
        "total_chunks": total_chunks,
        "chunk_size": chunk_size,
        "received_chunks": 0,
        "received_set": set(),
        "bytes_written": 0,
        "last_activity": time.time(),
        "type": "advanced",
    }

    return {"uploadId": upload_id}


@app.post("/advanced-docs/upload/chunk")
async def advanced_docs_upload_chunk(
    request: Request,
    file: UploadFile = File(...),
    uploadId: str = Form(...),
    chunkIndex: int = Form(...),
):
    """Receive a single chunk for advanced docs upload."""
    info = _chunked_uploads.get(uploadId)
    if not info or info.get("type") != "advanced":
        raise HTTPException(status_code=404, detail="Upload session not found or expired")

    if chunkIndex < 0 or chunkIndex >= info["total_chunks"]:
        raise HTTPException(status_code=400, detail=f"Invalid chunk index {chunkIndex}")

    info["last_activity"] = time.time()

    try:
        chunk_data = await file.read()
    except ClientDisconnect:
        raise HTTPException(status_code=499, detail="Client disconnected")

    chunk_size = info.get("chunk_size", 5 * 1024 * 1024)
    offset = chunkIndex * chunk_size
    with open(info["tmp_path"], "r+b" if os.path.getsize(info["tmp_path"]) > 0 else "wb") as f:
        f.seek(offset)
        f.write(chunk_data)

    received = info.setdefault("received_set", set())
    if chunkIndex not in received:
        received.add(chunkIndex)
        info["received_chunks"] = len(received)

    return {
        "chunkIndex": chunkIndex,
        "receivedChunks": info["received_chunks"],
        "totalChunks": info["total_chunks"],
    }


@app.post("/advanced-docs/upload/keepalive")
async def advanced_docs_upload_keepalive(request: Request):
    """Keep the upload session alive during slow transfers."""
    body = await request.json()
    upload_id = body.get("uploadId", "")
    info = _chunked_uploads.get(upload_id)
    if not info or info.get("type") != "advanced":
        raise HTTPException(status_code=404, detail="Upload session not found or expired")
    info["last_activity"] = time.time()
    return {
        "status": "alive",
        "receivedChunks": info["received_chunks"],
        "totalChunks": info["total_chunks"],
    }


@app.post("/advanced-docs/upload/finalize")
async def advanced_docs_upload_finalize(request: Request):
    """Finalize chunked upload and start advanced analysis."""
    body = await request.json()
    upload_id = body.get("uploadId", "")
    name_override = body.get("name", "")

    info = _chunked_uploads.get(upload_id)
    if not info or info.get("type") != "advanced":
        raise HTTPException(status_code=404, detail="Upload session not found or expired")

    if info["received_chunks"] < info["total_chunks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Upload incomplete: received {info['received_chunks']}/{info['total_chunks']} chunks"
        )

    _chunked_uploads.pop(upload_id, None)

    tmp_path = info["tmp_path"]
    if not os.path.exists(tmp_path):
        raise HTTPException(status_code=500, detail="Temporary upload file missing")

    actual_size = os.path.getsize(tmp_path)
    if actual_size < info["total_size"]:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail="Upload file size mismatch")

    if actual_size > info["total_size"]:
        with open(tmp_path, "r+b") as f:
            f.truncate(info["total_size"])

    project_name = name_override or info["name"] or info["filename"] or "Unnamed Project"
    doc_id = hashlib.md5(f"adv-{project_name}-{time.time()}".encode()).hexdigest()[:10]

    placeholder = {
        "id": doc_id,
        "name": project_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "processing",
        "file_count": 0,
        "files": [],
        "project_tree": "",
        "knowledge_graph": {},
        "features": {},
        "feature_connections": {},
        "flow_diagrams": {},
        "technical_specs": {},
        "documentation": "",
        "step_errors": {},
        "completed_steps": [],
        "current_step": "",
    }
    advanced_docs_store[doc_id] = placeholder
    _save_advanced_doc(doc_id, placeholder)

    _cleanup_old_sse_stores()
    _sse_stores[doc_id] = _SSEStore()

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _background_advanced_analysis, tmp_path, project_name, doc_id)

    return {"id": doc_id, "name": project_name, "status": "processing"}


@app.post("/advanced-docs-import")
async def import_advanced_doc(request: Request):
    """Import a previously exported advanced documentation report JSON."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(data, dict) or "name" not in data:
        raise HTTPException(status_code=400, detail="Invalid report format — missing 'name' field")

    doc_id = hashlib.md5(f"import-{data.get('name','')}-{time.time()}".encode()).hexdigest()[:10]

    data["id"] = doc_id
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    data.pop("output_folder", None)
    data.setdefault("status", data.get("status", "ready"))
    data.setdefault("file_count", data.get("file_count", 0))
    data.setdefault("files", data.get("files", []))
    data.setdefault("project_tree", data.get("project_tree", ""))
    data.setdefault("knowledge_graph", data.get("knowledge_graph", {}))
    data.setdefault("features", data.get("features", {}))
    data.setdefault("feature_connections", data.get("feature_connections", {}))
    data.setdefault("flow_diagrams", data.get("flow_diagrams", {}))
    data.setdefault("technical_specs", data.get("technical_specs", {}))
    data.setdefault("documentation", data.get("documentation", ""))

    advanced_docs_store[doc_id] = data
    _save_advanced_doc(doc_id, data)

    return {"id": doc_id, "name": data["name"], "status": data["status"]}


@app.get("/advanced-docs/{doc_id}/stream")
async def stream_advanced_doc(doc_id: str):
    """SSE endpoint — streams real-time step events for a processing doc."""
    if doc_id not in advanced_docs_store:
        raise HTTPException(status_code=404, detail="Advanced doc project not found")

    store = _sse_stores.get(doc_id)

    async def _generate():
        # If there's no SSE store (already finished before client connected),
        # replay the final state from the stored result as synthetic events.
        if store is None:
            data = advanced_docs_store.get(doc_id, {})
            yield f"event: connected\ndata: {json.dumps({'id': doc_id, 'name': data.get('name', '')}, default=str)}\n\n"
            for step_key in ["extraction", "knowledge_graph", "features",
                             "cross_validation", "feature_connections",
                             "flow_diagrams", "technical_specs",
                             "documentation", "quality_check"]:
                step_errors = data.get("step_errors", {})
                completed = data.get("completed_steps", [])
                if step_key in completed:
                    yield f"event: step_complete\ndata: {json.dumps({'step': step_key, 'summary': 'completed'}, default=str)}\n\n"
                elif step_key in step_errors:
                    yield f"event: step_error\ndata: {json.dumps({'step': step_key, 'error': step_errors[step_key]}, default=str)}\n\n"
            yield f"event: done\ndata: {json.dumps({'id': doc_id, 'status': data.get('status', 'ready')}, default=str)}\n\n"
            return

        cursor = 0
        heartbeat_interval = 15
        last_heartbeat = time.time()
        while True:
            new_events, done = store.read_from(cursor)
            for ev in new_events:
                payload = json.dumps(ev["data"], default=str)
                yield f"event: {ev['event']}\ndata: {payload}\n\n"
                cursor += 1
                last_heartbeat = time.time()
            if done and not new_events:
                break
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                yield f"event: heartbeat\ndata: {json.dumps({'ts': int(now * 1000)})}\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/advanced-docs")
def list_advanced_docs():
    """List all advanced documentation projects."""
    return [
        {
            "id": d["id"],
            "name": d["name"],
            "created_at": d.get("created_at", ""),
            "status": d.get("status", "processing"),
            "file_count": d.get("file_count", 0),
        }
        for d in advanced_docs_store.values()
    ]


@app.get("/advanced-docs/{doc_id}")
def get_advanced_doc(doc_id: str):
    """Get full advanced documentation result."""
    if doc_id not in advanced_docs_store:
        raise HTTPException(status_code=404, detail="Advanced doc project not found")
    return advanced_docs_store[doc_id]


@app.post("/advanced-docs/{doc_id}/regenerate/technical-specs")
async def regenerate_advanced_technical_specs(doc_id: str):
    """Re-run only the technical specifications step (Claude CLI). Runs in a thread; poll GET for results."""
    if doc_id not in advanced_docs_store:
        raise HTTPException(status_code=404, detail="Advanced doc project not found")
    if _advanced_section_jobs.get(doc_id):
        raise HTTPException(
            status_code=409,
            detail="A section regeneration is already running for this project.",
        )

    doc = advanced_docs_store[doc_id]
    if doc.get("status") == "processing":
        raise HTTPException(
            status_code=409,
            detail="Full analysis is still running; wait for it to finish.",
        )

    folder = doc.get("output_folder")
    if not folder or not os.path.isdir(folder):
        raise HTTPException(
            status_code=400,
            detail="Extracted project folder is missing. Re-upload the ZIP.",
        )

    _advanced_section_jobs[doc_id] = "technical_specs"
    sj = doc.setdefault("section_jobs", {})
    sj["technical_specs"] = "running"
    sj["technical_specs_started_at"] = datetime.now(timezone.utc).isoformat()
    _save_advanced_doc(doc_id, doc)

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _background_regen_technical_specs, doc_id)
    return {"accepted": True, "section": "technical_specs"}


@app.get("/advanced-docs/{doc_id}/status")
def get_advanced_doc_status(doc_id: str):
    """Get processing status of an advanced doc project."""
    if doc_id not in advanced_docs_store:
        raise HTTPException(status_code=404, detail="Advanced doc project not found")
    d = advanced_docs_store[doc_id]
    return {
        "id": d["id"],
        "status": d.get("status", "processing"),
        "error": d.get("error"),
    }


@app.delete("/advanced-docs/{doc_id}")
def delete_advanced_doc(doc_id: str):
    """Delete an advanced documentation project."""
    if doc_id not in advanced_docs_store:
        raise HTTPException(status_code=404, detail="Advanced doc project not found")

    data = advanced_docs_store[doc_id]
    output_folder = data.get("output_folder")
    if output_folder and os.path.exists(output_folder):
        shutil.rmtree(output_folder, ignore_errors=True)

    meta_file = os.path.join(ADVANCED_DOCS_METADATA_DIR, f"{doc_id}.json")
    if os.path.exists(meta_file):
        os.unlink(meta_file)

    _sse_stores.pop(doc_id, None)
    del advanced_docs_store[doc_id]
    return {"message": f"Advanced doc {doc_id} deleted"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PYTHON_API_PORT", "5001"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        timeout_keep_alive=300,        # keep connections alive for slow uploads
        h11_max_incomplete_event_size=0, # no limit on header/body buffering
    )
