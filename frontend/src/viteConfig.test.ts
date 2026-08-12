import { describe, expect, it } from "vitest";

import config from "../vite.config";

describe("Vite development API proxy", () => {
  it("forwards same-origin /api requests to the backend service", () => {
    expect(config).toMatchObject({
      server: {
        host: "127.0.0.1",
        port: 5174,
        proxy: {
          "/api": {
            target: "http://127.0.0.1:8011",
            changeOrigin: true
          }
        }
      }
    });
  });
});
