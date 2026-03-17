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
            "- Summary of what the solution contains: list the EXACT counts from the knowledge graph summary:\n"
            "  entity_count, workflow_count, plugin_count, role_count (use the exact numbers, do not round or estimate)\n"
            "- Form count: count ONLY the forms that are listed in the metadata. Do NOT add forms that don't exist.\n"
            "- List the actual component NAMES as they appear in the metadata\n"
            "- Key stakeholders: ONLY if security roles are present in the metadata, list them; otherwise state 'No security roles detected'\n\n"
            "STRICTLY FORBIDDEN in this section:\n"
            "- Do NOT fabricate business objectives, benefits, or ROI claims\n"
            "- Do NOT invent 'business context' beyond what component names suggest\n"
            "- Do NOT describe what workflows or plugins DO unless their steps/descriptions are in the metadata\n"
            "Present this as a professional executive-level overview."
        ),
    },
    # --- 2. Business Requirements Document (BRD) ---
    "business_requirements": {
        "title": "Business Requirements Document (BRD)",
        "order": 2,
        "prompt": (
            "Generate a Business Requirements Document based STRICTLY on the solution metadata. Include:\n"
            "- Business objectives: state ONLY that the solution manages the entities listed in the metadata.\n"
            "  Do NOT invent specific business goals like 'streamline approvals' or 'increase revenue tracking'\n"
            "  unless a workflow step explicitly says so.\n"
            "- Stakeholders: list ONLY from security roles found in metadata; if none, state 'No security roles detected in solution metadata'\n"
            "- Business workflows: For each workflow, state ONLY:\n"
            "  - Its exact name, trigger entity, and trigger event from metadata\n"
            "  - Its actual steps from the 'steps' array (if steps only contain a file reference, state 'Detailed steps not available in metadata')\n"
            "- Functional requirements table: FR-01, FR-02, etc.\n"
            "  Format: | ID | Description | Related Component | Source |\n"
            "  The Description must describe ONLY what the metadata shows (e.g., 'Plugin executes on Create of Account (Pre-operation)')\n"
            "  Do NOT invent requirements like 'Revenue validation' or 'Email notifications' unless those appear in steps/descriptions\n"
            "- Non-functional requirements: Do NOT include this section. Non-functional requirements cannot be determined from solution metadata alone.\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Do NOT invent business rules, thresholds, or conditions not in the metadata\n"
            "- Do NOT describe workflow logic beyond what the steps array contains\n"
            "- Do NOT fabricate approval processes, notification flows, or escalation rules"
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
            "- Business process flows: For each workflow, show ONLY:\n"
            "  - Its name, trigger entity, trigger event\n"
            "  - Its steps EXACTLY as listed in the metadata 'steps' array\n"
            "  - Its conditions EXACTLY as listed in the metadata 'conditions' array\n"
            "  - If steps contain only a file reference, state: 'Detailed workflow logic not available in solution metadata'\n"
            "- Forms: list ONLY forms that appear in the metadata. Use the EXACT form names and counts from metadata.\n"
            "  DO NOT fabricate forms for entities that have no forms listed.\n"
            "  If an entity has zero forms in the metadata, state: 'No forms detected for this entity'\n"
            "- Dashboards: ONLY if detected in metadata; otherwise state 'No dashboards detected in solution'\n"
            "- Security roles mapping: ONLY if roles are present in metadata\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Do NOT describe what a workflow 'does' beyond its listed steps\n"
            "- Do NOT invent form names, Quick Create forms, or views not in metadata\n"
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
            "For each plugin, populate ALL columns from the metadata. If a value is not present, write 'N/A'.\n"
            "IMPORTANT: The 'Description' column must contain ONLY the exact description from the metadata.\n"
            "Do NOT expand or interpret plugin names into functional descriptions.\n"
            "If description is N/A, leave it as N/A — do NOT write things like 'validates account data' just because the plugin is named AccountValidation.\n\n"
            "### Workflow Definition Table\n"
            "| Workflow Name | Trigger Entity | Trigger Event | Mode | Steps | Conditions |\n"
            "List ALL workflows with their EXACT steps and conditions from the metadata.\n"
            "If steps contain only a file reference (e.g. 'Process defined in X.xml'), state that — do NOT fabricate step details.\n\n"
            "### Components Summary\n"
            "| Component Type | Name | Target Entity | Description |\n"
            "List every plugin, workflow, web resource found in the metadata.\n\n"
            "### Integration Points\n"
            "Document ONLY integrations where an EXPLICIT external system reference appears in:\n"
            "- A plugin description field (the actual 'description' value, not the plugin name)\n"
            "- A workflow step string (an actual step from the 'steps' array)\n"
            "Component names alone (e.g., 'notification_service', 'contact_sync') are NOT sufficient evidence of external integration.\n"
            "For each genuine reference found, state: 'Evidence: [exact quoted text from metadata field]'\n"
            "DO NOT fabricate REST API endpoints, Azure services, authentication methods, or middleware.\n"
            "If no concrete integration evidence is found, state: 'No external integration endpoints detected in solution metadata.'"
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
            "DO NOT change any field's required status from what the metadata states.\n"
            "The 'Description' column must use ONLY the description from metadata, or 'N/A' if none exists.\n\n"
            "### Entity Purpose Summary\n"
            "| Entity | Display Name | Field Count | Forms | Related Workflows | Related Plugins |\n"
            "The Field Count MUST match the exact number of fields in the metadata for that entity.\n"
            "The Forms column MUST list only forms from the metadata (or 'None detected').\n\n"
            "### Lookup Relationships\n"
            "List ONLY fields with Type=Lookup as entity relationships.\n"
            "Show: Source Entity, Field Name, Target Entity (inferred from field name ONLY).\n"
            "DO NOT fabricate relationships not evidenced by lookup fields.\n\n"
            "### Option Set Fields\n"
            "List ONLY fields with Type=Picklist or Type=OptionSet found in the metadata.\n"
            "If none found, state: 'No option set fields detected.'\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Inventing field descriptions not in the metadata\n"
            "- Changing field types or required status from what metadata states\n"
            "- Adding relationships between entities that are not evidenced by Lookup fields\n"
            "- Fabricating entity-relationship diagrams beyond what lookup fields prove"
        ),
    },
    # --- 6. Integration Documentation ---
    "integration": {
        "title": "Integration Documentation",
        "order": 6,
        "prompt": (
            "Generate Integration Documentation based STRICTLY on what the metadata evidences.\n\n"
            "### IMPORTANT: What Counts as Integration Evidence\n"
            "Integration evidence MUST come from one of these metadata fields:\n"
            "1. A plugin 'description' field that EXPLICITLY mentions an external system, URL, or service name\n"
            "2. A workflow 'steps' array entry that EXPLICITLY references an external system\n"
            "3. A web resource description that references an external endpoint\n\n"
            "The following are NOT integration evidence:\n"
            "- Component NAMES that SUGGEST integration (e.g., 'contact_sync', 'notification_service')\n"
            "  These are just naming conventions and do not prove external integration exists\n"
            "- Plugin registration metadata (entity, message, stage) — these are internal Dynamics CRM registrations\n"
            "- Workflow trigger events — these are internal Dynamics CRM events\n\n"
            "### If Integration Evidence is Found\n"
            "For EACH detected reference, document:\n"
            "| Integration Point | Exact Evidence (quoted from metadata) | Component Type | Component Name |\n"
            "State: 'The following integration points are identified from explicit references in the metadata. "
            "Actual integration details (endpoints, authentication, payloads) are not present in the solution metadata "
            "and must be verified with the development team.'\n\n"
            "### If NO Integration Evidence is Found\n"
            "State: 'No external integration points were detected in the solution metadata. "
            "Plugin registrations and workflow definitions in this solution reference only internal Dynamics CRM operations. "
            "If external integrations exist, they may be implemented in plugin source code (assemblies) which is not "
            "included in the solution metadata.'\n\n"
            "DO NOT fabricate:\n"
            "- API endpoint URLs\n"
            "- Authentication methods or credentials\n"
            "- Message formats or data payloads\n"
            "- Azure service connections\n"
            "- Middleware configurations\n"
            "- Integration architecture diagrams based on component names alone"
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
            "Populate ALL columns from metadata. Use 'N/A' for missing values.\n"
            "IMPORTANT: The 'Description' column must contain ONLY the description from the metadata.\n"
            "Do NOT invent what the plugin 'does' — plugin metadata contains ONLY registration info, NOT source code.\n\n"
            "### Custom Workflows\n"
            "| Workflow Name | Trigger Entity | Trigger Event | Mode | Step Count | Has Conditions |\n"
            "Count steps and conditions from the ACTUAL metadata arrays only.\n\n"
            "### Forms\n"
            "| Form Name | Entity | Source File | Tab Count | Section Count | Control Count |\n"
            "List ONLY forms found in the metadata. The EXACT count must match the metadata.\n"
            "DO NOT add forms for entities that have none in the metadata.\n\n"
            "### Web Resources\n"
            "| Resource Name | Type | Related Entity | Description |\n"
            "List ONLY from metadata. If none detected, state 'No web resources detected.'\n\n"
            "### Custom Entities\n"
            "List all entities found in the solution with their field counts.\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Inventing form tab/section/control counts not in metadata\n"
            "- Adding web resources not present in metadata\n"
            "- Describing plugin business logic (only registration metadata is available)\n"
            "- Fabricating form layouts or field placements not in metadata"
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
            "List EXACT counts from the metadata: entities, workflows, plugins, forms, roles, web resources.\n"
            "These counts MUST match the metadata exactly.\n\n"
            "### Pre-deployment Component Checklist (from metadata)\n"
            "Generate a checklist using ONLY the actual component names from the metadata:\n"
            "| # | Component | Type | Pre-deployment Check |\n"
            "For each plugin: 'Verify [exact plugin name] assembly is registered on target'\n"
            "For each workflow: 'Verify [exact workflow name] trigger entity [entity name] exists on target'\n"
            "For each entity: 'Verify [exact entity name] schema exists on target'\n"
            "Use ONLY component names from the metadata. Do NOT add generic checklist items.\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Generic deployment checklists not tied to specific components in this solution\n"
            "- Fabricating migration scripts, rollback procedures, or PowerShell scripts\n"
            "- Fabricating Azure DevOps pipeline configurations\n"
            "- Inventing backup commands or database scripts\n"
            "- Adding ANY checklist item that does not reference a specific component from the metadata"
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
            "CRITICAL TEST CASE RULES — VIOLATION IS A FAILURE:\n"
            "1. The 'Scenario' must be: 'Test workflow trigger: [workflow name] triggers on [entity] [event]'\n"
            "2. The 'Preconditions' must be: 'User has access to [entity]' — NO specific data values\n"
            "3. The 'Steps' must reference ONLY the component's metadata fields:\n"
            "   CORRECT: '1. Create/Update [entity from metadata] record. 2. Verify [workflow name from metadata] triggers. 3. Check System Jobs for completion'\n"
            "   WRONG: '1. Set Revenue to $150,000. 2. Verify approval threshold check passes'\n"
            "   WRONG: '1. Enter customer data. 2. Verify validation rules execute'\n"
            "4. The 'Expected Result' must be ONLY 'Component executes without errors':\n"
            "   CORRECT: 'Workflow executes without errors'\n"
            "   WRONG: 'Email notification is sent to manager'\n"
            "   WRONG: 'Revenue threshold validation passes'\n"
            "5. Do NOT reference workflow STEP NAMES as testable actions:\n"
            "   A step named 'Check approval threshold' in the metadata does NOT mean you know what the threshold is.\n"
            "   WRONG: 'Verify approval threshold of $100,000 is enforced'\n"
            "   You do NOT know the threshold. You do NOT know what is being checked.\n\n"
            "### Test Cases from Plugins\n"
            "For EACH plugin in the metadata, generate 1-2 test cases:\n\n"
            "| Test ID | Plugin | Scenario | Entity | Message | Expected Result | Type |\n"
            "|---------|--------|----------|--------|---------|-----------------|------|\n\n"
            "PLUGIN TEST RULES:\n"
            "- Scenario: 'Verify plugin fires on [Message] of [Entity] at [Stage] stage'\n"
            "- Expected Result: 'Plugin executes without throwing exception'\n"
            "- You do NOT know what validation the plugin performs or what business rules it enforces.\n"
            "- WRONG: 'Verify duplicate detection blocks duplicate accounts'\n"
            "- WRONG: 'Verify revenue validation rejects invalid amounts'\n\n"
            "### Test Coverage Matrix\n"
            "| Component | Component Type | Test IDs | Coverage |\n\n"
            "IMPORTANT: Use clean, well-formatted markdown tables."
        ),
    },
    # --- 11. Support & Operations Guide ---
    "support_operations": {
        "title": "Support & Operations Guide",
        "order": 11,
        "prompt": (
            "Generate a Support & Operations Guide based on the ACTUAL solution components.\n\n"
            "### Component Monitoring Table (from metadata)\n"
            "For each plugin found in the metadata:\n"
            "| Plugin Name | Entity | Message | Stage | Execution Mode | Log Location |\n"
            "The 'Log Location' column must be ONLY: 'Plugin Trace Log' for sync plugins, 'System Jobs + Plugin Trace Log' for async plugins.\n"
            "Do NOT describe what the plugin does.\n\n"
            "### Workflow Monitoring Table (from metadata)\n"
            "For each workflow:\n"
            "| Workflow Name | Trigger Entity | Trigger | Mode | Log Location |\n"
            "The 'Log Location' must be ONLY: 'System Jobs' for background workflows, 'Plugin Trace Log' for real-time workflows.\n\n"
            "### Troubleshooting per Component (from metadata)\n"
            "For EACH plugin and workflow from the metadata, generate ONE row:\n"
            "| Component Name | Type | Stage/Mode (from metadata) | Where to Check Logs |\n"
            "The 'Where to Check Logs' column must be ONLY:\n"
            "- 'Plugin Trace Log' for plugins\n"
            "- 'System Jobs' for async workflows/plugins\n"
            "Do NOT invent specific error messages, failure scenarios, or resolution steps.\n"
            "Do NOT describe what the component does or what could go wrong with its business logic.\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Generic troubleshooting guides not referencing specific components from this solution\n"
            "- Invented error messages or failure scenarios\n"
            "- Azure Application Insights or any monitoring tool not evidenced in metadata\n"
            "- Fabricated logging configurations or log file paths"
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
            "- Purpose: State which entity this is based on its name. Do NOT invent detailed business purposes.\n"
            "- Key fields: list the ACTUAL fields from metadata with their display names and whether they are required\n"
            "- Available forms: list ONLY forms found in the metadata for this entity.\n"
            "  If no forms exist for this entity, state: 'No custom forms detected in solution metadata'\n"
            "- Related workflows: list the ACTUAL workflows that trigger on this entity\n"
            "- Related plugins: list the ACTUAL plugins that fire on this entity\n\n"
            "### Business Process Guides\n"
            "For each workflow, provide a user-friendly description:\n"
            "- State the workflow name, what entity triggers it, and on what event\n"
            "- If the workflow has detailed steps in the metadata, list them\n"
            "- If the workflow steps contain only a file reference, state:\n"
            "  'This workflow's detailed steps are defined in a workflow definition file. Contact your administrator for step details.'\n"
            "Do NOT invent step-by-step business processes not present in the metadata.\n\n"
            "Do NOT include a FAQ section. FAQs require knowledge of actual system behavior which is not available in solution metadata.\n\n"
            "Write in end-user-friendly language. Use actual field names and form names from the metadata.\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Describing what happens when a user creates/updates a record beyond listing which plugins/workflows trigger\n"
            "- Inventing user instructions not backed by form metadata\n"
            "- Fabricating business process descriptions beyond what step names show"
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
            "Use EXACT counts from the metadata. These MUST match the metadata exactly.\n\n"
            "Do NOT include a generic environment deployment matrix. Only document what is in the metadata.\n\n"
            "### Plugin Registration Requirements\n"
            "For each plugin found, document the registration requirements using actual metadata:\n"
            "| Plugin | Assembly | Entity | Message | Stage | Mode |\n\n"
            "STRICTLY FORBIDDEN:\n"
            "- Fabricating Azure configuration, connection strings, or secrets\n"
            "- Inventing environment variables or application settings\n"
            "- Describing integration endpoints not evidenced in metadata\n"
            "- Adding server names, URLs, or credentials"
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
            "Do NOT include a version history template or change management process section.\n"
            "Only document what is actually present in the solution metadata.\n"
            "Do NOT generate any generic guidance or templates."
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
            "6. **Cross-entity relationships** — ONLY if a workflow's 'relatedEntities' array or a plugin's 'triggerEntity' explicitly names a SECOND entity in the metadata, draw an edge. Do NOT infer relationships from component names.\n\n"
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

    Rules:
    - 'integration': suppress if no plugin descriptions explicitly mention external systems
      and no workflow steps explicitly mention external systems.
    - 'security_model': suppress if no security roles in the graph.
    """

    if section_key == "integration":
        # Check for explicit external system references in plugin descriptions and workflow steps
        external_keywords = [
            "api", "rest", "soap", "http", "https", "endpoint", "url",
            "external", "integration", "sync", "third-party", "webhook",
            "azure", "service bus", "queue", "crm online", "sharepoint",
            "mailbox", "smtp", "oauth", "authentication", "token",
        ]
        has_evidence = False

        for pdata in graph.plugins.values():
            if pdata.description:
                desc_lower = pdata.description.lower()
                if any(kw in desc_lower for kw in external_keywords):
                    has_evidence = True
                    break

        if not has_evidence:
            for wdata in graph.workflows.values():
                for step in wdata.steps:
                    step_lower = step.lower()
                    if any(kw in step_lower for kw in external_keywords):
                        has_evidence = True
                        break
                if has_evidence:
                    break

        if not has_evidence:
            return (
                "# Integration Documentation\n\n"
                "## Integration Assessment\n\n"
                "No external integration points were detected in the solution metadata.\n\n"
                "The solution contains plugin registrations and workflow definitions that operate "
                "within the internal Dynamics CRM platform. No plugin descriptions or workflow steps "
                "reference external systems, APIs, endpoints, or third-party services.\n\n"
                "**Note:** If external integrations exist, they may be implemented in plugin assembly "
                "source code (.dll), which is not included in the solution metadata XML. "
                "Review the plugin assemblies and source code for actual integration logic."
            )

    if section_key == "security_model":
        if not graph.roles:
            return (
                "# Security Model\n\n"
                "## Security Assessment\n\n"
                "No security roles were detected in the solution metadata.\n\n"
                "Security configuration may exist in the Dynamics CRM environment but is not "
                "included in this solution package. Contact the system administrator for "
                "details on security role assignments and privilege configurations."
            )

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
