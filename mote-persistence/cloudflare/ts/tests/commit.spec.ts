import { env } from 'cloudflare:workers'
import { runInDurableObject } from 'cloudflare:test'
import { describe, expect, it } from 'vitest'

import { Commit } from '../src/index.ts'
import type { PersistenceTestDurableObject } from './worker.ts'

type State = Readonly<{
    revision: number
    runId: string
    value: string
}>

const access = {
    encode: (state: State): Uint8Array =>
        new TextEncoder().encode(JSON.stringify(state)),
    revision: (state: State): number => state.revision,
    runId: (state: State): string => state.runId,
}

describe('Cloudflare Commit', () => {
    it('persists transitions in real Durable Object storage with exact revision CAS', async () => {
        const stub = env.PERSISTENCE_TEST_OBJECTS.get(
            env.PERSISTENCE_TEST_OBJECTS.idFromName('commit-cas'),
        )

        await runInDurableObject(
            stub,
            async (_instance: PersistenceTestDurableObject, state) => {
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
            },
        )
    })

    it('uses Cloudflare transactionSync rollback semantics', async () => {
        const stub = env.PERSISTENCE_TEST_OBJECTS.get(
            env.PERSISTENCE_TEST_OBJECTS.idFromName('transaction-rollback'),
        )

        await runInDurableObject(
            stub,
            (_instance: PersistenceTestDurableObject, state) => {
                state.storage.sql.exec(
                    'CREATE TABLE rollback_test (value TEXT NOT NULL) STRICT',
                )
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
                        .exec<{ count: number }>(
                            'SELECT COUNT(*) AS count FROM rollback_test',
                        )
                        .one().count,
                ).toBe(0)
            },
        )
    })

    it('exports only Commit', async () => {
        expect(Object.keys(await import('../src/index.ts'))).toEqual(['Commit'])
    })
})
