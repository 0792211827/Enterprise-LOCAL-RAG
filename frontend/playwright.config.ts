import { defineConfig } from "@playwright/test";

// The browser tests drive production builds of the app with the API stubbed at the HTTP boundary
// (see e2e/support/api-stub.ts), so they need no live services. The API address is baked into the
// build, so the build points at a made-up host that every test intercepts.
//
// Two builds, because authentication is chosen at build time: auth off (the default surface) on
// :3101 and OIDC on :3102. `npm run test:e2e` builds both and runs everything.
const API = "http://api.stub.test";
const IDP = "http://idp.stub.test";

const server = (port: number, distDir: string, env: Record<string, string>) => ({
  command: `next build && next start -p ${port}`,
  url: `http://localhost:${port}`,
  reuseExistingServer: !process.env.CI,
  timeout: 300_000,
  env: { NEXT_DIST_DIR: distDir, NEXT_PUBLIC_API_URL: API, NEXT_TELEMETRY_DISABLED: "1", ...env },
});

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: true,
  reporter: [["list"]],
  use: { trace: "retain-on-failure" },
  projects: [
    { name: "auth-off", testMatch: /.*\.spec\.ts/, testIgnore: /\.oidc\.spec\.ts/, use: { baseURL: "http://localhost:3101" } },
    { name: "oidc", testMatch: /\.oidc\.spec\.ts/, use: { baseURL: "http://localhost:3102" } },
  ],
  webServer: [
    server(3101, ".next-e2e", { NEXT_PUBLIC_AUTH_MODE: "none" }),
    server(3102, ".next-e2e-oidc", {
      NEXT_PUBLIC_AUTH_MODE: "oidc",
      NEXT_PUBLIC_OIDC_ISSUER: IDP,
      NEXT_PUBLIC_OIDC_CLIENT_ID: "rag-ui",
    }),
  ],
});
