import { DurableObject } from 'cloudflare:workers'

// The Cloudflare Durable Object is the composition root for both hosting and
// object-local persistence.  `Commit` stays implemented in its focused module,
// but is shipped from this same Worker bundle so it can be constructed with
// this object's `ctx.storage` when the Agent protocol is wired.
export { Commit } from './persistence.ts'

/**
 * Cloudflare container for one logical Mote Agent.
 *
 * The Agent protocol and persistent schema will be added with their first Kernel consumer and
 * conformance contract. Until then, the class deliberately exposes no provisional operations.
 */
export class AgentDurableObject extends DurableObject<Env> {
    override fetch(): Response {
        return new Response(null, { status: 501 })
    }
}

/**
 * Worker entry point reserved for future product-owned routing.
 */
export default {
    fetch(): Response {
        return new Response(null, { status: 404 })
    },
} satisfies ExportedHandler<Env>
