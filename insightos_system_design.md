# InsightOS — System Design for Large-Scale Financial Document AI

**Version:** 1.0  
**Author:** InsightOS Engineering  
**Stack:** LlamaIndex · Neo4j · Qdrant · PostgreSQL · LlamaIndex Workflows

---

## Table of Contents

1. [The Core Problem](#1-the-core-problem)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Pipeline 1 — Ingestion, Parsing, Transformation](#3-pipeline-1--ingestion-parsing-transformation)
4. [Pipeline 2 — Knowledge Graph Construction](#4-pipeline-2--knowledge-graph-construction)
5. [Pipeline 3 — RAG System and Chat UI](#5-pipeline-3--rag-system-and-chat-ui)
6. [Cross-Cutting Concerns](#6-cross-cutting-concerns)
7. [Low-Level Component Design](#7-low-level-component-design)
8. [Data Schemas](#8-data-schemas)
9. [Full Code Reference](#9-full-code-reference)
10. [Deployment Architecture](#10-deployment-architecture)

---

## 1. The Core Problem

### Why large financial documents are hard

A single Indian listed company typically publishes the following per year:

| Document | Pages | Characteristics |
|---|---|---|
| Annual report | 200–400 | Dense tables, charts, footnotes, images, dual-column layouts |
| Quarterly concall transcript | 20–60 | Speaker-attributed, conversational, jargon-heavy |
| CRISIL / ICRA rating report | 10–30 | Proprietary layouts, mixed image and text, scanned pages |
| Exchange filing (BSE/NSE) | 5–100 | Semi-structured, regulatory boilerplate |

At 20 companies with 4–6 documents each, you are looking at **80–120 documents** totalling **8,000–15,000 pages** at initial ingestion alone, refreshed quarterly.

The naive approach — read PDFs as plain text, split every 512 tokens, embed and store — fails for four specific reasons with financial documents:

1. **Tables become garbage.** A balance sheet extracted as plain text produces rows of numbers with no column headers, making retrieval meaningless.
2. **Fixed chunking breaks financial context.** A chunk boundary in the middle of a "Management Discussion and Analysis" paragraph loses the causal chain. "Revenue grew 18% due to X" split across two chunks means neither chunk answers the question well.
3. **300 pages × 20 companies = throughput problem.** Sequential synchronous ingestion of 6,000 pages is too slow for any reasonable update cadence. You need async parallel processing.
4. **Entity co-reference.** "HZL", "Hindustan Zinc", and "Hindustan Zinc Limited" all refer to the same entity. Naive extraction creates three disconnected nodes in your knowledge graph.

### Design goals

- Process a 300-page annual report in **under 10 minutes** end-to-end
- Ingest 20 companies in **parallel**, not sequentially
- Every retrieved chunk carries **page number, document name, company ID** as metadata
- Tables are extracted as **structured Markdown**, not broken text
- Knowledge graph uses a **strict financial schema** to prevent entity drift
- All pipelines are **idempotent** — re-running on the same document does not create duplicates

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DOCUMENT SOURCES                             │
│  NSE/BSE APIs · Company IR Pages · Manual Upload                   │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PIPELINE 1 — INGESTION                           │
│                                                                     │
│   LlamaParse (PDF/OCR) → HierarchicalNodeParser → IngestionPipeline │
│       ↓ chunks + metadata           ↓ embeddings                   │
│   Doc Registry (PostgreSQL)      Vector Store (Qdrant)              │
│       ↓ entity JSON                                                 │
│   Staging Store (PostgreSQL)                                        │
└───────────────────────────┬─────────────────────────────────────────┘
                            │  (Job Scheduler triggers on P1 complete)
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PIPELINE 2 — KNOWLEDGE GRAPH                     │
│                                                                     │
│   SchemaLLMPathExtractor → Entity Resolution → PropertyGraphIndex   │
│                                    ↓                                │
│                             Neo4j Graph DB                          │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PIPELINE 3 — RAG + CHAT UI                       │
│                                                                     │
│   Query → Hybrid Retrieval → Re-rank → Graph Context Merge          │
│                    ↓                                                │
│         CitationQueryEngine (LlamaIndex)                            │
│                    ↓                                                │
│         Answer + Page Citations + Entity Panel                      │
│                    ↓                                                │
│              Chat UI (React)                                        │
└─────────────────────────────────────────────────────────────────────┘

          ┌──────────────────────────────────┐
          │      CROSS-CUTTING SERVICES       │
          │  Job Scheduler · Doc Registry     │
          │  Update Strategy · Eval Loop      │
          └──────────────────────────────────┘
```

---

## 3. Pipeline 1 — Ingestion, Parsing, Transformation

### 3.1 The problem with 300-page PDFs

A 300-page annual report must be broken into the right-sized pieces for two very different consumers:

- The **embedding model** needs chunks of 128–512 tokens for precise semantic retrieval
- The **LLM** needs 1,024–2,048 token context windows for coherent answer synthesis

The solution is **hierarchical chunking**: store the same content at multiple granularities and retrieve the right level at query time.

```
Document (all 300 pages)
    └── Section (e.g., "Management Discussion & Analysis" — ~5,000 tokens)
            └── Paragraph group (~1,024 tokens)  ← LLM context window
                    └── Sentence cluster (~256 tokens)  ← embedding retrieval unit
```

### 3.2 Parsing strategy by document type

| Document Type | Parser | Special Handling |
|---|---|---|
| Annual report PDF (text-based) | LlamaParse v2 | Table extraction as Markdown, chart descriptions |
| Annual report PDF (scanned) | LlamaParse OCR mode | Full OCR, image captioning |
| Concall transcript | LlamaParse + SentenceSplitter | Speaker attribution preserved in metadata |
| Rating report | LlamaParse + custom template | Proprietary layout handling |
| HTML filing | HTMLNodeParser | Section-aware splitting |

### 3.3 LlamaParse configuration for financial PDFs

LlamaParse is the open-source compatible PDF parser from LlamaIndex. For financial documents, the key settings are:

```python
from llama_cloud import LlamaCloud
from llama_index.core import SimpleDirectoryReader

# For open-source / self-hosted usage, use pymupdf + pdfplumber fallback
# LlamaParse handles tables and charts best for complex PDFs

from llama_index.readers.file import PDFReader
import pdfplumber

def parse_financial_pdf(pdf_path: str, company_id: str, doc_type: str):
    """
    Parse a financial PDF with table-awareness.
    Uses pdfplumber for table extraction, LlamaIndex PDFReader for text.
    """
    import fitz  # PyMuPDF
    from pathlib import Path

    pages_data = []
    doc = fitz.open(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, (fitz_page, plumber_page) in enumerate(
            zip(doc, pdf.pages), start=1
        ):
            # Extract text via PyMuPDF
            text = fitz_page.get_text("text")

            # Extract tables via pdfplumber — preserves structure
            tables = plumber_page.extract_tables()
            table_md = ""
            for table in tables:
                if table:
                    # Convert to Markdown table
                    header = "| " + " | ".join(
                        str(c or "") for c in table[0]
                    ) + " |"
                    separator = "| " + " | ".join(
                        "---" for _ in table[0]
                    ) + " |"
                    rows = [
                        "| " + " | ".join(str(c or "") for c in row) + " |"
                        for row in table[1:]
                    ]
                    table_md += "\n".join([header, separator] + rows) + "\n\n"

            pages_data.append({
                "page_num": page_num,
                "text": text,
                "tables": table_md,
                "company_id": company_id,
                "doc_type": doc_type,
                "source_file": Path(pdf_path).name,
            })

    return pages_data
```

### 3.4 Hierarchical chunking with HierarchicalNodeParser

This is the key pattern for large documents. From the LlamaIndex docs:

> "This node parser will chunk nodes into hierarchical nodes. This means a single input will be chunked into several hierarchies of chunk sizes, with each node containing a reference to its parent node. When combined with the AutoMergingRetriever, this enables us to automatically replace retrieved nodes with their parents when a majority of children are retrieved."

```python
from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter
from llama_index.core.retrievers import AutoMergingRetriever
from llama_index.core import VectorStoreIndex
from llama_index.core.storage.docstore import SimpleDocumentStore

# Three levels:
# - 2048 tokens: LLM synthesis context (e.g., full MD&A section)
# - 512 tokens:  Mid-level context (e.g., a paragraph about NPAs)
# - 128 tokens:  Embedding retrieval unit (precise sentence cluster)
node_parser = HierarchicalNodeParser.from_defaults(
    chunk_sizes=[2048, 512, 128]
)

# Parse documents into hierarchical nodes
nodes = node_parser.get_nodes_from_documents(documents)

# Store ALL levels in docstore (required for auto-merging)
docstore = SimpleDocumentStore()
docstore.add_documents(nodes)

# Only index leaf nodes (128-token) in the vector store
# AutoMergingRetriever will pull in parent context at query time
leaf_nodes = [n for n in nodes if n.metadata.get("hierarchy_level") == "leaf"]

index = VectorStoreIndex(
    leaf_nodes,
    storage_context=storage_context,
)

# At retrieval time, use AutoMergingRetriever
base_retriever = index.as_retriever(similarity_top_k=12)
retriever = AutoMergingRetriever(
    base_retriever,
    storage_context,
    verbose=True,
)
```

**Why this matters for a 300-page annual report:**  
The embedding search finds the precise 128-token sentence cluster. But when you retrieve 6 out of 8 child chunks from the same parent, AutoMergingRetriever automatically returns the full 512-token parent instead — giving the LLM complete context without requiring you to pre-specify section boundaries.

### 3.5 Parallel async ingestion pipeline

From the LlamaIndex docs: `IngestionPipeline` supports `num_workers > 1` for parallel execution. Both sync and async versions of batched parallel execution are possible.

```python
import asyncio
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import HierarchicalNodeParser
from llama_index.core.extractors import (
    TitleExtractor,
    QuestionsAnsweredExtractor,
    KeywordExtractor,
)
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
import qdrant_client

# ── Vector store setup ──────────────────────────────────────────────
qclient = qdrant_client.QdrantClient(url="http://localhost:6333")
vector_store = QdrantVectorStore(
    client=qclient,
    collection_name="insightos_chunks",
)

# ── Ingestion pipeline ───────────────────────────────────────────────
pipeline = IngestionPipeline(
    transformations=[
        # 1. Hierarchical chunking
        HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128]),

        # 2. Metadata extraction — runs LLM on each chunk
        TitleExtractor(nodes=5),             # infer section title
        KeywordExtractor(keywords=10),       # financial keywords per chunk

        # 3. Embed leaf nodes only
        OpenAIEmbedding(model="text-embedding-3-small"),
    ],
    vector_store=vector_store,
    # Attach docstore for hierarchical retrieval to work
    docstore=docstore,
)

# ── Process one company's documents in parallel ──────────────────────
async def ingest_company_documents(
    company_id: str,
    document_paths: list[str],
    semaphore: asyncio.Semaphore,
) -> dict:
    """
    Ingest all documents for one company.
    Semaphore controls total concurrent LLM API calls.
    """
    async with semaphore:
        documents = []
        for path in document_paths:
            pages = parse_financial_pdf(path, company_id, doc_type="annual_report")
            for page in pages:
                from llama_index.core import Document
                doc = Document(
                    text=page["text"] + "\n\n" + page["tables"],
                    metadata={
                        "company_id": company_id,
                        "page_label": str(page["page_num"]),
                        "source_file": page["source_file"],
                        "doc_type": page["doc_type"],
                    },
                    # Exclude heavy fields from embedding
                    excluded_embed_metadata_keys=["tables_raw"],
                )
                documents.append(doc)

        # Run pipeline with 4 parallel workers
        nodes = await pipeline.arun(documents=documents, num_workers=4)

        return {
            "company_id": company_id,
            "docs_processed": len(document_paths),
            "chunks_created": len(nodes),
        }

# ── Ingest ALL companies in parallel ────────────────────────────────
async def ingest_all_companies(company_documents: dict[str, list[str]]):
    """
    company_documents: { "FEDERALBNK": ["path/to/ar.pdf", ...], ... }
    Semaphore limits to 5 companies processing simultaneously.
    """
    semaphore = asyncio.Semaphore(5)
    tasks = [
        ingest_company_documents(company_id, paths, semaphore)
        for company_id, paths in company_documents.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results

# Run it
company_docs = {
    "FEDERALBNK": ["./docs/federal_bank_ar_fy24.pdf",
                   "./docs/federal_bank_q3_transcript.pdf"],
    "HINDZINC":   ["./docs/hzl_ar_fy24.pdf"],
    # ... all 20 companies
}

results = asyncio.run(ingest_all_companies(company_docs))
```

### 3.6 Document deduplication and management

LlamaIndex's `IngestionPipeline` with a docstore handles deduplication automatically:

```python
from llama_index.core.ingestion import IngestionPipeline
from llama_index.storage.docstore.redis import RedisDocumentStore

# Attach a persistent docstore — pipeline will skip docs it has seen before
pipeline = IngestionPipeline(
    transformations=[...],
    docstore=RedisDocumentStore.from_host_and_port(
        host="localhost", port=6379
    ),
    docstore_strategy="upserts",  # re-process if content changed
)
```

From the LlamaIndex docs: "Attaching a docstore to the ingestion pipeline will enable document management. Using the `document.doc_id` as a grounding point, the ingestion pipeline will actively look for duplicates."

Set `doc_id` to include a content hash so re-ingesting the same annual report is a no-op:

```python
import hashlib

def make_doc_id(company_id: str, source_file: str, page_num: int) -> str:
    content = f"{company_id}::{source_file}::page{page_num}"
    return hashlib.md5(content.encode()).hexdigest()

doc.doc_id = make_doc_id(company_id, source_file, page_num)
```

### 3.7 Pipeline 1 — complete flow diagram

```
PDF / HTML / Scanned
        │
        ▼
  ┌─────────────┐    table → Markdown
  │  Parser     │    text  → plain text
  │  (pdfplumber│    image → [placeholder / caption]
  │  + PyMuPDF) │    page  → metadata field
  └──────┬──────┘
         │ Document objects with page_label metadata
         ▼
  ┌─────────────────────────────┐
  │  HierarchicalNodeParser     │
  │  chunk_sizes=[2048,512,128] │
  └──────┬──────────────────────┘
         │ Nodes at 3 hierarchy levels
         ▼
  ┌─────────────────────────────┐
  │  IngestionPipeline          │
  │  (num_workers=4, async)     │
  │  ├─ TitleExtractor          │
  │  ├─ KeywordExtractor        │
  │  └─ EmbeddingModel          │
  └──────┬──────────────────────┘
         │
    ┌────┴─────────────────┐
    ▼                       ▼
Vector Store            Doc Registry
(Qdrant)                (PostgreSQL)
leaf nodes +            company_id,
embeddings              file_name,
                        status,
    ▼                   page_count
Docstore
(Redis)                 Staging Store
all hierarchy           (PostgreSQL)
levels for              entity JSON
AutoMerging             from metadata
                        extractors
```

---

## 4. Pipeline 2 — Knowledge Graph Construction

### 4.1 Why a schema-first approach is essential for financial entities

A generic entity extractor given the text "Vedanta Resources holds 64.9% of Hindustan Zinc" might extract:

- Entity: "Vedanta Resources" (ORGANIZATION)
- Entity: "Hindustan Zinc" (ORGANIZATION)
- Relation: "holds" (GENERIC_VERB)

That's not useful. For InsightOS we need:

- Entity: "Vedanta Resources" (PROMOTER)
- Entity: "Hindustan Zinc" (COMPANY, NSE: HINDZINC)
- Relation: "PROMOTER_OF" with property stake=64.9%

LlamaIndex's `SchemaLLMPathExtractor` allows you to define exactly this.

### 4.2 Financial entity schema design

```python
from typing import Literal
from llama_index.core.indices.property_graph import SchemaLLMPathExtractor

# ── Entity types for InsightOS ────────────────────────────────────────
FINANCIAL_ENTITIES = Literal[
    "COMPANY",          # Listed entity (HZL, Federal Bank, etc.)
    "SUBSIDIARY",       # Subsidiary or JV of a company
    "PROMOTER",         # Promoter / promoter group
    "SHAREHOLDER",      # Major institutional / non-promoter shareholder
    "OFFICER",          # KMP: MD, CEO, CFO, Director
    "AUDITOR",          # Statutory auditor firm
    "RATING_AGENCY",    # CRISIL, ICRA, CARE, etc.
    "BANK",             # Lending bank or financial institution
    "REGULATOR",        # SEBI, RBI, MCA
    "VENDOR_CUSTOMER",  # Key supplier or customer
]

# ── Relationship types ────────────────────────────────────────────────
FINANCIAL_RELATIONS = Literal[
    "SUBSIDIARY_OF",        # A is subsidiary of B
    "PROMOTED_BY",          # Company is promoted by Promoter
    "HOLDS_STAKE_IN",       # Shareholder holds % in Company
    "OFFICER_OF",           # Person is MD/CFO/Director of Company
    "AUDITED_BY",           # Company is audited by Auditor firm
    "RATED_BY",             # Company is rated by Rating Agency
    "RELATED_PARTY_OF",     # Related-party transaction link
    "BORROWS_FROM",         # Company borrows from Bank
    "JV_PARTNER_OF",        # Joint venture partnership
    "REGULATED_BY",         # Company is regulated by Regulator
    "SUPPLIES_TO",          # Vendor supplies to Company
]

# ── Schema: which entity types can connect via which relations ────────
FINANCIAL_SCHEMA = {
    "COMPANY":      ["SUBSIDIARY_OF", "PROMOTED_BY", "AUDITED_BY",
                     "RATED_BY", "RELATED_PARTY_OF", "BORROWS_FROM",
                     "JV_PARTNER_OF", "REGULATED_BY"],
    "SUBSIDIARY":   ["SUBSIDIARY_OF", "JV_PARTNER_OF"],
    "PROMOTER":     ["PROMOTED_BY", "HOLDS_STAKE_IN"],
    "SHAREHOLDER":  ["HOLDS_STAKE_IN"],
    "OFFICER":      ["OFFICER_OF"],
    "AUDITOR":      ["AUDITED_BY"],
    "RATING_AGENCY":["RATED_BY"],
    "BANK":         ["BORROWS_FROM"],
    "VENDOR_CUSTOMER": ["SUPPLIES_TO", "RELATED_PARTY_OF"],
    "REGULATOR":    ["REGULATED_BY"],
}

kg_extractor = SchemaLLMPathExtractor(
    llm=llm,
    possible_entities=FINANCIAL_ENTITIES,
    possible_relations=FINANCIAL_RELATIONS,
    kg_validation_schema=FINANCIAL_SCHEMA,
    strict=True,        # reject triples outside the schema
    num_workers=4,
    max_triplets_per_chunk=15,
)
```

### 4.3 Building the PropertyGraphIndex with Neo4j

```python
from llama_index.core import PropertyGraphIndex
from llama_index.graph_stores.neo4j import Neo4jPropertyGraphStore
import os

# ── Connect to Neo4j ──────────────────────────────────────────────────
graph_store = Neo4jPropertyGraphStore(
    username=os.getenv("NEO4J_USERNAME", "neo4j"),
    password=os.getenv("NEO4J_PASSWORD"),
    url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    database="insightos",
)

# ── Build the index ───────────────────────────────────────────────────
# Only run on the entity-rich sections — not boilerplate pages
# Filter nodes to those tagged with high-entity sections:
# "related_parties", "directors_report", "shareholding_pattern"
entity_rich_nodes = [
    n for n in all_nodes
    if n.metadata.get("section_type") in [
        "related_parties",
        "directors_report",
        "shareholding_pattern",
        "management_discussion",
        "key_managerial_personnel",
    ]
]

kg_index = PropertyGraphIndex(
    nodes=entity_rich_nodes,
    property_graph_store=graph_store,
    kg_extractors=[
        kg_extractor,           # Schema-constrained LLM extraction
        ImplicitPathExtractor(), # Also use LlamaIndex implicit relations
    ],
    show_progress=True,
)
```

### 4.4 Entity resolution — the deduplication problem

Before loading into Neo4j, resolve co-references:

```python
# Common co-reference patterns in Indian financial filings
ENTITY_ALIASES = {
    "FEDERALBNK":  ["Federal Bank", "Federal Bank Ltd", "Federal Bank Limited",
                    "the Bank", "FB"],
    "HINDZINC":    ["Hindustan Zinc", "Hindustan Zinc Ltd", "HZL",
                    "Hindustan Zinc Limited"],
    "CHOLAFIN":    ["Cholamandalam", "Chola", "Cholamandalam Investment",
                    "Cholamandalam Inv & Fin"],
}

def resolve_entity(raw_name: str, company_id: str) -> str:
    """Normalize entity name to canonical form."""
    raw_lower = raw_name.lower().strip()
    # Check aliases for the current company
    for canonical, aliases in ENTITY_ALIASES.items():
        for alias in aliases:
            if alias.lower() in raw_lower or raw_lower in alias.lower():
                return canonical
    return raw_name  # Return as-is if no match found
```

### 4.5 Querying the knowledge graph

The `PropertyGraphIndex` provides multiple retrieval methods. For InsightOS, use `TextToCypherRetriever` for natural language graph queries:

```python
from llama_index.core.indices.property_graph import TextToCypherRetriever

# Answer: "Who are the promoters of Hindustan Zinc?"
# This generates and executes Cypher against Neo4j automatically
cypher_retriever = TextToCypherRetriever(
    kg_index.property_graph_store,
    llm=llm,
)

results = cypher_retriever.retrieve(
    "Who are the promoters and major shareholders of Hindustan Zinc?"
)

# The generated Cypher looks like:
# MATCH (p:PROMOTER)-[:PROMOTED_BY]-(c:COMPANY {name: 'Hindustan Zinc'})
# RETURN p.name, p.stake
```

### 4.6 Pipeline 2 — complete flow diagram

```
Staging Store (entity-rich chunks from P1)
        │
        ▼
  ┌─────────────────────────────────────────┐
  │  Section Classifier                      │
  │  Filter to: related_parties,            │
  │  directors_report, shareholding, KMP    │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │  SchemaLLMPathExtractor                 │
  │  (strict financial schema)              │
  │  Extracts triplets:                     │
  │  (HZL, PROMOTED_BY, Vedanta)            │
  │  (Sandeep Modi, OFFICER_OF, HZL)        │
  │  (Deloitte, AUDITED_BY, HZL)            │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │  Entity Resolution                      │
  │  "HZL" → "HINDZINC" (canonical)        │
  │  Deduplicate nodes by company_id        │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │  PropertyGraphIndex → Neo4j             │
  │  Nodes: COMPANY, OFFICER, PROMOTER...  │
  │  Edges: PROMOTED_BY, OFFICER_OF...     │
  │  Properties: stake%, designation, date │
  └─────────────────────────────────────────┘
```

---

## 5. Pipeline 3 — RAG System and Chat UI

### 5.1 Query routing — not all questions are the same

Financial user queries fall into three categories, each needing a different retrieval strategy:

| Query Type | Example | Best Retrieval |
|---|---|---|
| Factual lookup | "What is Federal Bank's NPA ratio for Q3 FY25?" | Vector retrieval (semantic) |
| Entity/relationship | "Who are Hindustan Zinc's related parties?" | Knowledge graph (Cypher) |
| Comparison/synthesis | "Compare Federal Bank vs City Union Bank's credit growth" | Multi-index retrieval |

### 5.2 Hybrid retrieval setup

```python
from llama_index.core.retrievers import (
    VectorIndexRetriever,
    QueryFusionRetriever,
)
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.postprocessor import SentenceTransformerRerank

# ── Company-scoped vector retriever ──────────────────────────────────
# Filter retrieval to documents from a specific company
from llama_index.core.vector_stores.types import (
    MetadataFilter,
    MetadataFilters,
    FilterOperator,
)

def get_company_retriever(company_id: str, index: VectorStoreIndex):
    """Returns a retriever scoped to one company's documents."""
    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="company_id",
                value=company_id,
                operator=FilterOperator.EQ,
            )
        ]
    )
    return VectorIndexRetriever(
        index=index,
        similarity_top_k=15,
        filters=filters,
    )

# ── BM25 keyword retriever (for financial jargon like "NPA", "AUM") ──
bm25_retriever = BM25Retriever.from_defaults(
    nodes=leaf_nodes,
    similarity_top_k=10,
)

# ── Fuse both retrievers with Reciprocal Rank Fusion ─────────────────
fusion_retriever = QueryFusionRetriever(
    retrievers=[vector_retriever, bm25_retriever],
    similarity_top_k=10,
    num_queries=3,       # generates query variants for better recall
    mode="reciprocal_rerank",
)

# ── Re-rank with a cross-encoder ─────────────────────────────────────
reranker = SentenceTransformerRerank(
    model="cross-encoder/ms-marco-MiniLM-L-2-v2",
    top_n=5,
)
```

### 5.3 Graph-augmented retrieval

After vector retrieval, enrich the context with the knowledge graph:

```python
from llama_index.core.indices.property_graph import (
    VectorContextRetriever,
    LLMSynonymRetriever,
    PGRetriever,
)

# ── Graph retriever ───────────────────────────────────────────────────
sub_retrievers = [
    VectorContextRetriever(
        kg_index.property_graph_store,
        embed_model=embed_model,
        similarity_top_k=3,
        include_text=True,
    ),
    LLMSynonymRetriever(
        kg_index.property_graph_store,
        llm=llm,
        include_text=True,
        path_depth=2,        # 2-hop: find subsidiaries of subsidiaries
    ),
]

graph_retriever = PGRetriever(sub_retrievers=sub_retrievers)
```

### 5.4 CitationQueryEngine — getting page-level citations

```python
from llama_index.core.query_engine import CitationQueryEngine
from llama_index.core import get_response_synthesizer

# ── Build the full RAG query engine ──────────────────────────────────
synthesizer = get_response_synthesizer(
    response_mode="compact",
    use_async=True,
)

citation_engine = CitationQueryEngine(
    index=vector_index,
    retriever=fusion_retriever,
    node_postprocessors=[reranker],
    citation_chunk_size=512,
    citation_chunk_overlap=20,
)

# ── Query with full citations ─────────────────────────────────────────
response = citation_engine.query(
    "What is Federal Bank's net NPA ratio and what are the key drivers?"
)

print(response.response)
# "Federal Bank's net NPA ratio stood at 0.60% as of Q3 FY25, driven by
#  improvement in the retail and agriculture segments [1][2]."

# Citations with full provenance
for i, node in enumerate(response.source_nodes, 1):
    print(f"[{i}] {node.metadata['source_file']} "
          f"| Page {node.metadata['page_label']} "
          f"| Score: {node.score:.3f}")
# [1] federal_bank_q3fy25_transcript.pdf | Page 8 | Score: 0.923
# [2] federal_bank_ar_fy24.pdf | Page 142 | Score: 0.871
```

### 5.5 Query router — directing questions to the right backend

```python
from llama_index.core.query_engine import RouterQueryEngine
from llama_index.core.selectors import LLMSingleSelector
from llama_index.core.tools import QueryEngineTool

# ── Tool 1: Vector RAG for factual questions ─────────────────────────
vector_tool = QueryEngineTool.from_defaults(
    query_engine=citation_engine,
    description=(
        "Useful for answering factual questions about financial metrics, "
        "ratios, guidance, and qualitative management commentary from "
        "annual reports, earnings transcripts, and filings."
    ),
)

# ── Tool 2: Knowledge graph for relationship questions ────────────────
kg_tool = QueryEngineTool.from_defaults(
    query_engine=kg_index.as_query_engine(
        sub_retrievers=[LLMSynonymRetriever(...), TextToCypherRetriever(...)]
    ),
    description=(
        "Useful for questions about company structure, ownership, "
        "subsidiaries, officers, related parties, and entity relationships."
    ),
)

# ── Router selects the right tool ────────────────────────────────────
router_engine = RouterQueryEngine(
    selector=LLMSingleSelector.from_defaults(),
    query_engine_tools=[vector_tool, kg_tool],
)

# The router sends "Who are the promoters?" → KG tool
# The router sends "What is the NPA ratio?" → Vector RAG tool
```

### 5.6 LlamaIndex Workflow for the full query pipeline

LlamaIndex Workflows provide an async, event-driven way to chain the query steps. From the docs:

> "A workflow is an event-driven, step-based way to control the execution flow of an application. Workflows run on Python's asyncio event loop."

```python
from llama_index.core.workflow import (
    Workflow,
    step,
    Event,
    StartEvent,
    StopEvent,
)
from dataclasses import dataclass

@dataclass
class QueryEvent(Event):
    query: str
    company_id: str

@dataclass
class RetrievedEvent(Event):
    query: str
    nodes: list
    graph_context: str

@dataclass
class RankedEvent(Event):
    query: str
    top_nodes: list
    graph_context: str

class InsightOSQueryWorkflow(Workflow):

    @step
    async def receive_query(self, ev: StartEvent) -> QueryEvent:
        return QueryEvent(
            query=ev.query,
            company_id=ev.company_id,
        )

    @step
    async def hybrid_retrieve(self, ev: QueryEvent) -> RetrievedEvent:
        # Company-scoped vector + BM25 retrieval
        retriever = get_company_retriever(ev.company_id, vector_index)
        vector_nodes = await retriever.aretrieve(ev.query)

        # Parallel: also query knowledge graph
        graph_nodes = await graph_retriever.aretrieve(ev.query)
        graph_context = "\n".join([n.text for n in graph_nodes[:3]])

        return RetrievedEvent(
            query=ev.query,
            nodes=vector_nodes,
            graph_context=graph_context,
        )

    @step
    async def rerank(self, ev: RetrievedEvent) -> RankedEvent:
        top_nodes = reranker.postprocess_nodes(
            ev.nodes, query_str=ev.query
        )
        return RankedEvent(
            query=ev.query,
            top_nodes=top_nodes[:5],
            graph_context=ev.graph_context,
        )

    @step
    async def synthesize(self, ev: RankedEvent) -> StopEvent:
        # Build context including graph enrichment
        context = ev.graph_context + "\n\n" + "\n\n".join(
            [f"[Source: {n.metadata['source_file']} p.{n.metadata['page_label']}]\n{n.text}"
             for n in ev.top_nodes]
        )
        response = await llm.acomplete(
            f"Answer based on context:\n{context}\n\nQuestion: {ev.query}"
        )
        citations = [
            {
                "file": n.metadata["source_file"],
                "page": n.metadata["page_label"],
                "score": round(n.score, 3),
                "passage": n.text[:200],
            }
            for n in ev.top_nodes
        ]
        return StopEvent(result={"answer": str(response), "citations": citations})

# Run the workflow
workflow = InsightOSQueryWorkflow(timeout=30, verbose=False)
result = await workflow.run(
    query="What is the bank's guidance on credit growth?",
    company_id="FEDERALBNK",
)
```

---

## 6. Cross-Cutting Concerns

### 6.1 Document registry schema (PostgreSQL)

```sql
CREATE TABLE document_registry (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      VARCHAR(20) NOT NULL,          -- NSE symbol
    company_name    VARCHAR(200) NOT NULL,
    doc_type        VARCHAR(50) NOT NULL,           -- annual_report, concall, rating
    fiscal_year     VARCHAR(10),                    -- FY24, Q3FY25
    source_file     VARCHAR(500) NOT NULL,
    page_count      INTEGER,
    content_hash    VARCHAR(64) UNIQUE,             -- MD5 for dedup
    ingestion_status VARCHAR(20) DEFAULT 'pending', -- pending, running, done, failed
    p1_completed_at  TIMESTAMP,
    p2_completed_at  TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- Index for company-scoped queries
CREATE INDEX idx_registry_company ON document_registry(company_id);
CREATE INDEX idx_registry_status  ON document_registry(ingestion_status);
```

### 6.2 Pipeline orchestration with Prefect

```python
from prefect import flow, task

@task(retries=3, retry_delay_seconds=60)
async def run_pipeline_1(company_id: str, doc_paths: list[str]):
    results = await ingest_company_documents(company_id, doc_paths, semaphore)
    update_registry_status(company_id, "p1_done")
    return results

@task(retries=2, retry_delay_seconds=30)
async def run_pipeline_2(company_id: str):
    build_knowledge_graph(company_id)
    update_registry_status(company_id, "p2_done")

@flow(name="insightos-full-ingestion")
async def full_ingestion_flow(company_documents: dict):
    for company_id, paths in company_documents.items():
        p1_result = await run_pipeline_1(company_id, paths)
        await run_pipeline_2(company_id)  # waits for P1

    print("All companies ingested and graphs built.")
```

### 6.3 Update strategy — handling quarterly refreshes

When a new quarterly transcript arrives:

1. Compute `content_hash` of new file
2. Check `document_registry` — if hash exists, skip entirely (idempotent)
3. If new: run P1 ingestion with `docstore_strategy="upserts"` — only new/changed nodes are embedded
4. Run P2 with `merge=True` — add new triplets to existing Neo4j graph, do not replace
5. No rebuild of the full vector index needed — Qdrant supports incremental upserts

### 6.4 RAG evaluation with RAGAS

```python
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from datasets import Dataset

# Build test dataset from known Q&A pairs
eval_data = {
    "question": ["What is Federal Bank's net NPA?", ...],
    "answer":   ["0.60% as of Q3 FY25", ...],             # ground truth
    "contexts": [["chunk1 text", "chunk2 text"], ...],     # retrieved chunks
    "ground_truth": ["0.60%", ...],
}

result = evaluate(
    Dataset.from_dict(eval_data),
    metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
)

print(result)
# faithfulness: 0.91, answer_relevancy: 0.87,
# context_precision: 0.84, context_recall: 0.79
```

---

## 7. Low-Level Component Design

### 7.1 Component interaction map

```
┌──────────────────────────────────────────────────────────────────┐
│                        API LAYER                                  │
│  FastAPI · /query · /ingest · /companies · /graph               │
└────────────────┬────────────────────────────────┬────────────────┘
                 │                                │
        ┌────────▼──────────┐          ┌──────────▼───────────┐
        │  Query Workflow    │          │  Ingestion Workflow   │
        │  (LlamaIndex WF)  │          │  (LlamaIndex WF)     │
        └────────┬──────────┘          └──────────┬───────────┘
                 │                                │
     ┌───────────┼───────────┐       ┌────────────┼───────────────┐
     │           │           │       │            │               │
┌────▼───┐ ┌────▼────┐ ┌────▼───┐ ┌──▼─────┐ ┌───▼────┐ ┌──────▼──┐
│ Qdrant │ │  Neo4j  │ │  LLM   │ │Parser  │ │Chunker │ │Embedder │
│Vector  │ │ Graph   │ │ (GPT-4 │ │(PyMuPDF│ │(Hierch.│ │(OAI     │
│Store   │ │   DB    │ │ /local)│ │+plumber│ │Parser) │ │ada-002) │
└────────┘ └─────────┘ └────────┘ └────────┘ └────────┘ └─────────┘
                                       │
                              ┌────────▼────────┐
                              │   PostgreSQL     │
                              │  Doc Registry    │
                              │  + Docstore      │
                              └─────────────────┘
```

### 7.2 Memory and throughput estimates

For a 300-page annual report:

| Step | Estimated Time | Notes |
|---|---|---|
| PDF parse (pdfplumber) | 30–60 seconds | CPU-bound, parallelisable |
| Hierarchical chunk (2048/512/128) | 5–10 seconds | Fast, pure Python |
| Metadata extraction (LLM) | 4–8 min | 1 LLM call per chunk, ~600 chunks |
| Embedding (batch API) | 2–3 min | Batching 100 chunks per API call |
| KG extraction (LLM) | 3–6 min | Only entity-rich sections, ~100 chunks |
| Neo4j load | 30 seconds | Bulk insert |
| **Total per document** | **~12–18 min** | With 4 parallel workers |
| **Total for 20 companies × 3 docs** | **~60–90 min** | With 5 parallel companies |

### 7.3 Chunk size decision guide

| Scenario | Recommended chunk_sizes | Why |
|---|---|---|
| Annual reports (300 pages) | `[2048, 512, 128]` | Preserves section context, precise retrieval |
| Concall transcripts (30–60 pages) | `[1024, 256]` | Shorter docs, speaker turns are natural units |
| Rating reports (10–30 pages) | `[512, 128]` | Dense, every sentence counts |
| HTML filings | `[512, 128]` | Well-structured, section headers are boundaries |

---

## 8. Data Schemas

### 8.1 Neo4j node structure

```
(COMPANY {
    name: "Hindustan Zinc Ltd",
    canonical_id: "HINDZINC",
    nse_symbol: "HINDZINC",
    bse_code: "500188",
    isin: "INE267A01025",
    sector: "Metals"
})

(OFFICER {
    name: "Sandeep Modi",
    designation: "CFO",
    din: "XXXXXXXX"
})

(PROMOTER {
    name: "Vedanta Resources",
    stake_pct: 64.9,
    type: "Corporate Promoter"
})
```

### 8.2 Neo4j edge structure

```
(OFFICER)-[:OFFICER_OF {
    as_of: "2024-03-31",
    source_doc: "hzl_ar_fy24.pdf",
    source_page: 112
}]->(COMPANY)

(PROMOTER)-[:HOLDS_STAKE_IN {
    stake_pct: 64.9,
    as_of: "2024-03-31",
    category: "Promoter Group"
}]->(COMPANY)
```

### 8.3 Qdrant vector payload structure

Each vector point carries this payload:

```json
{
    "company_id":   "FEDERALBNK",
    "source_file":  "federal_bank_ar_fy24.pdf",
    "page_label":   "87",
    "doc_type":     "annual_report",
    "fiscal_year":  "FY24",
    "section":      "management_discussion",
    "hierarchy_level": "leaf",
    "chunk_index":  342,
    "title":        "Asset Quality and NPA Discussion",
    "keywords":     ["NPA", "GNPA", "credit cost", "slippage"],
    "text":         "The Gross NPA ratio improved to 2.35% as of March 2024..."
}
```

---

## 9. Full Code Reference

### 9.1 Project structure

```
insightos/
│
├── pipelines/
│   ├── p1_ingestion/
│   │   ├── parser.py            # PDF/HTML parsing
│   │   ├── chunker.py           # HierarchicalNodeParser setup
│   │   ├── pipeline.py          # IngestionPipeline with async
│   │   └── registry.py          # Document registry operations
│   │
│   ├── p2_graph/
│   │   ├── schema.py            # FINANCIAL_ENTITIES, RELATIONS, SCHEMA
│   │   ├── extractor.py         # SchemaLLMPathExtractor
│   │   ├── resolver.py          # Entity resolution / dedup
│   │   └── loader.py            # PropertyGraphIndex + Neo4j
│   │
│   └── p3_rag/
│       ├── retriever.py         # Hybrid retrieval + AutoMerging
│       ├── reranker.py          # SentenceTransformer reranker
│       ├── query_engine.py      # CitationQueryEngine + RouterQueryEngine
│       └── workflow.py          # InsightOSQueryWorkflow
│
├── orchestration/
│   ├── scheduler.py             # Prefect flows
│   └── update_strategy.py       # Incremental re-ingestion logic
│
├── api/
│   ├── main.py                  # FastAPI app
│   ├── routes/
│   │   ├── query.py             # POST /query
│   │   ├── ingest.py            # POST /ingest
│   │   └── graph.py             # GET /graph/{company_id}
│   └── models.py                # Pydantic request/response schemas
│
├── eval/
│   └── ragas_eval.py            # RAGAS evaluation pipeline
│
├── docker/
│   ├── docker-compose.yml       # Qdrant + Neo4j + Redis + PostgreSQL + API
│   └── Dockerfile
│
└── config/
    └── settings.py              # Environment-based config
```

### 9.2 docker-compose.yml

```yaml
version: "3.9"
services:

  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: neo4j/insightos_secret
      NEO4J_PLUGINS: '["apoc", "graph-data-science"]'
    ports: ["7474:7474", "7687:7687"]
    volumes: ["./data/neo4j:/data"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: ["./data/redis:/data"]

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: insightos
      POSTGRES_USER: insightos
      POSTGRES_PASSWORD: insightos_secret
    ports: ["5432:5432"]
    volumes: ["./data/postgres:/var/lib/postgresql/data"]

  api:
    build: .
    ports: ["8000:8000"]
    depends_on: [qdrant, neo4j, redis, postgres]
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      NEO4J_URI: bolt://neo4j:7687
      QDRANT_URL: http://qdrant:6333
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://insightos:insightos_secret@postgres/insightos
```

---

## 10. Deployment Architecture

### 10.1 Environment overview

```
┌────────────────────────────────────────────────────────────────┐
│                    PRODUCTION ENVIRONMENT                       │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  React UI    │    │  FastAPI     │    │  Prefect Server  │  │
│  │  (chat +     │───▶│  (query +    │    │  (pipeline       │  │
│  │   graph viz) │    │   ingest)    │    │   scheduling)    │  │
│  └──────────────┘    └──────┬───────┘    └────────┬─────────┘  │
│                             │                      │            │
│         ┌───────────────────┼──────────────────────┘            │
│         │                  │                                    │
│  ┌──────▼──────┐   ┌────────▼──────┐   ┌──────────────────┐   │
│  │   Qdrant    │   │    Neo4j      │   │   PostgreSQL     │   │
│  │ (vectors)   │   │ (graph DB)    │   │ (registry +      │   │
│  │             │   │               │   │  docstore)       │   │
│  └─────────────┘   └───────────────┘   └──────────────────┘   │
│                                                                 │
│  ┌──────────────┐   ┌──────────────────────────────────────┐   │
│  │    Redis     │   │          LLM Provider                │   │
│  │  (docstore + │   │  OpenAI API  OR  local Ollama        │   │
│  │   job cache) │   │  (qwen2.5, llama3.1 for KG extract)  │   │
│  └──────────────┘   └──────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

### 10.2 Local LLM option for KG extraction cost control

Knowledge graph extraction makes many LLM calls (one per chunk for entity extraction). At 300 pages × ~3 chunks per page = ~900 LLM calls per document, GPT-4o costs add up. Use a local model for extraction:

```python
from llama_index.llms.ollama import Ollama

# Use a capable local model for KG extraction to save costs
extraction_llm = Ollama(model="qwen2.5:14b", request_timeout=120.0)

# Use GPT-4o only for the final answer synthesis
synthesis_llm = OpenAI(model="gpt-4o", temperature=0)

kg_extractor = SchemaLLMPathExtractor(
    llm=extraction_llm,   # cheaper local model for bulk extraction
    ...
)

citation_engine = CitationQueryEngine(
    llm=synthesis_llm,    # high-quality model for user-facing answers
    ...
)
```

---

## Key References from LlamaIndex Documentation

- [IngestionPipeline with parallel execution](https://docs.llamaindex.ai/en/stable/examples/ingestion/parallel_execution_ingestion_pipeline)
- [HierarchicalNodeParser + AutoMergingRetriever](https://docs.llamaindex.ai/en/stable/examples/retrievers/auto_merging_retriever)
- [PropertyGraphIndex guide](https://docs.llamaindex.ai/en/stable/module_guides/indexing/lpg_index_guide)
- [SchemaLLMPathExtractor for typed KG](https://docs.llamaindex.ai/en/stable/examples/property_graph/dynamic_kg_extraction)
- [Neo4j PropertyGraph integration](https://docs.llamaindex.ai/en/stable/examples/property_graph/property_graph_neo4j)
- [CitationQueryEngine](https://docs.llamaindex.ai/en/stable/examples/query_engine/citation_query_engine)
- [LlamaIndex Workflows — async](https://docs.llamaindex.ai/en/stable/module_guides/workflow/async_workflows)
- [LlamaExtract for financial reports](https://docs.llamaindex.ai/en/stable/examples/llamaextract/extract_data_with_citations)
- [Document management + dedup pipeline](https://docs.llamaindex.ai/en/stable/examples/ingestion/document_management_pipeline)
