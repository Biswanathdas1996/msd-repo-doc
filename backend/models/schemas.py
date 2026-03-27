from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class SolutionStatus(str, Enum):
    processing = "processing"
    ready = "ready"
    error = "error"


class EntityField(BaseModel):
    name: str
    type: str
    displayName: Optional[str] = None
    required: bool = False
    description: Optional[str] = None
    maxLength: Optional[int] = None
    options: Optional[list[str]] = None  # for picklist/optionset fields


class Entity(BaseModel):
    name: str
    displayName: Optional[str] = None
    fields: list[EntityField] = []
    forms: list[str] = []
    workflows: list[str] = []
    plugins: list[str] = []


class Workflow(BaseModel):
    name: str
    triggerEntity: Optional[str] = None
    trigger: Optional[str] = None
    mode: Optional[str] = None  # Synchronous / Asynchronous / RealTime
    scope: Optional[str] = None  # Organization / Business Unit / User
    steps: list[str] = []
    plugins: list[str] = []
    conditions: list[str] = []
    relatedEntities: list[str] = []


class Plugin(BaseModel):
    name: str
    triggerEntity: Optional[str] = None
    operation: Optional[str] = None
    stage: Optional[str] = None
    description: Optional[str] = None
    executionMode: Optional[str] = None  # Synchronous / Asynchronous
    executionOrder: Optional[int] = None
    filteringAttributes: Optional[list[str]] = None
    assemblyName: Optional[str] = None
    secureConfiguration: Optional[str] = None


class Role(BaseModel):
    name: str
    privileges: list[str] = []
    description: Optional[str] = None


class WebResource(BaseModel):
    name: str
    type: str = "unknown"
    displayName: Optional[str] = None
    description: Optional[str] = None
    relatedEntity: Optional[str] = None


class KnowledgeGraphFieldDetail(BaseModel):
    name: str
    type: str = "string"
    displayName: Optional[str] = None
    required: bool = False


class FormDetail(BaseModel):
    name: str
    entity: Optional[str] = None
    tabs: list[str] = []
    sections: list[str] = []
    controls: list[str] = []
    sourceFile: Optional[str] = None


class KnowledgeGraphEntity(BaseModel):
    fields: list[str] = []  # kept for backward compat
    fieldDetails: list[KnowledgeGraphFieldDetail] = []
    forms: list[str] = []
    formDetails: list[FormDetail] = []
    workflows: list[str] = []
    plugins: list[str] = []


class KnowledgeGraphWorkflow(BaseModel):
    trigger: Optional[str] = None
    triggerEntity: Optional[str] = None
    mode: Optional[str] = None
    scope: Optional[str] = None
    steps: list[str] = []
    conditions: list[str] = []
    plugins: list[str] = []
    relatedEntities: list[str] = []


class KnowledgeGraphPlugin(BaseModel):
    triggerEntity: Optional[str] = None
    operation: Optional[str] = None
    stage: Optional[str] = None
    executionMode: Optional[str] = None
    executionOrder: Optional[int] = None
    filteringAttributes: Optional[list[str]] = None
    assemblyName: Optional[str] = None
    description: Optional[str] = None


class KnowledgeGraphRole(BaseModel):
    privileges: list[str] = []
    relatedEntities: list[str] = []
    description: Optional[str] = None


class KnowledgeGraphWebResource(BaseModel):
    type: str = "unknown"
    relatedEntity: Optional[str] = None
    description: Optional[str] = None


class Relationship(BaseModel):
    source: str
    target: str
    type: str


class KnowledgeGraph(BaseModel):
    entities: dict[str, KnowledgeGraphEntity] = {}
    workflows: dict[str, KnowledgeGraphWorkflow] = {}
    plugins: dict[str, KnowledgeGraphPlugin] = {}
    roles: dict[str, KnowledgeGraphRole] = {}
    webResources: dict[str, KnowledgeGraphWebResource] = {}
    relationships: list[Relationship] = []


class FunctionalFlow(BaseModel):
    entity: str
    workflow: str
    plugins: list[str] = []
    steps: list[str] = []
    description: Optional[str] = None


class SolutionMetadata(BaseModel):
    solutionVersion: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    uniqueName: Optional[str] = None
    isManaged: Optional[bool] = None
    dependencies: Optional[list[str]] = None
    # generic_code | source_code | ax_fo | etc.
    type: Optional[str] = None
    projectKind: Optional[str] = None


class SolutionSummary(BaseModel):
    id: str
    name: str
    uploadedAt: str
    entityCount: int = 0
    workflowCount: int = 0
    pluginCount: int = 0
    hasDocumentation: bool = False
    status: SolutionStatus = SolutionStatus.processing


class Solution(BaseModel):
    id: str
    name: str
    uploadedAt: str
    status: SolutionStatus = SolutionStatus.processing
    entityCount: int = 0
    workflowCount: int = 0
    pluginCount: int = 0
    formCount: int = 0
    roleCount: int = 0
    webResourceCount: int = 0
    hasDocumentation: bool = False
    metadata: Optional[SolutionMetadata] = None


class DocSection(BaseModel):
    title: str
    slug: str
    content: str
    order: int


class GeneratedDocs(BaseModel):
    solutionId: str
    generatedAt: str
    sections: list[DocSection] = []
    verified: bool = False


class GitHubImportRequest(BaseModel):
    url: str
    name: str = ""
    processMode: str = "auto"


class SolutionChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str = Field(default="", max_length=8000)


class SolutionChatRequest(BaseModel):
    """Ask a question grounded only in parsed/generated project data."""
    message: str = Field(..., max_length=8000)
    history: list[SolutionChatMessage] = Field(default_factory=list, max_length=20)


class SolutionChatResponse(BaseModel):
    answer: str


class GenerateInsightRequest(BaseModel):
    """PwC Gen AI insight type for solution detail tabs."""
    insightType: str


class GenerateDocsRequest(BaseModel):
    sections: list[str] = []


class VerificationIssue(BaseModel):
    severity: str
    section: str
    message: str


class VerificationResult(BaseModel):
    solutionId: str
    verified: bool
    score: float
    issues: list[VerificationIssue] = []
    summary: str
