import { DurableObject } from 'cloudflare:workers'

export class PersistenceTestDurableObject extends DurableObject<Env> {
    override fetch(): Response {
        return new Response(null, { status: 404 })
    }
}

export default {
    fetch(): Response {
        return new Response(null, { status: 404 })
    },
} satisfies ExportedHandler<Env>
