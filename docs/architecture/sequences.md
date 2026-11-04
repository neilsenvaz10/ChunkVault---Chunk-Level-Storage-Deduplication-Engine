# Sequence Diagrams (the implemented upload and restore behaviour)

## Upload

```mermaid
sequenceDiagram
    actor U as User
    participant UI as React client
    participant API as FastAPI
    participant O as Upload orchestrator
    participant C as CDC engine
    participant H as SHA-256
    participant D as Dedup manager
    participant DB as SQLite
    participant S as Chunk store

    U->>UI: choose file
    UI->>API: POST /api/files (multipart)
    API->>O: upload(filename, stream)
    O->>DB: T1: insert operation(running), file(uploading)
    O->>O: stream upload to staging/
    loop each chunk
        O->>C: next chunk (boundary via rolling fingerprint)
        C-->>O: chunk bytes
        O->>H: sha256(chunk)
        H-->>O: hash
        O->>D: decide(hash, size)
        D->>DB: read chunks row
        D-->>O: NEW / DUPLICATE / REPAIR
        alt NEW or REPAIR
            O->>DB: ensure chunk row (pending)
            O->>S: stage(hash, bytes) + fsync
            O->>S: publish (atomic rename)
        end
    end
    O->>DB: T2 (one commit): chunks stored, file_chunks rows, file complete, operation succeeded
    O-->>API: UploadResult
    API-->>UI: 201 UploadResponse
    Note over O,DB: Any failure: operation/file marked failed, staging removed, orphans left for reconciliation
```

## Restore

```mermaid
sequenceDiagram
    actor U as User
    participant UI as React client
    participant API as FastAPI
    participant R as Restoration engine
    participant DB as SQLite
    participant S as Chunk store
    participant H as SHA-256

    U->>UI: click restore
    UI->>API: GET /api/files/{id}/download
    API->>R: restore(file_id)
    R->>DB: file (must be complete) + file_chunks ordered by chunk_index
    alt not found / not complete
        R-->>API: FileNotRestorableError
        API-->>UI: 404 error envelope
    else
        loop each chunk in order
            R->>S: read(hash)
            S-->>R: bytes (or ChunkNotFoundError)
            R->>H: verify(bytes, hash)
            R-->>API: yield bytes
            API-->>UI: stream bytes
        end
        R->>H: compare whole-file sha256 with files.sha256
    end
    Note over R,API: A mid-stream failure aborts the stream; wrong data is never returned as success
```
