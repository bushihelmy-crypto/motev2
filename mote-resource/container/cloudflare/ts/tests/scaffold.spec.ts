import { env, exports } from 'cloudflare:workers'
import { describe, expect, it } from 'vitest'

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
})
