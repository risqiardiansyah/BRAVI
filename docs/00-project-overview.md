# 00 — Project Overview

## 1. Project Name
`bravi-ai-chatbot`

## 2. One-line Summary
An AI Agent Orchestration backend that powers a document-grounded chatbot (text + image input), with separate flows for end-users and operators, built on Python, LangGraph, PostgreSQL/pgvector, and AWS Bedrock.

## 3. Purpose

This project exists to give Bravi a chatbot that answers questions **only from a curated, ingested knowledge base** (files/documents), rather than freely from general LLM knowledge. It also gives operators tools to manage that knowledge base and monitor how the system is being used (cost, latency, model, question trends).

## 4. Problem It Solves

- No existing internal system orchestrates retrieval-augmented generation (RAG) against controlled document sources.
- No visibility into AI usage cost/latency/model mix.
- No mechanism to avoid wasteful LLM calls on trivial or irrelevant input.

## 5. Scope Summary

| In Scope | Out of Scope |
|---|---|
| Chat orchestration (User + Operator) | Login / authentication / RBAC |
| Document ingestion (startup + on-demand) | Front-end UI |
| Embedding & vector search (pgvector) | Multi-tenant support |
| AWS Bedrock integration (embedding + text) | Model fine-tuning |
| Usage analytics & observability | Voice/real-time streaming audio |
| Cost-control short-circuits (greeting, out-of-topic, similarity threshold) | Payment/billing integration |
| Production hardening (Redis-backed rate limiting, Bedrock resilience, retention cleanup) | Multi-region / multi-tenant support |

## 6. Key Stakeholder Roles

| Role | Responsibility |
|---|---|
| Product/Tech Lead | Owns PRD, prioritization |
| Backend Engineers | Implement FastAPI/Python services |
| AI/Agent Engineers | Design & implement LangGraph agent graphs |
| DevOps | Deployment, environment/secret management |
| Operators (end users of Operator role) | Manage knowledge base, monitor analytics |

## 7. High-Level System Summary

```
                 ┌─────────────────────┐
   User/Visitor  │                     │
   (text/image) ─┼──▶  /api/chat        │
                 │                     │      ┌─────────────────────┐
                 │   FastAPI Backend   │──────▶  LangGraph Agent(s)  │
   Operator     ─┼──▶ /api/opr/*       │      │  (Chat / Ingestion)  │
   (text/image, │                     │◀──────                     │
    ingest,     │                     │      └──────────┬───────────┘
    analytics)  └─────────┬───────────┘                 │
                          │                              ▼
                          ▼                    ┌─────────────────────┐
                 ┌─────────────────────┐       │   AWS Bedrock        │
                 │ PostgreSQL + pgvector│       │ (Embedding + Text)  │
                 │ (knowledge, sessions,│       └─────────────────────┘
                 │  messages, metrics)  │
                 └─────────────────────┘
```

## 8. Related Documents

See `README.md` for the full documentation index.

## 9. Glossary

| Term | Meaning |
|---|---|
| RAG | Retrieval-Augmented Generation |
| Session | A conversation thread identified by `session_id` |
| Ingestion | The process of chunking, embedding, and storing a document into pgvector |
| Short-circuit | A response path that avoids calling the LLM (greeting, out-of-topic, low-similarity) |
| Contextual condensation | Summarizing/compressing prior conversation turns to control token usage |
| Operator | Internal user managing knowledge & viewing analytics |
| User/Visitor | End-user chatting with the bot without knowledge-management privileges |
