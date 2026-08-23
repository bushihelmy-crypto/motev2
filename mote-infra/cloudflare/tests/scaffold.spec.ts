import { env, exports } from 'cloudflare:workers'
import { runInDurableObject } from 'cloudflare:test'
import { describe, expect, it } from 'vitest'

import { AgentDurableObject } from '../src/index.ts'

describe('Cloudflare Worker scaffold', () => {
    it('does not define a product route before its owning contract exists', async () => {
        const response = await exports.default.fetch('https://mote.invalid/')

        expect(response.status).toBe(404)
        expect(await response.text()).toBe('')
    })

    it('exports the Agent Durable Object without a provisional protocol', async () => {
        const id = env.AGENT_OBJECTS.idFromName('agent-without-protocol')
        const stub = env.AGENT_OBJECTS.get(id)

        const response = await stub.fetch('https://mote.invalid/')

        expect(response.status).toBe(501)
        expect(await response.text()).toBe('')
    })

    it('provides SQLite transactions and rolls back failed writes', async () => {
        const id = env.AGENT_OBJECTS.idFromName('transaction-smoke')
        const stub = env.AGENT_OBJECTS.get(id)

        const storedRows = await runInDurableObject(
            stub,
            (_instance: AgentDurableObject, state) => {
                state.storage.sql.exec(`
                    CREATE TABLE transaction_smoke (
                        value TEXT NOT NULL
                    ) STRICT
                `)

                const rollback = new Error('rollback transaction smoke write')
                try {
                    state.storage.transactionSync(() => {
                        state.storage.sql.exec(
                            'INSERT INTO transaction_smoke (value) VALUES (?)',
                            'uncommitted',
                        )
                        throw rollback
                    })
                } catch (error) {
                    if (error !== rollback) {
                        throw error
                    }
                }

                return state.storage.sql
                    .exec<{ count: number }>('SELECT COUNT(*) AS count FROM transaction_smoke')
                    .one().count
            },
        )

        expect(storedRows).toBe(0)
    })
})
