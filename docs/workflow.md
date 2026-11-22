# Workflow — how a prompt travels through the app

This document follows one question from the moment the user presses **Enter** to the last word on screen. Every diagram is Mermaid, so GitHub draws it.

### Shape legend

```mermaid
flowchart LR
    s(["Start / End"]) --> p["Process step"] --> d{"Decision"} --> io[/"Input / Output"/] --> db[("Database")] --> ext[["External service"]]
```

| Shape | Meaning |
|---|---|
| Rounded pill | Start or end of a flow |
| Rectangle | A step the code performs |
| Diamond | A decision / branch |
| Parallelogram | Data going in or out (request, event) |
| Cylinder | MongoDB |
| Double-bordered box | An external service (Clerk, LLM, GitHub, Open-Meteo) |

---

## 1. The whole journey (swimlanes)

```mermaid
flowchart TB
    subgraph Browser["🖥️ Browser — Next.js"]
        A(["User presses Enter"]) --> B["ChatInput → useChatStream.sendMessage"]
        B --> C["Add user message +<br/>empty assistant message"]
        C --> D["api-client: getToken() from Clerk"]
        D --> E[/"POST /llm<br/>Authorization: Bearer JWT<br/>body: prompt"/]
        S["Parse SSE events<br/>stream-chat.ts"] --> T["applyStreamEvent:<br/>update the message"]
        T --> U(["Answer shown with<br/>route, model, tools, sources"])
    end

    subgraph Backend["⚙️ Backend — FastAPI"]
        F["CurrentUser dependency"] --> G{"Token valid?"}
        G -- no --> G1[/"401 JSON"/]
        G -- yes --> H["AuthService: find or create user"]
        H --> I["ChatService.chat(prompt, user)"]
        I --> J["QueryRouter.route()"]
        J --> K{"Route?"}
        K -- RAG --> K1["RAG handler"]
        K -- TOOL --> K2["Tool agent"]
        K -- GITHUB --> K3["GitHub agent<br/>(per user)"]
        K -- DIRECT --> K4["Direct agent"]
        K1 & K2 & K3 & K4 --> L["_stream_agent:<br/>astream_events v2"]
        L --> M[/"SSE: route, tool_*, model,<br/>token…, sources, done"/]
    end

    subgraph External["☁️ External"]
        CK[["Clerk JWKS"]]
        DB[("MongoDB Atlas")]
        LLM[["Groq / Gemini / Mistral"]]
        GH[["GitHub MCP"]]
    end

    E --> F
    F -. "public keys (cached)" .-> CK
    H <--> DB
    K1 <--> DB
    J <--> LLM
    L <--> LLM
    K3 <--> GH
    M --> S
    G1 --> S
```

---

## 2. Step 1 — Frontend sends the question

```mermaid
flowchart TD
    A(["Enter pressed"]) --> B{"Text empty or<br/>already streaming?"}
    B -- yes --> Z(["Ignore"])
    B -- no --> C["ensureConversation():<br/>create one if none is open<br/>(title = first 48 chars)"]
    C --> D["Append user message +<br/>assistant placeholder<br/>(status: streaming)"]
    D --> E["Clerk getToken()<br/>(auto-refreshed JWT)"]
    E --> F[/"fetch POST /llm<br/>with AbortController"/]
    F --> G{"HTTP status"}
    G -- "200" --> H["Read body stream"]
    G -- "401" --> I["Auth bridge:<br/>getToken skipCache"]
    I --> J{"Fresh token?"}
    J -- yes --> F2["Retry once"]
    F2 --> G2{"401 again?"}
    G2 -- yes --> K[/"Error: couldn't verify<br/>sign-in with the server"/]
    G2 -- no --> H
    J -- no --> L(["Redirect to /sign-in"])
    G -- "5xx / network" --> M[/"Error + Retry button"/]
    H --> N(["Go to step 6:<br/>render events"])
```

Files: `features/chat/hooks/use-chat-stream.ts`, `features/chat/api/stream-chat.ts`, `lib/api-client.ts`, `lib/auth-token.ts`, `features/auth/components/auth-session-bridge.tsx`.

---

## 3. Step 2 — Backend checks who is asking

```mermaid
flowchart TD
    A[/"Request with<br/>Authorization header"/] --> B{"Header present<br/>and Bearer?"}
    B -- no --> X1[/"401 Missing / malformed"/]
    B -- yes --> C["Clerk SDK verifies:<br/>RS256 signature (cached JWKS)<br/>exp/nbf (5 s leeway)<br/>azp in allowed origins"]
    C --> D{"Signed in?"}
    D -- no --> X2[/"401 Invalid or expired"/]
    D -- yes --> E{"iss == CLERK_ISSUER?"}
    E -- no --> X2
    E -- yes --> F["clerk_user_id = token sub"]
    F --> G[("users.find_one<br/>clerk_user_id")]
    G --> H{"Found?"}
    H -- no --> I[["Clerk API: profile<br/>(first request only)"]]
    I --> J[("Atomic upsert<br/>unique index")]
    H -- yes --> K["Refresh last_login<br/>(at most every 15 min)"]
    J --> L{"is_active?"}
    K --> L
    L -- no --> X3[/"403 Deactivated"/]
    L -- yes --> M(["User object →<br/>endpoint runs"])
    G -. "Mongo down" .-> X4[/"503 Sign-in service<br/>unavailable"/]
```

Files: `app/routes/dependencies.py`, `infra/auth/clerk_token_verifier.py`, `app/auth/service.py`, `infra/db/user_repository.py`.

---

## 4. Step 3 — The router picks a route

```mermaid
flowchart TD
    A[/"Question"/] --> B["ROUTER_PROMPT +<br/>structured output RouteDecision"]
    B --> C[["Mistral"]]
    C -- "fails after 2 tries" --> D[["Groq"]]
    D -- "fails after 2 tries" --> E[["Gemini"]]
    E -- fails --> F["Default route: RAG"]
    C & D & E -- ok --> G{"route"}
    G -- "About Virat Kohli" --> R1(["RAG"])
    G -- "Weather / INR→USD" --> R2(["TOOL"])
    G -- "User's own GitHub" --> R3(["GITHUB"])
    G -- "Anything else" --> R4(["DIRECT"])
    F --> R1
```

The first SSE event is always `route` with the reason, so the UI can show a "Documents / Tool / GitHub / General" badge.

Files: `app/ai/router.py`, `app/ai/prompts/routes.py`, `app/ai/constants.py`.

---

## 5. Step 4 — Each route

### 5.1 RAG (documents)

```mermaid
flowchart TD
    A(["RAG route"]) --> B[/"SSE retrieval_start"/]
    B --> C["Embed question<br/>gemini-embedding-001"]
    C --> D[("$vectorSearch<br/>chunks_vector_index<br/>top 5")]
    D -- "error after retries" --> X[/"SSE error:<br/>Could not search the documents"/]
    D --> E[/"SSE retrieval_end (count)"/]
    E --> F{"Any chunks?"}
    F -- no --> G[/"SSE token: no relevant info<br/>+ done"/]
    F -- yes --> H["build_context():<br/>[Source: file, page] text…<br/>+ de-duplicated sources"]
    H --> I["RAG_CHAT_PROMPT<br/>(context + question)"]
    I --> J["rag_agent streams answer"]
    J --> K[/"SSE model, token…"/]
    K --> L[/"SSE sources, done"/]
    L --> Z(["End"])
```

### 5.2 TOOL (weather, currency)

```mermaid
sequenceDiagram
    autonumber
    participant CS as ChatService
    participant AG as tool_agent
    participant LLM as LLM
    participant WT as weather_tool
    participant OM as Open-Meteo
    participant FE as Frontend

    CS->>AG: astream_events(question)
    AG->>LLM: question + tool schemas
    LLM-->>AG: call weather_tool(city="Chennai")
    AG-->>CS: on_tool_start
    CS-->>FE: SSE tool_start
    AG->>WT: run
    WT->>OM: geocode city
    OM-->>WT: lat, lon
    WT->>OM: current forecast
    OM-->>WT: temperature, humidity, wind
    WT-->>AG: readable text
    AG-->>CS: on_tool_end
    CS-->>FE: SSE tool_end (output, first 500 chars)
    AG->>LLM: tool result
    loop answer
        LLM-->>CS: token
        CS-->>FE: SSE token
    end
    CS-->>FE: SSE done
```

### 5.3 GITHUB (MCP connector)

```mermaid
flowchart TD
    A(["GITHUB route"]) --> B["connector_service.get_tools(user, github)"]
    B --> C[("mcp_connections<br/>user_id + github")]
    C --> D{"Connected?"}
    D -- no --> E[/"SSE token: connect GitHub<br/>on the Connectors page + done"/]
    D -- yes --> F["Decrypt token<br/>(refresh if expiring)"]
    F --> G[["GitHub MCP server:<br/>initialize + tools/list"]]
    G -- "401/403" --> H["Mark needs_reauth"] --> E
    G -- ok --> I["Keep read-only tools,<br/>rename github__*"]
    I --> J["build_agent(github_agent,<br/>prompt + 'signed in as login')"]
    J --> K["Agent calls tools, e.g.<br/>github__get_me,<br/>github__search_repositories"]
    K --> L[/"SSE tool_start / tool_end"/]
    L --> M[/"SSE model, token…, done"/]
    M --> Z(["End"])
```

The full MCP story (OAuth, encryption, why each file exists) is in [connectors.md](connectors.md).

### 5.4 DIRECT (general knowledge)

```mermaid
flowchart LR
    A(["DIRECT route"]) --> B["direct_agent<br/>DIRECT_SYSTEM_PROMPT"] --> C[/"SSE model, token…"/] --> D[/"SSE done"/] --> E(["End"])
```

---

## 6. Step 5 — Streaming the answer (backend)

`_stream_agent` turns LangChain's event stream into our SSE events.

```mermaid
flowchart TD
    A(["agent.astream_events v2"]) --> B{"event kind"}
    B -- on_tool_start --> C[/"SSE tool_start<br/>tool, input"/]
    B -- on_tool_end --> D[/"SSE tool_end<br/>tool, output (capped)"/]
    B -- on_chat_model_stream --> E{"chunk has text?"}
    E -- no --> A
    E -- yes --> F{"new model run?"}
    F -- yes --> G[/"SSE model<br/>provider/model"/]
    G --> H[/"SSE token"/]
    F -- no --> H
    C & D & H --> A
    A -- "stream ends" --> I{"any text streamed?"}
    I -- no --> J[/"SSE error: empty answer"/]
    I -- yes --> K(["Handler sends done"])
    A -- "all models failed" --> L[/"SSE error: all providers failed"/]
```

**Fallback during streaming:** if Groq fails, `ModelFallbackMiddleware` switches to Gemini mid-request; the new run ID makes the backend emit a fresh `model` event, so the UI shows which model really answered.

---

## 7. Step 6 — Rendering events (frontend)

```mermaid
stateDiagram-v2
    [*] --> routing: message created
    routing --> retrieving: retrieval_start
    routing --> using_tool: tool_start
    routing --> writing: token
    retrieving --> writing: retrieval_end then token
    using_tool --> using_tool: tool_end / next tool_start
    using_tool --> writing: token
    writing --> writing: token (append text)
    writing --> done: done
    writing --> done: sources then done
    routing --> error: error
    retrieving --> error: error
    using_tool --> error: error
    writing --> error: error
    writing --> done: user presses Stop
    done --> [*]
    error --> [*]: Retry re-sends the same question
```

`applyStreamEvent` (pure function) updates one assistant message per event; React re-renders the message list, which keeps scrolled to the bottom unless the user scrolled up to read.

---

## 8. Sign-in and sign-out flow

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant P as proxy.ts
    participant SI as /sign-in (our page)
    participant CK as Clerk
    participant CH as /chat
    participant BR as Auth bridge

    U->>P: open /chat (signed out)
    P->>CK: auth.protect()
    CK-->>U: redirect /sign-in?redirect_url=/chat
    U->>SI: email / Google
    SI->>CK: sign in
    CK-->>U: session cookie, redirect /chat
    U->>CH: page loads
    CH->>CH: GET /me with token → profile in sidebar
    Note over U,BR: Later — sign out
    U->>CK: Sign out (user menu)
    CK-->>U: navigate to /sign-in
    BR->>BR: userId became null → clear TanStack cache<br/>(no second redirect from auth pages)
```

---

## 9. Where each step lives

| Step | Frontend | Backend |
|---|---|---|
| Send question | `features/chat/hooks/use-chat-stream.ts` | `app/routes/chat.py` |
| Token | `lib/api-client.ts`, `lib/auth-token.ts` | `app/routes/dependencies.py`, `infra/auth/clerk_token_verifier.py` |
| User profile | `features/auth/hooks/use-current-user.ts` | `app/auth/service.py`, `infra/db/user_repository.py` |
| Routing | — | `app/ai/router.py` |
| Route handlers | — | `app/ai/chat.py` |
| Agents | — | `app/ai/agent.py` |
| RAG | — | `app/ai/rag/pipeline.py`, `infra/db/mongo_vector_store.py` |
| Tools | — | `app/ai/tools/tool.py` |
| GitHub | `features/connectors/*` | `app/connectors/service.py`, `infra/mcp/client.py` |
| Stream parsing / UI | `features/chat/api/stream-chat.ts`, `hooks/apply-stream-event.ts` | — |
