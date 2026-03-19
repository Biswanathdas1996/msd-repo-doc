import zipfile
import os
import shutil
import uuid
import logging
from lxml import etree
from datetime import datetime, timezone

logger = logging.getLogger("extractor")

MAX_ZIP_SIZE = 10 * 1024 * 1024 * 1024
MAX_ENTRY_COUNT = 50000
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024 * 1024

# ---------------------------------------------------------------------------
# Post-extraction cleansing
# ---------------------------------------------------------------------------

# Junk / OS / IDE files that are never useful for processing
JUNK_FILE_NAMES = {
    ".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".gitignore",
    ".gitattributes", ".editorconfig", ".npmrc", ".yarnrc",
    "license", "license.md", "license.txt",
    "readme.md", "readme.txt", "readme",
    "changelog.md", "changelog.txt",
    ".eslintrc", ".eslintrc.json", ".eslintrc.js",
    ".prettierrc", ".prettierrc.json",
    ".babelrc", ".stylelintrc",
}

JUNK_DIR_NAMES = {
    "__macosx", ".git", ".svn", ".hg", ".idea", ".vscode",
    "node_modules", "__pycache__", ".vs", "bin", "obj",
    ".terraform", ".tox",
}

JUNK_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",  # archives
    ".exe", ".dll", ".so", ".dylib", ".pdb",                # binaries
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",  # images
    ".mp3", ".mp4", ".wav", ".avi", ".mov",                  # media
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",  # office docs
    ".log", ".tmp", ".bak", ".swp",                          # temp files
}


def _cleanse_extracted_folder(folder: str) -> dict:
    """Remove nested zips, junk directories, and unnecessary files after extraction.

    Returns a summary dict with counts of removed items.
    """
    removed_zips = 0
    removed_dirs = 0
    removed_files = 0

    # --- Pass 1: remove entire junk directories (top-down so we can prune) ---
    for root_dir, dirs, _files in os.walk(folder, topdown=True):
        dirs_to_remove = []
        for d in dirs:
            if d.lower() in JUNK_DIR_NAMES:
                dir_path = os.path.join(root_dir, d)
                shutil.rmtree(dir_path, ignore_errors=True)
                removed_dirs += 1
                dirs_to_remove.append(d)
                logger.info("Cleanse: removed junk directory %s", dir_path)
        # Prune so os.walk doesn't descend into removed dirs
        for d in dirs_to_remove:
            dirs.remove(d)

    # --- Pass 2: remove junk / unnecessary files ---
    for root_dir, _dirs, files in os.walk(folder):
        for f in files:
            fl = f.lower()
            full_path = os.path.join(root_dir, f)
            _name, ext = os.path.splitext(fl)

            # 1) Nested zip / archive files at any level
            if ext in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}:
                try:
                    os.remove(full_path)
                    removed_zips += 1
                    logger.info("Cleanse: removed nested archive %s", full_path)
                except OSError:
                    pass
                continue

            # 2) Junk files by exact name
            if fl in JUNK_FILE_NAMES:
                try:
                    os.remove(full_path)
                    removed_files += 1
                    logger.info("Cleanse: removed junk file %s", full_path)
                except OSError:
                    pass
                continue

            # 3) Junk files by extension
            if ext in JUNK_EXTENSIONS:
                try:
                    os.remove(full_path)
                    removed_files += 1
                    logger.info("Cleanse: removed unnecessary file %s", full_path)
                except OSError:
                    pass
                continue

    # --- Pass 3: remove empty directories left behind ---
    for root_dir, dirs, files in os.walk(folder, topdown=False):
        if root_dir == folder:
            continue
        if not os.listdir(root_dir):
            try:
                os.rmdir(root_dir)
                logger.info("Cleanse: removed empty directory %s", root_dir)
            except OSError:
                pass

    summary = {
        "removed_zips": removed_zips,
        "removed_dirs": removed_dirs,
        "removed_files": removed_files,
    }
    logger.info("Cleanse summary: %s", summary)
    return summary

AX_CLASS_TAGS = {"axclass"}
AX_TABLE_TAGS = {"axtable", "axtableextension"}
AX_VIEW_TAGS = {"axview", "axviewextension"}
AX_FORM_TAGS = {"axform", "axformextension"}
AX_ENTITY_TAGS = {"axdataentityview", "axdataentityviewextension"}
AX_WORKFLOW_TAGS = {"axworkflowcategory", "axworkflowtype", "axworkflowtemplate", "axworkflowapproval", "axworkflowtask"}
AX_MENU_TAGS = {"axmenuitem", "axmenuitemextension", "axmenu", "axmenuextension"}
AX_SECURITY_TAGS = {"axsecurityrole", "axsecurityduty", "axsecurityprivilege", "axsecuritypolicy"}
AX_QUERY_TAGS = {"axquery", "axqueryextension"}
AX_SSRS_TAGS = {"axreport", "axreportextension"}

ALL_AX_TAGS = (
    AX_CLASS_TAGS | AX_TABLE_TAGS | AX_VIEW_TAGS | AX_FORM_TAGS |
    AX_ENTITY_TAGS | AX_WORKFLOW_TAGS | AX_MENU_TAGS |
    AX_SECURITY_TAGS | AX_QUERY_TAGS | AX_SSRS_TAGS
)


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

    # --- Post-extraction cleansing ---
    cleanse_summary = _cleanse_extracted_folder(output_folder)
    logger.info("Post-extract cleanse for %s: %s", solution_id, cleanse_summary)

    structure = analyze_structure(output_folder)

    return {
        "id": solution_id,
        "output_folder": output_folder,
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "structure": structure,
        "is_source_code": _is_source_code_repo(structure, output_folder)
    }


def _detect_ax_root_tag(file_path: str) -> str | None:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                  dtd_validation=False, load_dtd=False, huge_tree=False)
        tree = etree.parse(file_path, parser)
        root = tree.getroot()
        tag = root.tag
        if "}" in tag:
            tag = tag.split("}")[1]
        return tag.lower()
    except Exception:
        return None


def analyze_structure(folder: str) -> dict:
    structure = {
        "entities": [],
        "workflows": [],
        "plugins": [],
        "forms": [],
        "webresources": [],
        "roles": [],
        "other_xml": [],
        "solution_xml": None,
        "customizations_xml": None,
        "xaml_workflows": [],
        "ax_classes": [],
        "ax_tables": [],
        "ax_views": [],
        "ax_data_entities": [],
        "ax_queries": [],
        "ax_reports": [],
        "is_ax_fo": False,
    }

    unclassified_xml = []

    for root_dir, dirs, files in os.walk(folder):
        rel_root = os.path.relpath(root_dir, folder).lower()
        for f in files:
            full_path = os.path.join(root_dir, f)
            fl = f.lower()

            if fl == "solution.xml" and rel_root == ".":
                structure["solution_xml"] = full_path
            elif fl == "customizations.xml":
                # Monolithic CRM customizations file — prefer root-level
                if rel_root == "." or structure["customizations_xml"] is None:
                    structure["customizations_xml"] = full_path
            elif fl.endswith(".xaml"):
                # XAML workflow definitions (CRM solution ZIPs)
                structure["xaml_workflows"].append(full_path)
            elif fl.endswith(".xml"):
                classified = False
                if "entit" in rel_root or "entit" in fl:
                    structure["entities"].append(full_path)
                    classified = True
                elif "workflow" in rel_root or "workflow" in fl or "process" in rel_root:
                    structure["workflows"].append(full_path)
                    classified = True
                elif "plugin" in rel_root or "plugin" in fl or "assembly" in rel_root:
                    structure["plugins"].append(full_path)
                    classified = True
                elif "form" in rel_root or "form" in fl:
                    structure["forms"].append(full_path)
                    classified = True
                elif "webresource" in rel_root or "webresource" in fl:
                    structure["webresources"].append(full_path)
                    classified = True
                elif "role" in rel_root or "role" in fl:
                    structure["roles"].append(full_path)
                    classified = True

                if not classified:
                    unclassified_xml.append(full_path)

    if unclassified_xml:
        ax_detected = False
        for xml_path in unclassified_xml:
            root_tag = _detect_ax_root_tag(xml_path)
            if root_tag and root_tag in ALL_AX_TAGS:
                ax_detected = True
                if root_tag in AX_CLASS_TAGS:
                    structure["ax_classes"].append(xml_path)
                elif root_tag in AX_TABLE_TAGS:
                    structure["ax_tables"].append(xml_path)
                elif root_tag in AX_VIEW_TAGS:
                    structure["ax_views"].append(xml_path)
                elif root_tag in AX_FORM_TAGS:
                    structure["forms"].append(xml_path)
                elif root_tag in AX_ENTITY_TAGS:
                    structure["ax_data_entities"].append(xml_path)
                elif root_tag in AX_WORKFLOW_TAGS:
                    structure["workflows"].append(xml_path)
                elif root_tag in AX_SECURITY_TAGS:
                    structure["roles"].append(xml_path)
                elif root_tag in AX_QUERY_TAGS:
                    structure["ax_queries"].append(xml_path)
                elif root_tag in AX_SSRS_TAGS:
                    structure["ax_reports"].append(xml_path)
                else:
                    structure["other_xml"].append(xml_path)
            else:
                structure["other_xml"].append(xml_path)

        if ax_detected:
            structure["is_ax_fo"] = True

    if not structure["entities"] and not structure["workflows"] and not structure["ax_classes"]:
        for root_dir, dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".xml"):
                    full_path = os.path.join(root_dir, f)
                    if full_path not in [structure["solution_xml"]] and full_path not in structure["other_xml"]:
                        structure["other_xml"].append(full_path)

    return structure


def _is_source_code_repo(structure: dict, folder: str) -> bool:
    has_xml_content = bool(
        structure["entities"] or structure["workflows"] or
        structure["plugins"] or structure["solution_xml"] or
        structure.get("is_ax_fo", False)
    )
    if has_xml_content:
        return False

    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(".cs") or f.endswith(".csproj") or f.endswith(".crmregister"):
                return True
    return False
