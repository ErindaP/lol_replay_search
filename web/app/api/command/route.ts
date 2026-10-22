import { jsonResponse, runPython } from "@/lib/project";

export const dynamic = "force-dynamic";

type CommandBody = {
  action?: string;
  payload?: Record<string, unknown>;
};

const longRunningActions = new Set([
  "ingest-summoner",
  "build-windows",
  "build-index",
  "export-clip",
  "export-clips-from-query",
  "replay-watch-lcu",
  "query-open-result",
]);

function asBool(value: unknown): boolean {
  return value === true || value === "true" || value === 1;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function pushFlag(args: string[], name: string, enabled: unknown) {
  if (asBool(enabled)) args.push(name);
}

function pushValue(args: string[], name: string, value: unknown) {
  if (value !== undefined && value !== null && value !== "") {
    args.push(name, String(value));
  }
}

function buildArgs(action: string, payload: Record<string, unknown>): string[] {
  const args = ["-m", "lol_search.cli", action];

  if (action === "ingest-summoner") {
    pushValue(args, "--riot-id", payload.riotId);
    pushValue(args, "--count", payload.count ?? 20);
    pushValue(args, "--platform", payload.platform);
    pushValue(args, "--region", payload.region);
  }

  if (action === "build-windows") {
    pushValue(args, "--window-seconds", payload.windowSeconds ?? 30);
    pushValue(args, "--step-seconds", payload.stepSeconds ?? 10);
  }

  if (action === "build-index") {
    pushValue(args, "--embedding-model", payload.embeddingModel || "sentence-transformers/all-MiniLM-L6-v2");
    pushFlag(args, "--use-pca", payload.usePca);
    pushValue(args, "--pca-dim", payload.pcaDim ?? 128);
  }

  if (action === "export-clip") {
    pushValue(args, "--match-id", payload.matchId);
    pushValue(args, "--start-ms", payload.startMs);
    pushValue(args, "--end-ms", payload.endMs);
    pushValue(args, "--title", payload.title);
    pushValue(args, "--pad-before-s", payload.padBeforeS ?? 2);
    pushValue(args, "--pad-after-s", payload.padAfterS ?? 2);
    pushValue(args, "--codec", payload.codec || "webm");
    pushFlag(args, "--auto-fetch", payload.autoFetch);
    pushFlag(args, "--auto-load", payload.autoLoad);
  }

  if (action === "export-clips-from-query") {
    pushValue(args, "--text", payload.text);
    pushValue(args, "--top-k", payload.topK ?? 8);
    pushValue(args, "--export-k", payload.exportK ?? 3);
    pushValue(args, "--pad-before-s", payload.padBeforeS ?? 2);
    pushValue(args, "--pad-after-s", payload.padAfterS ?? 2);
    pushValue(args, "--codec", payload.codec || "webm");
    pushValue(args, "--base-url", payload.baseUrl);
    pushFlag(args, "--use-llm-parser", payload.useLlmParser);
    pushFlag(args, "--auto-fetch", payload.autoFetch);
    pushFlag(args, "--auto-load", payload.autoLoad);
    pushFlag(args, "--continue-on-error", true);
  }

  if (action === "replay-watch-lcu") {
    pushValue(args, "--match-id", payload.matchId);
    pushFlag(args, "--no-launch-client", payload.noLaunchClient);
  }

  if (action === "query-open-result") {
    pushValue(args, "--text", payload.text);
    pushValue(args, "--top-k", payload.topK ?? 8);
    pushValue(args, "--result-index", payload.resultIndex ?? 1);
    pushValue(args, "--pad-before-s", payload.padBeforeS ?? 2);
    pushValue(args, "--pad-after-s", payload.padAfterS ?? 2);
    pushValue(args, "--codec", payload.codec || "webm");
    pushValue(args, "--replay-api-timeout-s", payload.replayApiTimeoutS ?? 30);
    pushValue(args, "--title", payload.title);
    pushFlag(args, "--use-llm-parser", payload.useLlmParser);
    pushFlag(args, "--auto-fetch", payload.autoFetch);
    pushFlag(args, "--no-auto-load", payload.noAutoLoad);
    pushFlag(args, "--no-camera", payload.noCamera);
    pushFlag(args, "--no-record", payload.noRecord);
  }

  return args;
}

export async function POST(request: Request) {
  const body = (await request.json()) as CommandBody;
  const action = asString(body.action);
  const payload = body.payload || {};

  if (!longRunningActions.has(action)) {
    return jsonResponse({ status: "error", message: `Unsupported action: ${action}` }, 400);
  }

  const result = await runPython(buildArgs(action, payload), 10 * 60_000);
  return jsonResponse(
    {
      status: result.ok ? "ok" : "error",
      code: result.code,
      stdout: result.stdout,
      stderr: result.stderr,
      json: result.json,
    },
    result.ok ? 200 : 500,
  );
}
