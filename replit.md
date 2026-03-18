# AI Documentation Generator

## Overview

Enterprise-grade AI Documentation Generator that takes Microsoft Dynamics Solution ZIP files, parses XML metadata, builds a JSON Knowledge Graph, and uses PwC Gen AI to generate structured documentation with multi-pass verification.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **Frontend**: React + Vite + Tailwind CSS (artifacts/doc-generator)
- **Backend**: Python FastAPI (backend/)
- **AI**: PwC Gen AI Services (via PWC_GENAI_ENDPOINT_URL)
- **API codegen**: Orval (from OpenAPI spec)
- **Database**: PostgreSQL + Drizzle ORM (available but not used for solution storage)
- **Solution Storage**: File-based JSON metadata in backend/data/

## Architecture

```text
Dynamics Solution ZIP
        |
  [Python FastAPI Backend]
        |
XML Parser (lxml) -> Metadata JSON
        |
JSON Knowledge Graph Builder
        |
Chunking Engine -> Context for AI
        |
PwC Gen AI Reasoning
        |
Documentation Generator -> Markdown Sections
        |
  [React Frontend]
        |
Dashboard / Solution Detail / Knowledge Graph / AI Docs
```

## Structure

```text
artifacts-monorepo/
├── artifacts/
│   ├── api-server/         # Express API server (health check)
│   └── doc-generator/      # React + Vite frontend
├── backend/                # Python FastAPI backend
│   ├── main.py             # FastAPI app entry point (port 5001)
│   ├── models/
│   │   └── schemas.py      # Pydantic models
│   ├── services/
│   │   ├── extractor.py    # ZIP extraction
│   │   ├── xml_parser.py   # XML parsing (lxml)
│   │   ├── knowledge_graph.py # JSON Knowledge Graph builder
│   │   ├── flow_generator.py  # Functional flow generator
│   │   ├── chunking_engine.py # Chunking for AI context
│   │   └── ai_reasoning.py   # PwC Gen AI reasoning layer
│   └── data/               # Solution files and metadata
├── lib/
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas
│   └── db/                 # Drizzle ORM schema + DB connection
├── pnpm-workspace.yaml
└── package.json
```

## Key Endpoints (Python FastAPI proxied via Express at /api/py-api)

- `GET /api/py-api/healthz` - Health check
- `POST /api/py-api/solutions/upload` - Upload Dynamics solution ZIP (multipart/form-data, streamed, up to 10GB, legacy single-request)
- `POST /api/py-api/solutions/upload/init` - Initialize chunked upload session (JSON body: filename, totalSize, totalChunks, name)
- `POST /api/py-api/solutions/upload/chunk` - Upload a single chunk (multipart: file, uploadId, chunkIndex; idempotent via seek-based writes)
- `POST /api/py-api/solutions/upload/finalize` - Finalize chunked upload and start processing (JSON body: uploadId, name)
- `GET /api/py-api/solutions` - List solutions
- `GET /api/py-api/solutions/{id}` - Solution details
- `DELETE /api/py-api/solutions/{id}` - Delete solution
- `GET /api/py-api/solutions/{id}/knowledge-graph` - JSON Knowledge Graph
- `GET /api/py-api/solutions/{id}/entities` - Parsed entities
- `GET /api/py-api/solutions/{id}/workflows` - Parsed workflows
- `GET /api/py-api/solutions/{id}/plugins` - Parsed plugins
- `GET /api/py-api/solutions/{id}/functional-flows` - Functional flows
- `POST /api/py-api/solutions/{id}/generate-docs` - Generate AI documentation
- `GET /api/py-api/solutions/{id}/docs` - Get generated docs
- `POST /api/py-api/solutions/{id}/verify` - Verify docs against knowledge graph
- `GET /api/py-api/solutions/{id}/download/docx` - Download docs as Word (.docx)
- `GET /api/py-api/solutions/{id}/download/pdf` - Download docs as PDF
- `POST /api/py-api/solutions/{id}/reprocess` - Re-run parser + knowledge graph builder on existing source files (useful after parser changes)

## Input Formats Supported

1. **Dynamics Solution ZIP exports** - Standard XML-based solution packages (entities/, workflows/, plugins/ folders)
2. **C# Source Code Repos** (ZIP or GitHub import) - Parses .cs files for plugin/workflow classes, .crmregister for registrations, .csproj for project structure. Detects entity references from code (new Entity("name"), EntityReference, QueryExpression). Uses three-level entity aggregation (per-file → directory → parent directory) to correctly link workflow activities to entities across project boundaries (e.g. workflow in Workflows/ project linked to entities from Common/ project).

## Processing Pipeline (Multi-pass)

1. **Pass 1**: Extract ZIP, detect format (XML solution vs source code repo), parse accordingly
2. **Pass 2**: Build JSON Knowledge Graph with entity-workflow-plugin relationships
3. **Pass 3**: Identify functional modules via relationship analysis
4. **Pass 4**: Generate functional flows from graph traversal
5. **Pass 5**: Chunk knowledge graph for AI context windows
6. **Pass 6**: PwC Gen AI generates structured markdown documentation per section
7. **Pass 7**: PwC Gen AI verification pass checks docs against knowledge graph

## Workflows

- `Python API Server` - FastAPI backend on port 5001
- `artifacts/doc-generator: web` - React frontend
- `artifacts/api-server: API Server` - Express health check

## Frontend Pages

- **Dashboard**: Solution list with stats, upload dialog
- **Solution Detail**: Tabbed view with Overview, Knowledge Graph, Entities, Workflows, Plugins, AI Documentation
- **Knowledge Graph Viewer**: Interactive node-based visualization using @xyflow/react
- **AI Documentation**: Section selector, AI generation, markdown preview, verification, Word/PDF download

## Environment Variables

- `PWC_GENAI_ENDPOINT_URL` - PwC Gen AI completions endpoint URL
- `PWC_GENAI_API_KEY` - PwC Gen AI API key
- `PWC_GENAI_BEARER_TOKEN` - PwC Gen AI bearer token for authentication
- `DATABASE_URL` - PostgreSQL connection (auto-configured)
