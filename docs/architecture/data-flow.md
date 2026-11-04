# Data Flow and API Communication

## Upload data flow (planned)

```mermaid
flowchart TD
    A[File bytes from client] --> B[Staging file]
    B --> C[Rabin-style CDC<br/>boundaries from rolling fingerprint]
    C --> D[Chunk bytes]
    D --> E[SHA-256 per chunk<br/>= chunk identity]
    B -.-> F[SHA-256 whole file<br/>streaming]
    E --> G{Known stored hash?<br/>metadata lookup}
    G -- yes DUPLICATE --> H[Add file_chunks row only]
    G -- no NEW/REPAIR --> I[Stage + publish bytes to chunk store]
    I --> J[chunks row stored]
    J --> H
    H --> K[(SQLite: files, chunks, file_chunks, operations)]
    I --> L[(chunks/ab/cd/hash)]
    F --> K
```

Metadata flows to SQLite; bytes flow to the filesystem. They are joined only by the SHA-256 hash.

## API communication flow (Phase 1, implemented)

```mermaid
sequenceDiagram
    participant R as React (Vite dev server :5173)
    participant P as Vite proxy
    participant F as FastAPI :8000
    participant DB as SQLite
    R->>P: GET /api/health
    P->>F: forward (or direct call with CORS if VITE_API_BASE_URL set)
    F->>DB: open connection (foreign_keys=ON), SELECT 1, read user_version
    F-->>R: 200 {status, service, version, database}
    Note over R,F: Errors: {"error": {code, message, details}}
```

Startup/shutdown: on startup the app creates `data/{chunks,staging,benchmarks}` and applies the
schema (idempotent, inserts nothing). Connections are opened per request and closed after it.
