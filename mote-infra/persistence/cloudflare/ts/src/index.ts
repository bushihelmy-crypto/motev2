type VersionedStateAccess<State> = Readonly<{
    encode(state: State): Uint8Array
    revision(state: State): number
    runId(state: State): string
}>

type Transition<State> = Readonly<{
    candidateState: State
    previousState: State | null
    scope: readonly string[]
}>

type CommitPort<State> = (transition: Transition<State>) => Promise<State>

type StoredRow = {
    revision: number
    run_id: string
}

class ConflictError extends Error {}

const CREATE_TABLE = `
    CREATE TABLE IF NOT EXISTS mote_graph_state_v1 (
        scope TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        payload BLOB NOT NULL
    ) STRICT
`
const SELECT = `
    SELECT run_id, revision
    FROM mote_graph_state_v1
    WHERE scope = ?
`
const INSERT = `
    INSERT INTO mote_graph_state_v1 (scope, run_id, revision, payload)
    VALUES (?, ?, ?, ?)
`
const UPDATE = `
    UPDATE mote_graph_state_v1
    SET run_id = ?, revision = ?, payload = ?
    WHERE scope = ? AND run_id = ? AND revision = ?
`

/**
 * Build the sole public Commit Port using Cloudflare Durable Object storage.
 *
 * The returned callable is structurally compatible with its caller and imports no upper layer.
 */
export function Commit<State>(
    storage: DurableObjectStorage,
    access: VersionedStateAccess<State>,
): CommitPort<State> {
    storage.sql.exec(CREATE_TABLE)

    return async (transition) => {
        const previous = transition.previousState
        const candidate = transition.candidateState
        validateTransition(previous, candidate, access)
        const scope = scopeKey(transition.scope)
        const runId = access.runId(candidate)
        const revision = access.revision(candidate)
        const payload = exactBuffer(access.encode(candidate))

        storage.transactionSync(() => {
            const rows = storage.sql.exec<StoredRow>(SELECT, scope).toArray()
            if (previous === null) {
                if (rows.length !== 0) {
                    throw new ConflictError('state scope already exists')
                }
                storage.sql.exec(INSERT, scope, runId, revision, payload)
                return
            }

            if (rows.length !== 1) {
                throw new ConflictError('state scope is missing or duplicated')
            }
            const previousRunId = access.runId(previous)
            const previousRevision = access.revision(previous)
            const row = rows[0]
            if (
                row?.run_id !== previousRunId ||
                row.revision !== previousRevision
            ) {
                throw new ConflictError(
                    'transition is based on a stale durable revision',
                )
            }
            const updated = storage.sql.exec(
                UPDATE,
                runId,
                revision,
                payload,
                scope,
                previousRunId,
                previousRevision,
            )
            if (updated.rowsWritten !== 1) {
                throw new ConflictError(
                    'transition lost its durable compare-and-swap',
                )
            }
        })

        return candidate
    }
}

function exactBuffer(bytes: Uint8Array): ArrayBuffer {
    if (!(bytes instanceof Uint8Array)) {
        throw new TypeError('Commit encode must return Uint8Array')
    }
    return bytes.buffer.slice(
        bytes.byteOffset,
        bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer
}

function scopeKey(scope: readonly string[]): string {
    if (scope.some((part) => typeof part !== 'string' || part.length === 0)) {
        throw new TypeError('state scope parts must be non-empty strings')
    }
    return JSON.stringify(scope)
}

function validateTransition<State>(
    previous: State | null,
    candidate: State,
    access: VersionedStateAccess<State>,
): void {
    const candidateRevision = access.revision(candidate)
    if (previous === null) {
        if (candidateRevision !== 0) {
            throw new ConflictError('initial state must use revision zero')
        }
        return
    }
    if (access.runId(candidate) !== access.runId(previous)) {
        throw new ConflictError('a transition cannot replace its run identity')
    }
    if (candidateRevision !== access.revision(previous) + 1) {
        throw new ConflictError(
            'a transition must advance exactly one revision',
        )
    }
}
