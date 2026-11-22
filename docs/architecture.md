# Architecture (High-Level Design)

This document describes how the whole system is put together: the components, how they talk to each other, where data lives, and why it is built this way. For step-by-step request flows see [workflow.md](workflow.md); for MCP connectors in depth see [connectors.md](connectors.md).

---

## 1. System context

Who and what the system talks to.

```mermaid
flowchart TB
    user(["👤 User<br/>browser, phone or TV"])

    subgraph app["Kohli RAG"]
        fe["Frontend<br/>Next.js on Vercel"]
        be["Backend<br/>FastAPI"]
    end

    clerk["Clerk<br/>identity and sessions"]
    mongo[("MongoDB Atlas<br/>data + vector search")]
    llm["LLM providers<br/>Groq · Gemini · Mistral"]
    emb["Gemini embeddings"]
    meteo["Open-Meteo<br/>weather API"]
    gh["GitHub<br/>OAuth + remote MCP server"]

    user --> fe
    fe -- "sign-in UI" --> clerk
    fe -- "REST + SSE, Bearer token" --> be
    be -- "verify token (JWKS)" --> clerk
    be --> mongo
    be --> llm
    be --> emb
    be --> meteo
    be -- "OAuth token exchange, MCP tool calls" --> gh
    user -. "consent screen" .-> gh
```

| External system | Used for | Called by |
|---|---|---|
| Clerk | Sign-in / sign-up, sessions, short-lived JWTs | Frontend (UI), backend (verification, profile lookup) |
| MongoDB Atlas | Users, documents, chunks + embeddings, connector connections | Backend only |
| Groq / Gemini / Mistral | Routing decisions and answers (with fallback) | Backend only |
| Gemini embeddings | Turning chunks and questions into vectors | Backend only |
| Open-Meteo | Live weather tool | Backend only |
| GitHub | OAuth App consent + remote MCP server (read-only tools) | Backend; the browser only visits GitHub's consent page |

The browser never talks to MongoDB, LLMs or GitHub's API directly, and never sees an API key or a GitHub token.

---

## 2. Containers

The two deployable parts and their main building blocks.

```mermaid
flowchart LR
    subgraph FE["Frontend — Next.js 16"]
        direction TB
        proxy["proxy.ts<br/>page protection"]
        shell["App shell<br/>sidebar, theme, consent"]
        chatui["Chat view<br/>stream parser"]
        connui["Connectors page"]
        api["api-client<br/>token + 401 retry"]
        bridge["Auth session bridge"]
        chatui --> api
        connui --> api
        bridge -. "registers getToken" .-> api
    end

    subgraph BE["Backend — FastAPI"]
        direction TB
        routes["routes/<br/>chat · users · connectors"]
        dep["CurrentUser dependency"]
        authsvc["AuthService"]
        chatsvc["ChatService"]
        routerllm["Query router"]
        agents["Agents<br/>create_agent + middleware"]
        rag["RAG pipeline"]
        connsvc["ConnectorService"]
        routes --> dep --> authsvc
        routes --> chatsvc
        routes --> connsvc
        chatsvc --> routerllm
        chatsvc --> agents
        chatsvc --> rag
        chatsvc --> connsvc
    end

    api -- "HTTPS JSON / SSE" --> routes
```

### Frontend responsibilities

| Part | Files | Job |
|---|---|---|
| Page protection | `src/proxy.ts` | Redirect signed-out visitors to `/sign-in` (UX only; the backend is the real gate) |
| Clerk setup | `src/app/layout.tsx` | Our own sign-in/up pages, redirect to `/chat` after sign-in |
| App shell | `src/app/(app)/layout.tsx`, `components/shared/app-shell.tsx` | Collapsible/resizable sidebar; conversations shared between `/chat` and `/connectors` |
| API client | `src/lib/api-client.ts`, `lib/auth-token.ts` | Adds a fresh Clerk token to every request; one retry on 401; friendly errors |
| Auth bridge | `features/auth/components/auth-session-bridge.tsx` | Connects Clerk to the API client; clears cached data on sign-out |
| Chat | `features/chat/*` | Sends the question, parses SSE events, renders answer, tools, sources |
| Connectors | `features/connectors/*` | Lists connectors, starts OAuth (full-page redirect), disconnects |
| Consent | `features/consent/*` | Cookie choices stored in a first-party cookie |

### Backend responsibilities

| Part | Files | Job |
|---|---|---|
| HTTP layer | `app/routes/*` | Validate input, call a service, shape the response |
| Auth | `app/routes/dependencies.py`, `app/auth/*`, `infra/auth/*` | Token → verified Clerk user ID → MongoDB `User` (created on first request) |
| Chat orchestration | `app/ai/chat.py` | Route, run the right handler, stream SSE events |
| Router | `app/ai/router.py` | LLM with structured output picks RAG / TOOL / GITHUB / DIRECT |
| Agents | `app/ai/agent.py` | LangChain `create_agent` with model fallback, model retry, tool retry |
| RAG | `app/ai/rag/*`, `infra/db/mongo_vector_store.py` | Ingest PDFs, embed, vector search, build context |
| Connectors | `app/connectors/*`, `infra/oauth/*`, `infra/mcp/*` | OAuth, encrypted tokens, per-user MCP tools |
| Wiring | `app/core/container.py` | Creates every adapter once and injects it |

---

## 3. Backend layering (ports and adapters)

Business logic depends on small interfaces ("ports"); vendor code lives in adapters. Swapping Clerk, MongoDB or an MCP library means changing an adapter and one line in the container.

```mermaid
flowchart TB
    subgraph L1["HTTP layer — app/routes"]
        r1["chat.py"]
        r2["users.py"]
        r3["connectors.py"]
    end

    subgraph L2["Business logic — app/"]
        s1["ChatService<br/>app/ai/chat.py"]
        s2["AuthService<br/>app/auth/service.py"]
        s3["ConnectorService<br/>app/connectors/service.py"]
        p["Ports (Protocols)<br/>app/auth/ports.py<br/>app/connectors/ports.py"]
    end

    subgraph L3["Adapters — infra/"]
        a1["Clerk verifier / profile"]
        a2["Mongo repositories"]
        a3["Vector store"]
        a4["LLM + embedding providers"]
        a5["OAuth (GitHub)"]
        a6["MCP client"]
    end

    c["Composition root<br/>app/core/container.py"]

    L1 --> L2
    s2 --> p
    s3 --> p
    p -. "implemented by" .-> L3
    s1 --> a3
    s1 --> a4
    c -- "creates and injects" --> L2
    c -- "creates" --> L3
```

Rules the code follows:

1. `app/` never imports Motor, Clerk SDK or FastMCP directly (the AI layer uses LangChain, which is its framework).
2. `infra/` never contains business decisions (who may do what).
3. Routes never touch the database; they call a service.
4. Every user-owned query is filtered by the user ID from the verified token.

---

## 4. Request lifecycle at a glance

A chat question from the browser to the database and back. Detailed diagrams per route are in [workflow.md](workflow.md).

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Frontend
    participant CK as Clerk
    participant BE as Backend
    participant RT as Router LLM
    participant H as Route handler
    participant DB as MongoDB
    participant LLM as Answer LLM

    U->>FE: types a question, presses Enter
    FE->>CK: getToken()
    CK-->>FE: short-lived JWT
    FE->>BE: POST /llm {prompt}, Authorization Bearer
    BE->>BE: verify JWT (signature, exp, iss, azp)
    BE->>DB: find or create user
    BE->>RT: which route?
    RT-->>BE: RouteDecision(route, reason)
    BE-->>FE: SSE route
    BE->>H: run RAG / TOOL / GITHUB / DIRECT
    H->>DB: (RAG) vector search
    H->>LLM: messages + context/tools
    loop while generating
        LLM-->>BE: token
        BE-->>FE: SSE token
        FE-->>U: text appears
    end
    BE-->>FE: SSE sources (RAG), done
```

---

## 5. Features and how they are built

### 5.1 Authentication

- **Frontend:** Clerk manages its own secure cookies. `proxy.ts` sends signed-out visitors to `/sign-in`. The API client asks Clerk for a token on every request (Clerk refreshes them automatically) and retries once with a fresh token on 401.
- **Backend:** `ClerkTokenVerifier` checks the RS256 signature against Clerk's cached public keys, the expiry (with 5 s clock leeway), the issuer (`CLERK_ISSUER`) and the authorized party (`CLERK_AUTHORIZED_PARTIES`). The verified `sub` claim is the only source of identity.
- **Provisioning:** on the first request `AuthService` fetches the profile from Clerk once and inserts the user with an atomic upsert guarded by a unique index, so concurrent first requests can't create duplicates.
- **Errors:** 401 (bad token), 403 (deactivated user), 503 (auth dependency down — returned through CORS so the browser shows the right message).

### 5.2 Query routing

`QueryRouter` sends the question to a router model (Mistral → Groq → Gemini, temperature 0) with `with_structured_output(RouteDecision)`, so the result is always a valid `Route` enum. If every router model fails, the question goes to the default route (RAG).

### 5.3 Answer agents and resilience

`build_agent()` creates a LangChain agent with middleware:

```mermaid
flowchart LR
    q["Question"] --> F["ModelFallbackMiddleware<br/>Groq → Gemini → Mistral"]
    F --> R["ModelRetryMiddleware<br/>retries each model<br/>(exp. backoff + jitter)"]
    R --> M["Model call"]
    M -- "tool call" --> TR["ToolRetryMiddleware"]
    TR --> T["Tool"]
    T --> M
    M --> A["Answer stream"]
```

RAG, TOOL and DIRECT agents are built once at startup. The GITHUB agent is built **per request**, because its tools carry that user's own GitHub token.

### 5.4 RAG (documents)

```mermaid
flowchart LR
    subgraph ingest["Ingestion — at startup"]
        direction TB
        f["PDF in uploads/"] --> h{"SHA-256 already<br/>ingested?"}
        h -- yes --> skip["skip"]
        h -- "no / incomplete" --> x["Extract text + tables<br/>PyMuPDF"]
        x --> c["Chunk<br/>300 words, 50 overlap"]
        c --> e["Embed in batches<br/>gemini-embedding-001"]
        e --> s[("sources · chunks · tables")]
    end

    subgraph query["Retrieval — per question"]
        direction TB
        qq["Question"] --> qe["Embed question"]
        qe --> vs["$vectorSearch<br/>top 5 chunks"]
        vs --> ctx["Build context +<br/>source list"]
        ctx --> ans["RAG agent answers<br/>only from context"]
    end

    s -. "chunks_vector_index" .-> vs
```

Ingestion is idempotent: a file is identified by its hash, a `pending` record is written before chunks, and `completed` only after everything is stored — a crash mid-way is detected and retried on the next start.

### 5.5 Tools

`weather_tool` (Open-Meteo geocoding + forecast) and `inr_to_usd` return readable strings, including on errors, so the agent can explain problems instead of crashing.

### 5.6 MCP connectors (GitHub)

Users connect GitHub once with OAuth 2.0 + PKCE; the token is encrypted and stored per user; on GITHUB questions a fresh MCP client is created with that token, tools are discovered, filtered to read-only, namespaced, and given to a per-request agent. Full details: [connectors.md](connectors.md).

### 5.7 Frontend experience

- Layout: sidebar (collapse with Ctrl+B, drag to resize 13–26 rem, remembered only with "Preferences" consent), 56 rem conversation column.
- Conversations live in memory (React context in the `(app)` layout), so they survive moving between `/chat` and `/connectors`, and are cleared on sign-out or refresh.
- Design tokens in `src/styles/*.css`; every colour comes from CSS variables, so light/dark and TV scaling work everywhere.

---

## 6. Data model (MongoDB)

```mermaid
erDiagram
    USERS ||--o{ MCP_CONNECTIONS : "owns"
    USERS ||--o{ OAUTH_TRANSACTIONS : "starts"
    SOURCES ||--o{ CHUNKS : "split into"
    SOURCES ||--o{ TABLES : "contains"

    USERS {
        ObjectId _id
        string clerk_user_id UK "link to Clerk"
        string email
        string first_name
        string last_name
        string role "user or admin"
        bool is_active
        date created_at
        date last_login
    }
    MCP_CONNECTIONS {
        ObjectId _id
        string user_id "users._id"
        string provider_key "github"
        string server_url
        string status "connected, needs_reauth, error"
        array scopes
        string remote_account_login
        binData token_ciphertext "encrypted, never plaintext"
        date expires_at
        date created_at
        date last_connected_at
    }
    OAUTH_TRANSACTIONS {
        ObjectId _id
        string state_hash UK "sha256 of state"
        string user_id
        string provider_key
        string return_origin
        binData pkce_verifier_ciphertext
        date expires_at "TTL index"
    }
    SOURCES {
        ObjectId _id
        string filename
        string file_hash
        string ingestion_status
    }
    CHUNKS {
        ObjectId _id
        string source_id
        int chunk_index
        string text
        array embedding "3072 floats"
    }
    TABLES {
        ObjectId _id
        string source_id
        int page_number
    }
```

| Collection | Indexes | Notes |
|---|---|---|
| `users` | unique `clerk_user_id` | No passwords or tokens — Clerk owns credentials |
| `mcp_connections` | unique `(user_id, provider_key)` | `$jsonSchema` validator; token is Fernet ciphertext |
| `oauth_transactions` | unique `state_hash`, TTL on `expires_at` | Read-and-deleted atomically by the callback (single use) |
| `chunks` | Atlas Vector Search `chunks_vector_index` | Embeddings + metadata for retrieval |
| `sources`, `tables` | — | One record per ingested file; extracted tables |

Schema management: Pydantic models (Python) ↔ repositories (`infra/db/*_repository.py`) ↔ MongoDB `$jsonSchema` validators (`infra/db/schemas.py`, applied at startup).

---

## 7. Deployment view

```mermaid
flowchart LR
    b(["Browser"]) -- "https" --> v["Vercel<br/>Next.js frontend"]
    b -- "https (API + SSE)" --> api["Backend host<br/>FastAPI + Uvicorn"]
    b -. "OAuth consent" .-> ghc["github.com"]
    api --> atlas[("MongoDB Atlas")]
    api --> prov["Groq · Gemini · Mistral"]
    api --> ghmcp["api.githubcopilot.com/mcp"]
    ghc -. "redirect with code" .-> api
    api -. "303 redirect" .-> v
```

Production checklist:

- Set `FRONTEND_URL` and `CLERK_AUTHORIZED_PARTIES` to the production frontend origin.
- Update the GitHub OAuth App's homepage and callback URLs to the production domains (and `GITHUB_CALLBACK_URL`).
- Generate a **new** `CONNECTOR_ENCRYPTION_KEYS` for production and store it in the host's secret settings.
- Use Clerk production keys.
- Serve both apps over HTTPS.

---

## 8. Design decisions

| Decision | Why |
|---|---|
| LLM router instead of one big agent | Each route gets a focused prompt and toolset; cheaper and more predictable |
| Structured output for routing | The route is always a valid enum, never text to parse |
| SSE streaming | Users see the answer immediately; tool and source events show progress |
| Ports and adapters | Vendors can be swapped; business logic is testable with fakes |
| Backend verifies every token | Frontend protection can be bypassed; the API cannot |
| Per-request GitHub agent | Tools carry one user's token; no shared client can leak across users |
| Encrypted tokens, key outside the DB | A database leak alone doesn't expose anyone's GitHub |
| Catalog of allowed MCP servers | Prevents the backend being used to reach arbitrary hosts (SSRF) |
| In-memory conversations | The backend stores no chat history yet; nothing sensitive persists |
