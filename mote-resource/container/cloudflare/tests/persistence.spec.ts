import { env } from 'cloudflare:workers'
import { runInDurableObject } from 'cloudflare:test'
import { describe, expect, it } from 'vitest'

import { Commit } from '../src/index.ts'
import type { AgentDurableObject } from '../src/index.ts'

type State = Readonly<{
    revision: number
    runId: string
    value: string
}>

const access = {
    encode: (state: State): Uint8Array => new TextEncoder().encode(JSON.stringify(state)),
    revision: (state: State): number => state.revision,
    runId: (state: State): string => state.runId,
}

describe('Cloudflare Commit', () => {
    it('persists transitions in real Durable Object storage with exact revision CAS', async () => {
        const stub = env.AGENT_OBJECTS.get(env.AGENT_OBJECTS.idFromName('commit-cas'))

        await runInDurableObject(stub, async (_instance: AgentDurableObject, state) => {
            const commit = Commit<State>(state.storage, access)
            const zero = { revision: 0, runId: 'run', value: 'zero' }
            const one = { revision: 1, runId: 'run', value: 'one' }

            await expect(
                commit({
                    candidateState: zero,
                    previousState: null,
                    scope: [],
                }),
            ).resolves.toBe(zero)
            await expect(
                commit({
                    candidateState: one,
                    previousState: zero,
                    scope: [],
                }),
            ).resolves.toBe(one)
            expect(
                state.storage.sql
                    .exec<{ revision: number }>(
                        'SELECT revision FROM mote_graph_state_v1 WHERE scope = ?',
                        '[]',
                    )
                    .one().revision,
            ).toBe(1)
            await expect(
                commit({
                    candidateState: one,
                    previousState: zero,
                    scope: [],
                }),
            ).rejects.toThrow('stale')
            await expect(
                commit({
                    candidateState: zero,
                    previousState: null,
                    scope: [],
                }),
            ).rejects.toThrow('already exists')

            const initial = { revision: 0, runId: 'initial', value: 'initial' }
            const mismatchedRun = { revision: 1, runId: 'other', value: 'run' }
            await expect(
                commit({
                    candidateState: initial,
                    previousState: null,
                    scope: ['identity'],
                }),
            ).resolves.toBe(initial)
            await expect(
                commit({
                    candidateState: mismatchedRun,
                    previousState: initial,
                    scope: ['identity'],
                }),
            ).rejects.toThrow('run identity')

            const mismatchedRevision = { revision: 2, runId: 'initial', value: 'revision' }
            await expect(
                commit({
                    candidateState: mismatchedRevision,
                    previousState: initial,
                    scope: ['identity'],
                }),
            ).rejects.toThrow('advance exactly one')

            await expect(
                commit({
                    candidateState: { revision: 1, runId: 'new', value: 'invalid' },
                    previousState: null,
                    scope: ['invalid-initial-revision'],
                }),
            ).rejects.toThrow('revision zero')
        })
    })

    it('validates scope parts and encoded payloads', async () => {
        const stub = env.AGENT_OBJECTS.get(env.AGENT_OBJECTS.idFromName('commit-validation'))

        await runInDurableObject(stub, async (_instance: AgentDurableObject, state) => {
            const zero = { revision: 0, runId: 'run', value: 'zero' }
            const commit = Commit<State>(state.storage, access)

            await expect(
                commit({
                    candidateState: zero,
                    previousState: null,
                    scope: ['valid-scope'],
                }),
            ).resolves.toBe(zero)
            await expect(
                commit({
                    candidateState: { ...zero, value: 'empty-part' },
                    previousState: null,
                    scope: [''],
                }),
            ).rejects.toThrow('non-empty strings')
            await expect(
                commit({
                    candidateState: { ...zero, value: 'non-string-part' },
                    previousState: null,
                    scope: [1 as unknown as string],
                }),
            ).rejects.toThrow('non-empty strings')

            const invalidAccess = {
                ...access,
                encode: (_state: State): Uint8Array => 'not-a-byte-array' as unknown as Uint8Array,
            }
            const invalidEncoder = Commit<State>(state.storage, invalidAccess)
            await expect(
                invalidEncoder({
                    candidateState: zero,
                    previousState: null,
                    scope: ['invalid-encoding'],
                }),
            ).rejects.toThrow('Uint8Array')
        })
    })

    it('rejects missing, duplicated, and non-updating durable rows', async () => {
        const stub = env.AGENT_OBJECTS.get(env.AGENT_OBJECTS.idFromName('commit-row-shapes'))

        await runInDurableObject(stub, async (_instance: AgentDurableObject, state) => {
            const commit = Commit<State>(state.storage, access)
            const previous = { revision: 0, runId: 'run', value: 'previous' }
            const candidate = { revision: 1, runId: 'run', value: 'candidate' }

            await expect(
                commit({
                    candidateState: candidate,
                    previousState: previous,
                    scope: ['missing'],
                }),
            ).rejects.toThrow('missing or duplicated')

            state.storage.sql.exec('DROP TABLE mote_graph_state_v1')
            state.storage.sql.exec(`
                CREATE TABLE mote_graph_state_v1 (
                    scope TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload BLOB NOT NULL
                ) STRICT
            `)
            state.storage.sql.exec(
                'INSERT INTO mote_graph_state_v1 (scope, run_id, revision, payload) VALUES (?, ?, ?, ?)',
                '["duplicated"]',
                'run',
                0,
                new ArrayBuffer(0),
            )
            state.storage.sql.exec(
                'INSERT INTO mote_graph_state_v1 (scope, run_id, revision, payload) VALUES (?, ?, ?, ?)',
                '["duplicated"]',
                'run',
                0,
                new ArrayBuffer(0),
            )
            await expect(
                commit({
                    candidateState: candidate,
                    previousState: previous,
                    scope: ['duplicated'],
                }),
            ).rejects.toThrow('missing or duplicated')

            state.storage.sql.exec('DROP TABLE mote_graph_state_v1')
            state.storage.sql.exec(`
                CREATE TABLE mote_graph_state_v1 (
                    scope TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload BLOB NOT NULL
                ) STRICT
            `)
            state.storage.sql.exec(
                'INSERT INTO mote_graph_state_v1 (scope, run_id, revision, payload) VALUES (?, ?, ?, ?)',
                '["ignored-update"]',
                'run',
                0,
                new ArrayBuffer(0),
            )
            state.storage.sql.exec(`
                CREATE TRIGGER ignore_mote_graph_update
                BEFORE UPDATE ON mote_graph_state_v1
                BEGIN
                    SELECT RAISE(IGNORE);
                END
            `)
            await expect(
                commit({
                    candidateState: candidate,
                    previousState: previous,
                    scope: ['ignored-update'],
                }),
            ).rejects.toThrow('compare-and-swap')
        })
    })

    it('uses Cloudflare transactionSync rollback semantics', async () => {
        const stub = env.AGENT_OBJECTS.get(env.AGENT_OBJECTS.idFromName('transaction-rollback'))

        await runInDurableObject(stub, (_instance: AgentDurableObject, state) => {
            state.storage.sql.exec('CREATE TABLE rollback_test (value TEXT NOT NULL) STRICT')
            expect(() =>
                state.storage.transactionSync(() => {
                    state.storage.sql.exec(
                        'INSERT INTO rollback_test (value) VALUES (?)',
                        'uncommitted',
                    )
                    throw new Error('rollback')
                }),
            ).toThrow('rollback')
            expect(
                state.storage.sql
                    .exec<{ count: number }>('SELECT COUNT(*) AS count FROM rollback_test')
                    .one().count,
            ).toBe(0)
        })
    })

    it('exports the container and its persistence composition', async () => {
        expect(Object.keys(await import('../src/index.ts'))).toEqual([
            'Commit',
            'AgentDurableObject',
            'default',
        ])
    })
})
