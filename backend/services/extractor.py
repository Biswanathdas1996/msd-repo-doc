import zipfile
import os
import shutil
import uuid
from datetime import datetime, timezone

MAX_ZIP_SIZE = 100 * 1024 * 1024
MAX_ENTRY_COUNT = 5000
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024


def extract_solution(zip_file_path: str, base_output_folder: str) -> dict:
    file_size = os.path.getsize(zip_file_path)
    if file_size > MAX_ZIP_SIZE:
        raise ValueError(f"ZIP file too large ({file_size} bytes). Max allowed: {MAX_ZIP_SIZE} bytes.")

    solution_id = str(uuid.uuid4())[:8]
    output_folder = os.path.join(base_output_folder, solution_id)
    os.makedirs(output_folder, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
            entries = zip_ref.infolist()
            if len(entries) > MAX_ENTRY_COUNT:
                raise ValueError(f"ZIP has too many entries ({len(entries)}). Max: {MAX_ENTRY_COUNT}.")

            total_uncompressed = sum(e.file_size for e in entries)
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError(f"Total uncompressed size too large ({total_uncompressed} bytes).")

            resolved_output = os.path.realpath(output_folder)
            for entry in entries:
                target_path = os.path.realpath(os.path.join(output_folder, entry.filename))
                if not target_path.startswith(resolved_output + os.sep) and target_path != resolved_output:
                    raise ValueError(f"Zip Slip detected: {entry.filename}")

            zip_ref.extractall(output_folder)
    except zipfile.BadZipFile:
        shutil.rmtree(output_folder, ignore_errors=True)
        raise ValueError("Invalid ZIP file")
    except ValueError:
        shutil.rmtree(output_folder, ignore_errors=True)
        raise

    structure = analyze_structure(output_folder)

    return {
        "id": solution_id,
        "output_folder": output_folder,
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "structure": structure,
        "is_source_code": _is_source_code_repo(structure, output_folder)
    }


def analyze_structure(folder: str) -> dict:
    structure = {
        "entities": [],
        "workflows": [],
        "plugins": [],
        "forms": [],
        "webresources": [],
        "roles": [],
        "other_xml": [],
        "solution_xml": None
    }

    for root, dirs, files in os.walk(folder):
        rel_root = os.path.relpath(root, folder).lower()
        for f in files:
            full_path = os.path.join(root, f)
            fl = f.lower()

            if fl == "solution.xml" and rel_root == ".":
                structure["solution_xml"] = full_path
            elif fl.endswith(".xml"):
                if "entit" in rel_root or "entit" in fl:
                    structure["entities"].append(full_path)
                elif "workflow" in rel_root or "workflow" in fl or "process" in rel_root:
                    structure["workflows"].append(full_path)
                elif "plugin" in rel_root or "plugin" in fl or "assembly" in rel_root:
                    structure["plugins"].append(full_path)
                elif "form" in rel_root or "form" in fl:
                    structure["forms"].append(full_path)
                elif "webresource" in rel_root or "webresource" in fl:
                    structure["webresources"].append(full_path)
                elif "role" in rel_root or "role" in fl:
                    structure["roles"].append(full_path)
                else:
                    structure["other_xml"].append(full_path)

    if not structure["entities"] and not structure["workflows"]:
        for root, dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".xml"):
                    full_path = os.path.join(root, f)
                    if full_path not in [structure["solution_xml"]]:
                        structure["other_xml"].append(full_path)

    return structure


def _is_source_code_repo(structure: dict, folder: str) -> bool:
    has_xml_content = bool(structure["entities"] or structure["workflows"] or structure["plugins"] or structure["solution_xml"])
    if has_xml_content:
        return False

    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(".cs") or f.endswith(".csproj") or f.endswith(".crmregister"):
                return True
    return False
