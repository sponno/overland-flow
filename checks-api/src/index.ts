import { env } from "cloudflare:workers";

// The team's hand checks for check.html, so everyone checks into one place.
//   GET  /checks?tile=T              every answer, and who has which line open
//   PUT  /checks?tile=T&fence=F      save one line's answer (last save wins; all kept in the log)
//   POST /looking?tile=T&fence=F     "I have this line open"
//   GET  /checks.csv?tile=T          the answers as the CSV classify_fences.py reads

const ITEM = ["n", "parcel_a", "parcel_b", "street_front", "length_m", "lat", "lon"];
const ANSWER = ["structure", "along", "height", "water", "seen", "sure", "note"];
const CSV = ["n", "fence", "parcel_a", "parcel_b", "street_front", "length_m", "lat", "lon", ...ANSWER, "by", "at"];
const LOOKING = 3 * 60 * 1000; // a line stays "open" this long after someone last opened it
const ORIGINS = /^(https:\/\/sponno\.github\.io|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?)$/;

type Row = Record<string, unknown>;

function headers(request: Request, type = "application/json"): HeadersInit {
	const origin = request.headers.get("Origin") ?? "";
	return {
		"Content-Type": type,
		"Cache-Control": "no-store",
		...(ORIGINS.test(origin) ? { "Access-Control-Allow-Origin": origin, Vary: "Origin" } : {}),
	};
}

const json = (request: Request, body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: headers(request) });

// Short text only: these are chip values, a name and a note.
const text = (v: unknown, max: number) => (v == null || v === "" ? null : String(v).slice(0, max));
const number = (v: unknown) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

// The page and the CSV call the checker "by"; it's "who" in the database.
const out = ({ who, ...row }: Row) => ({ ...row, by: who ?? "" });

async function list(tile: string) {
	const since = new Date(Date.now() - LOOKING).toISOString();
	const [answers, looking] = await env.DB.batch([
		env.DB.prepare("SELECT * FROM answers WHERE tile = ? ORDER BY n").bind(tile),
		env.DB.prepare("SELECT fence, who, at FROM looking WHERE tile = ? AND at > ?").bind(tile, since),
	]);
	return { answers: answers.results.map(out), looking: looking.results.map(out), now: new Date().toISOString() };
}

async function save(tile: string, fence: number, body: Row) {
	const at = new Date().toISOString();
	const item = ITEM.map((k) => number(body[k]));
	const answer = ANSWER.map((k) => text(Array.isArray(body[k]) ? (body[k] as unknown[]).join("+") : body[k], k === "note" ? 2000 : 40));
	const who = text(body.by, 60);
	await env.DB.batch([
		env.DB.prepare(
			`INSERT INTO answers (tile, fence, ${ITEM.join(", ")}, ${ANSWER.join(", ")}, who, at)
			 VALUES (?, ?, ${[...ITEM, ...ANSWER].map(() => "?").join(", ")}, ?, ?)
			 ON CONFLICT (tile, fence) DO UPDATE SET ${[...ITEM, ...ANSWER, "who", "at"].map((k) => `${k} = excluded.${k}`).join(", ")}`,
		).bind(tile, fence, ...item, ...answer, who, at),
		env.DB.prepare(`INSERT INTO log (tile, fence, ${ANSWER.join(", ")}, who, at) VALUES (?, ?, ${ANSWER.map(() => "?").join(", ")}, ?, ?)`)
			.bind(tile, fence, ...answer, who, at),
	]);
	return out(Object.fromEntries([["tile", tile], ["fence", fence], ...ITEM.map((k, i) => [k, item[i]]), ...ANSWER.map((k, i) => [k, answer[i]]), ["who", who], ["at", at]]));
}

function csv(rows: Row[]) {
	const cell = (v: unknown) => (/[",\n]/.test(String(v ?? "")) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? ""));
	return [CSV.join(","), ...rows.map((r) => CSV.map((k) => cell(r[k])).join(","))].join("\n") + "\n";
}

export default {
	async fetch(request) {
		const url = new URL(request.url);
		if (request.method === "OPTIONS") {
			return new Response(null, {
				headers: { ...headers(request), "Access-Control-Allow-Methods": "GET, PUT, POST", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400" },
			});
		}
		const tile = url.searchParams.get("tile") ?? "";
		if (!/^[A-Za-z0-9_-]{1,40}$/.test(tile)) return json(request, { error: "tile?" }, 400);
		const fence = Number(url.searchParams.get("fence"));
		const hasFence = Number.isInteger(fence) && fence >= 0 && fence < 1e6;

		if (request.method === "GET" && url.pathname === "/checks") return json(request, await list(tile));
		if (request.method === "GET" && url.pathname === "/checks.csv") {
			const rows = await env.DB.prepare("SELECT * FROM answers WHERE tile = ? AND structure IS NOT NULL ORDER BY n").bind(tile).all();
			return new Response(csv(rows.results.map(out)), { headers: headers(request, "text/csv; charset=utf-8") });
		}
		if (request.method === "PUT" && url.pathname === "/checks" && hasFence) {
			return json(request, await save(tile, fence, await request.json<Row>()));
		}
		if (request.method === "POST" && url.pathname === "/looking" && hasFence) {
			const { by } = await request.json<Row>();
			await env.DB.prepare("INSERT INTO looking (tile, fence, who, at) VALUES (?, ?, ?, ?) ON CONFLICT (tile, fence) DO UPDATE SET who = excluded.who, at = excluded.at")
				.bind(tile, fence, text(by, 60), new Date().toISOString()).run();
			return json(request, { ok: true });
		}
		return json(request, { error: "not found" }, 404);
	},
} satisfies ExportedHandler;
