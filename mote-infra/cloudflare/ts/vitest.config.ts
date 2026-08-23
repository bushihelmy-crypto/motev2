import { cloudflareTest } from '@cloudflare/vitest-plugin'
import { defineConfig } from 'vitest/config'

export default defineConfig({
    plugins: [
        cloudflareTest({
            wrangler: {
                configPath: './wrangler.jsonc',
            },
        }),
    ],
    test: {
        coverage: {
            exclude: ['src/worker-configuration.d.ts'],
            include: ['src/**/*.ts'],
            provider: 'istanbul',
            reporter: ['text', 'json', 'html'],
            thresholds: {
                branches: 100,
                functions: 100,
                lines: 100,
                statements: 100,
            },
        },
    },
})
