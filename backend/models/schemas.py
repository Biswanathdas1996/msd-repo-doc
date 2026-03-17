from pydantic import BaseModel
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


class KnowledgeGraphEntity(BaseModel):
    fields: list[str] = []
    forms: list[str] = []
    workflows: list[str] = []
    plugins: list[str] = []


class KnowledgeGraphWorkflow(BaseModel):
    trigger: Optional[str] = None
    triggerEntity: Optional[str] = None
    steps: list[str] = []
    plugins: list[str] = []
    relatedEntities: list[str] = []


class KnowledgeGraphPlugin(BaseModel):
    triggerEntity: Optional[str] = None
    operation: Optional[str] = None
    stage: Optional[str] = None


class Relationship(BaseModel):
    source: str
    target: str
    type: str


class KnowledgeGraph(BaseModel):
    entities: dict[str, KnowledgeGraphEntity] = {}
    workflows: dict[str, KnowledgeGraphWorkflow] = {}
    plugins: dict[str, KnowledgeGraphPlugin] = {}
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
