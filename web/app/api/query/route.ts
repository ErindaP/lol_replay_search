import { jsonResponse, runPython } from "@/lib/project";

export const dynamic = "force-dynamic";

type QueryBody = {
  text?: string;
  mode?: "auto" | "semantic" | "precise";
  topK?: number;
  useLlmParser?: boolean;
  windowSeconds?: number;
  minKills?: number | null;
  maxKills?: number | null;
  requiresDragon?: boolean;
  requiresBaron?: boolean;
  sortBy?: string;
  ascending?: boolean;
  noKills?: boolean;
};

export async function POST(request: Request) {
  const body = (await request.json()) as QueryBody;
  const args = [
    "-m",
    "lol_search.web_api",
    "query",
    "--mode",
    body.mode || "auto",
    "--text",
    body.text || "",
    "--top-k",
    String(Math.max(1, Math.min(50, Number(body.topK || 8)))),
    "--window-seconds",
    String(Math.max(1, Number(body.windowSeconds || 30))),
    "--sort-by",
    body.sortBy || "gold_abs_swing",
  ];

  if (body.useLlmParser) args.push("--use-llm-parser");
  if (body.requiresDragon) args.push("--requires-dragon");
  if (body.requiresBaron) args.push("--requires-baron");
  if (body.ascending) args.push("--ascending");
  if (body.noKills) args.push("--no-kills");
  if (typeof body.minKills === "number") args.push("--min-kills", String(body.minKills));
  if (typeof body.maxKills === "number") args.push("--max-kills", String(body.maxKills));

  const result = await runPython(args, 90_000);
  if (!result.ok || !result.json) {
    return jsonResponse(
      {
        status: "error",
        message: "Query failed",
        stdout: result.stdout,
        stderr: result.stderr,
        code: result.code,
      },
      500,
    );
  }

  return jsonResponse(result.json);
}
