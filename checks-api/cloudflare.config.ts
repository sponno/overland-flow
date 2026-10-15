import { bindings, defineConfig } from "cf/config";
import * as entrypoint from "./src/index.ts" with { type: "cf-worker" };

export default defineConfig({
	worker: {
		name: "overland-checks",
		compatibilityDate: "2026-10-01",
		entrypoint,
		env: {
			DB: bindings.d1({ name: "overland-checks" }),
		},
	},
});
