"use client";

import clsx from "clsx";
import {
  Activity,
  BarChart3,
  CirclePlay,
  Clapperboard,
  Database,
  Download,
  Film,
  Loader2,
  Play,
  RefreshCcw,
  Search,
  Settings2,
  Sparkles,
  Swords,
  UploadCloud,
  Video,
} from "lucide-react";
import { ElementType, FormEvent, ReactNode, useEffect, useMemo, useState } from "react";

type StatusPayload = {
  raw: { matches: number; timelines: number };
  processed: { windows: number };
  index: Record<string, unknown>;
  clips: ClipAsset[];
  manifest: { clips?: ManifestClip[] };
};

type ClipAsset = {
  file: string;
  href: string;
  size: number;
  mtimeMs: number;
};

type ManifestClip = {
  match_id?: string;
  title?: string;
  start_ms?: number;
  end_ms?: number;
  video?: string;
  local_path?: string;
};

type SearchRow = {
  match_id: string;
  start_ms: number;
  end_ms: number;
  duration_s?: number;
  kills_total?: number;
  kills_blue?: number;
  kills_red?: number;
  dragon_kills?: number;
  baron_kills?: number;
  tower_kills?: number;
  gold_abs_swing?: number;
  score?: number;
  text?: string;
};

type QueryPayload = {
  status: string;
  mode: string;
  filters: Record<string, unknown>;
  results: SearchRow[];
};

type ProjectionPoint = SearchRow & {
  id: string;
  rank: number;
  x: number;
  y: number;
  raw_x?: number;
  raw_y?: number;
  nearest?: { rank: number; similarity: number }[];
};

type ProjectionPayload = {
  status: string;
  points: ProjectionPoint[];
  explained_variance?: number[];
};

type CommandResponse = {
  status: "ok" | "error";
  stdout: string;
  stderr: string;
  json: unknown;
};

const metricCards = [
  { key: "matches", label: "Matches", icon: Database },
  { key: "timelines", label: "Timelines", icon: Activity },
  { key: "windows", label: "Fenetres", icon: BarChart3 },
  { key: "clips", label: "Clips", icon: Film },
];

function formatMs(ms = 0) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const min = Math.floor(total / 60);
  const sec = total % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

function numberValue(value: unknown, fallback = 0) {
  return typeof value === "number" ? value : fallback;
}

function clipNameFromPath(localPath?: string) {
  if (!localPath) return "";
  return localPath.split(/[\\/]/).pop() || "";
}

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "green" | "red" | "amber" | "teal" }) {
  return (
    <span
      className={clsx(
        "inline-flex h-7 items-center rounded border px-2 text-xs font-medium",
        tone === "neutral" && "border-line bg-white text-ink",
        tone === "green" && "border-moss/25 bg-moss/10 text-moss",
        tone === "red" && "border-ember/25 bg-ember/10 text-ember",
        tone === "amber" && "border-brass/25 bg-brass/10 text-brass",
        tone === "teal" && "border-tide/25 bg-tide/10 text-tide",
      )}
    >
      {children}
    </span>
  );
}

function IconButton({
  label,
  icon: Icon,
  onClick,
  disabled,
  active,
}: {
  label: string;
  icon: ElementType;
  onClick?: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "grid h-10 w-10 place-items-center rounded border transition",
        active ? "border-tide bg-tide text-white" : "border-line bg-white text-ink hover:border-tide hover:text-tide",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}

function PrimaryButton({
  children,
  icon: Icon,
  loading,
  disabled,
}: {
  children: ReactNode;
  icon: ElementType;
  loading?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="submit"
      disabled={disabled || loading}
      className="inline-flex h-10 items-center justify-center gap-2 rounded border border-ink bg-ink px-4 text-sm font-semibold text-white transition hover:bg-moss disabled:cursor-not-allowed disabled:opacity-60"
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
      <span>{children}</span>
    </button>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-500">
      <span>{label}</span>
      {children}
    </label>
  );
}

function sameWindow(a: SearchRow | null, b: SearchRow | null) {
  return Boolean(a && b && a.match_id === b.match_id && a.start_ms === b.start_ms && a.end_ms === b.end_ms);
}

function pointTone(point: ProjectionPoint) {
  if (numberValue(point.baron_kills) > 0) return "fill-ember";
  if (numberValue(point.dragon_kills) > 0) return "fill-tide";
  if (numberValue(point.kills_total) > 0) return "fill-brass";
  return "fill-neutral-400";
}

function ProjectionPanel({
  points,
  selected,
  loading,
  onSelect,
}: {
  points: ProjectionPoint[];
  selected: SearchRow | null;
  loading: boolean;
  onSelect: (point: ProjectionPoint) => void;
}) {
  const selectedPoint = points.find((point) => sameWindow(point, selected)) || null;
  const similar = (selectedPoint?.nearest || [])
    .map((item) => {
      const point = points.find((candidate) => candidate.rank === item.rank);
      return point ? { point, similarity: item.similarity } : null;
    })
    .filter(Boolean) as { point: ProjectionPoint; similarity: number }[];

  return (
    <section className="rounded border border-line bg-white p-4 shadow-panel">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">Projection PCA</h2>
          <div className="mt-1 text-xs text-neutral-500">{points.length} fenetres</div>
        </div>
        {loading ? <Loader2 className="h-4 w-4 animate-spin text-tide" /> : <BarChart3 className="h-4 w-4 text-tide" />}
      </div>

      <div className="relative h-72 overflow-hidden rounded border border-line bg-paper">
        <svg viewBox="0 0 100 100" className="h-full w-full" role="img" aria-label="Projection PCA des resultats">
          <line x1="8" y1="50" x2="92" y2="50" className="stroke-neutral-300" strokeWidth="0.35" />
          <line x1="50" y1="8" x2="50" y2="92" className="stroke-neutral-300" strokeWidth="0.35" />
          {points.map((point) => {
            const active = sameWindow(point, selected);
            const x = 8 + point.x * 84;
            const y = 92 - point.y * 84;
            return (
              <g
                key={point.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(point)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onSelect(point);
                }}
                className="cursor-pointer"
              >
                <circle
                  cx={x}
                  cy={y}
                  r={active ? 3.4 : 2.2}
                  className={clsx(pointTone(point), active ? "stroke-ink" : "stroke-white")}
                  strokeWidth={active ? 1.4 : 0.8}
                />
                {point.rank <= 9 ? (
                  <text x={x} y={y + 0.7} textAnchor="middle" className="pointer-events-none fill-white text-[3px] font-bold">
                    {point.rank}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <Pill tone="teal">Dragon</Pill>
        <Pill tone="amber">Kills</Pill>
        <Pill tone="red">Baron</Pill>
        <Pill>Macro</Pill>
      </div>

      {selectedPoint ? (
        <div className="mt-4 grid gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold">#{selectedPoint.rank} voisins</span>
            <Pill tone="teal">{selectedPoint.match_id}</Pill>
          </div>
          {similar.map(({ point, similarity }) => (
            <button
              key={point.id}
              type="button"
              onClick={() => onSelect(point)}
              className="grid gap-1 rounded border border-line bg-paper p-2 text-left hover:border-tide"
            >
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-semibold">
                  #{point.rank} {formatMs(point.start_ms)} - {formatMs(point.end_ms)}
                </span>
                <span className="text-neutral-500">{similarity.toFixed(3)}</span>
              </div>
              <div className="truncate text-xs text-neutral-600">{point.text}</div>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export default function Page() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [queryText, setQueryText] = useState("teamfights autour des dragons");
  const [mode, setMode] = useState<"auto" | "semantic" | "precise">("auto");
  const [topK, setTopK] = useState(8);
  const [useLlmParser, setUseLlmParser] = useState(false);
  const [requiresDragon, setRequiresDragon] = useState(false);
  const [requiresBaron, setRequiresBaron] = useState(false);
  const [noKills, setNoKills] = useState(false);
  const [minKills, setMinKills] = useState(0);
  const [windowSeconds, setWindowSeconds] = useState(30);
  const [results, setResults] = useState<SearchRow[]>([]);
  const [projection, setProjection] = useState<ProjectionPoint[]>([]);
  const [selected, setSelected] = useState<SearchRow | null>(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [projectionLoading, setProjectionLoading] = useState(false);
  const [commandLoading, setCommandLoading] = useState("");
  const [consoleOutput, setConsoleOutput] = useState("");
  const [ingestRiotId, setIngestRiotId] = useState("");
  const [ingestCount, setIngestCount] = useState(10);
  const [stepSeconds, setStepSeconds] = useState(10);
  const [exportK, setExportK] = useState(3);
  const [autoFetch, setAutoFetch] = useState(false);
  const [autoLoad, setAutoLoad] = useState(false);

  async function refreshStatus() {
    setStatusLoading(true);
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      setStatus((await response.json()) as StatusPayload);
    } finally {
      setStatusLoading(false);
    }
  }

  useEffect(() => {
    refreshStatus();
  }, []);

  const metrics = useMemo(() => {
    return {
      matches: status?.raw.matches ?? 0,
      timelines: status?.raw.timelines ?? 0,
      windows: status?.processed.windows ?? 0,
      clips: status?.clips.length ?? 0,
    };
  }, [status]);

  async function runQuery(event: FormEvent) {
    event.preventDefault();
    setQueryLoading(true);
    setProjectionLoading(true);
    setConsoleOutput("");
    try {
      const queryBody = {
        text: queryText,
        mode,
        topK,
        useLlmParser,
        requiresDragon,
        requiresBaron,
        noKills,
        minKills: minKills > 0 ? minKills : null,
        windowSeconds,
      };
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(queryBody),
      });
      const payload = (await response.json()) as QueryPayload & { message?: string; stderr?: string };
      if (!response.ok) {
        setConsoleOutput(payload.stderr || payload.message || "Query failed");
        setProjection([]);
        return;
      }
      setResults(payload.results || []);
      setSelected(payload.results?.[0] || null);
      setConsoleOutput(JSON.stringify({ mode: payload.mode, filters: payload.filters }, null, 2));

      const projectionResponse = await fetch("/api/projection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(queryBody),
      });
      const projectionPayload = (await projectionResponse.json()) as ProjectionPayload & { message?: string; stderr?: string };
      if (projectionResponse.ok) {
        setProjection(projectionPayload.points || []);
      } else {
        setProjection([]);
        setConsoleOutput(projectionPayload.stderr || projectionPayload.message || "Projection failed");
      }
    } finally {
      setQueryLoading(false);
      setProjectionLoading(false);
    }
  }

  async function runCommand(action: string, payload: Record<string, unknown>) {
    setCommandLoading(action);
    setConsoleOutput("");
    try {
      const response = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, payload }),
      });
      const data = (await response.json()) as CommandResponse;
      setConsoleOutput([data.stdout, data.stderr].filter(Boolean).join("\n") || JSON.stringify(data.json, null, 2));
      await refreshStatus();
    } finally {
      setCommandLoading("");
    }
  }

  function exportSelected() {
    if (!selected) return;
    runCommand("export-clip", {
      matchId: selected.match_id,
      startMs: selected.start_ms,
      endMs: selected.end_ms,
      title: "web_selected",
      autoFetch,
      autoLoad,
      codec: "webm",
    });
  }

  function openSelected(noRecord = false) {
    if (!selected) return;
    const resultIndex = results.findIndex((row) => sameWindow(row, selected));
    if (resultIndex < 0) return;
    const index = resultIndex + 1;
    runCommand("query-open-result", {
      text: queryText,
      topK,
      resultIndex: index,
      autoFetch,
      noAutoLoad: !autoLoad,
      noRecord,
      codec: "webm",
    });
  }

  const manifestClips = status?.manifest.clips || [];

  return (
    <main className="mx-auto grid w-full max-w-[1480px] gap-5 px-4 py-4 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-4 border-b border-line pb-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded border border-ink bg-ink text-white">
              <Swords className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-normal text-ink">LoL Replay Search</h1>
              <p className="text-sm text-neutral-600">Recherche, timeline, clips, replay local</p>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <IconButton label="Rafraichir" icon={RefreshCcw} onClick={refreshStatus} disabled={statusLoading} />
          <IconButton label="Parser LLM" icon={Sparkles} onClick={() => setUseLlmParser((v) => !v)} active={useLlmParser} />
          <IconButton label="Auto fetch" icon={Download} onClick={() => setAutoFetch((v) => !v)} active={autoFetch} />
          <IconButton label="Auto load" icon={CirclePlay} onClick={() => setAutoLoad((v) => !v)} active={autoLoad} />
        </div>
      </header>

      <section className="grid metric-grid gap-3">
        {metricCards.map(({ key, label, icon: Icon }) => (
          <div key={key} className="rounded border border-line bg-white p-4 shadow-panel">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{label}</span>
              <Icon className="h-4 w-4 text-tide" />
            </div>
            <div className="mt-3 text-3xl font-semibold tracking-normal">{metrics[key as keyof typeof metrics]}</div>
          </div>
        ))}
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_420px]">
        <section className="grid gap-5">
          <form onSubmit={runQuery} className="rounded border border-line bg-white p-4 shadow-panel">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <Field label="Recherche">
                <div className="flex min-w-0 overflow-hidden rounded border border-line bg-paper">
                  <span className="grid w-11 place-items-center border-r border-line text-neutral-500">
                    <Search className="h-4 w-4" />
                  </span>
                  <input
                    value={queryText}
                    onChange={(event) => setQueryText(event.target.value)}
                    className="h-11 min-w-0 flex-1 bg-transparent px-3 text-sm outline-none"
                  />
                </div>
              </Field>
              <Field label="Mode">
                <select
                  value={mode}
                  onChange={(event) => setMode(event.target.value as "auto" | "semantic" | "precise")}
                  className="h-11 rounded border border-line bg-paper px-3 text-sm"
                >
                  <option value="auto">Auto</option>
                  <option value="semantic">Semantique</option>
                  <option value="precise">Precis</option>
                </select>
              </Field>
              <Field label="Top K">
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={topK}
                  onChange={(event) => setTopK(Number(event.target.value))}
                  className="h-11 w-24 rounded border border-line bg-paper px-3 text-sm"
                />
              </Field>
              <PrimaryButton icon={Search} loading={queryLoading}>
                Chercher
              </PrimaryButton>
            </div>

            <div className="mt-4 grid gap-3 border-t border-line pt-4 md:grid-cols-2 xl:grid-cols-5">
              <Field label="Kills min">
                <input
                  type="number"
                  min={0}
                  value={minKills}
                  onChange={(event) => setMinKills(Number(event.target.value))}
                  className="h-10 rounded border border-line bg-paper px-3 text-sm"
                />
              </Field>
              <Field label="Fenetre">
                <input
                  type="number"
                  min={1}
                  value={windowSeconds}
                  onChange={(event) => setWindowSeconds(Number(event.target.value))}
                  className="h-10 rounded border border-line bg-paper px-3 text-sm"
                />
              </Field>
              <button
                type="button"
                onClick={() => setRequiresDragon((v) => !v)}
                className={clsx(
                  "h-10 rounded border px-3 text-sm font-semibold",
                  requiresDragon ? "border-tide bg-tide text-white" : "border-line bg-paper text-ink",
                )}
              >
                Dragon
              </button>
              <button
                type="button"
                onClick={() => setRequiresBaron((v) => !v)}
                className={clsx(
                  "h-10 rounded border px-3 text-sm font-semibold",
                  requiresBaron ? "border-ember bg-ember text-white" : "border-line bg-paper text-ink",
                )}
              >
                Baron
              </button>
              <button
                type="button"
                onClick={() => setNoKills((v) => !v)}
                className={clsx(
                  "h-10 rounded border px-3 text-sm font-semibold",
                  noKills ? "border-brass bg-brass text-white" : "border-line bg-paper text-ink",
                )}
              >
                No kills
              </button>
            </div>
          </form>

          {(projection.length > 0 || projectionLoading) ? (
            <ProjectionPanel
              points={projection}
              selected={selected}
              loading={projectionLoading}
              onSelect={(point) => {
                const result = results.find((row) => sameWindow(row, point));
                setSelected(result || point);
              }}
            />
          ) : null}

          <section className="grid gap-3">
            {results.map((row, index) => (
              <button
                key={`${row.match_id}-${row.start_ms}-${row.end_ms}-${index}`}
                type="button"
                onClick={() => setSelected(row)}
                className={clsx(
                  "rounded border bg-white p-4 text-left shadow-panel transition hover:border-tide",
                  selected === row ? "border-tide ring-2 ring-tide/15" : "border-line",
                )}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <Pill tone="teal">#{index + 1}</Pill>
                    <span className="truncate font-semibold">{row.match_id}</span>
                    <span className="text-sm text-neutral-500">
                      {formatMs(row.start_ms)} - {formatMs(row.end_ms)}
                    </span>
                  </div>
                  {typeof row.score === "number" ? <Pill tone="green">{row.score.toFixed(3)}</Pill> : null}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Pill tone={numberValue(row.kills_total) > 0 ? "red" : "neutral"}>{row.kills_total ?? 0} kills</Pill>
                  <Pill tone={numberValue(row.dragon_kills) > 0 ? "teal" : "neutral"}>{row.dragon_kills ?? 0} dragons</Pill>
                  <Pill tone={numberValue(row.baron_kills) > 0 ? "red" : "neutral"}>{row.baron_kills ?? 0} barons</Pill>
                  <Pill tone="amber">{row.gold_abs_swing ?? 0} gold</Pill>
                </div>
                <p className="mt-3 line-clamp-2 text-sm leading-6 text-neutral-600">{row.text}</p>
              </button>
            ))}
          </section>
        </section>

        <aside className="grid content-start gap-5">
          <section className="rounded border border-line bg-white p-4 shadow-panel">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold">Replay</h2>
              <Settings2 className="h-4 w-4 text-neutral-500" />
            </div>
            {selected ? (
              <div className="grid gap-3">
                <div className="rounded border border-line bg-paper p-3">
                  <div className="truncate text-sm font-semibold">{selected.match_id}</div>
                  <div className="mt-1 text-sm text-neutral-600">
                    {formatMs(selected.start_ms)} - {formatMs(selected.end_ms)}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => openSelected(true)}
                    disabled={!!commandLoading}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded border border-line bg-paper text-sm font-semibold hover:border-tide"
                  >
                    <Play className="h-4 w-4" />
                    Open
                  </button>
                  <button
                    type="button"
                    onClick={exportSelected}
                    disabled={!!commandLoading}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded border border-line bg-paper text-sm font-semibold hover:border-tide"
                  >
                    <Video className="h-4 w-4" />
                    Clip
                  </button>
                  <button
                    type="button"
                    onClick={() => openSelected(false)}
                    disabled={!!commandLoading}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded border border-line bg-paper text-sm font-semibold hover:border-tide"
                  >
                    <Clapperboard className="h-4 w-4" />
                    Record
                  </button>
                </div>
              </div>
            ) : (
              <div className="rounded border border-line bg-paper p-4 text-sm text-neutral-600">Aucun resultat</div>
            )}
          </section>

          <section className="rounded border border-line bg-white p-4 shadow-panel">
            <h2 className="mb-4 text-base font-semibold">Pipeline</h2>
            <div className="grid gap-3">
              <div className="grid grid-cols-[1fr_96px] gap-2">
                <input
                  value={ingestRiotId}
                  onChange={(event) => setIngestRiotId(event.target.value)}
                  placeholder="GameName#EUW"
                  className="h-10 min-w-0 rounded border border-line bg-paper px-3 text-sm"
                />
                <input
                  type="number"
                  min={1}
                  value={ingestCount}
                  onChange={(event) => setIngestCount(Number(event.target.value))}
                  className="h-10 rounded border border-line bg-paper px-3 text-sm"
                />
              </div>
              <button
                type="button"
                disabled={!ingestRiotId || !!commandLoading}
                onClick={() => runCommand("ingest-summoner", { riotId: ingestRiotId, count: ingestCount })}
                className="inline-flex h-10 items-center justify-center gap-2 rounded border border-line bg-paper text-sm font-semibold hover:border-tide disabled:opacity-50"
              >
                <UploadCloud className="h-4 w-4" />
                Ingest
              </button>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Step">
                  <input
                    type="number"
                    min={1}
                    value={stepSeconds}
                    onChange={(event) => setStepSeconds(Number(event.target.value))}
                    className="h-10 rounded border border-line bg-paper px-3 text-sm"
                  />
                </Field>
                <Field label="Export K">
                  <input
                    type="number"
                    min={1}
                    value={exportK}
                    onChange={(event) => setExportK(Number(event.target.value))}
                    className="h-10 rounded border border-line bg-paper px-3 text-sm"
                  />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={!!commandLoading}
                  onClick={() => runCommand("build-windows", { windowSeconds, stepSeconds })}
                  className="h-10 rounded border border-line bg-paper text-sm font-semibold hover:border-tide disabled:opacity-50"
                >
                  Windows
                </button>
                <button
                  type="button"
                  disabled={!!commandLoading}
                  onClick={() => runCommand("build-index", {})}
                  className="h-10 rounded border border-line bg-paper text-sm font-semibold hover:border-tide disabled:opacity-50"
                >
                  Index
                </button>
              </div>
              <button
                type="button"
                disabled={!!commandLoading}
                onClick={() =>
                  runCommand("export-clips-from-query", {
                    text: queryText,
                    topK,
                    exportK,
                    autoFetch,
                    autoLoad,
                    useLlmParser,
                  })
                }
                className="inline-flex h-10 items-center justify-center gap-2 rounded border border-ink bg-ink text-sm font-semibold text-white hover:bg-moss disabled:opacity-50"
              >
                {commandLoading === "export-clips-from-query" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Film className="h-4 w-4" />}
                Export batch
              </button>
            </div>
          </section>

          <section className="rounded border border-line bg-[#1f1f1c] p-4 text-white shadow-panel">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-semibold">Console</h2>
              {commandLoading ? <Loader2 className="h-4 w-4 animate-spin text-brass" /> : null}
            </div>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded border border-white/10 bg-black/25 p-3 text-xs leading-5 text-neutral-200">
              {consoleOutput || "Ready"}
            </pre>
          </section>
        </aside>
      </div>

      <section className="grid gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Clips</h2>
          <Pill tone="teal">{manifestClips.length} manifest</Pill>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(status?.clips || []).map((clip) => {
            const manifest = manifestClips.find((item) => clipNameFromPath(item.local_path) === clip.file || item.video === clip.file);
            return (
              <article key={clip.file} className="overflow-hidden rounded border border-line bg-white shadow-panel">
                <video className="aspect-video w-full bg-black" src={clip.href} controls preload="metadata" />
                <div className="grid gap-2 p-3">
                  <div className="truncate text-sm font-semibold">{manifest?.title || clip.file}</div>
                  <div className="flex flex-wrap gap-2">
                    {manifest?.match_id ? <Pill>{manifest.match_id}</Pill> : null}
                    {manifest?.start_ms !== undefined ? (
                      <Pill tone="amber">
                        {formatMs(manifest.start_ms)} - {formatMs(manifest.end_ms)}
                      </Pill>
                    ) : null}
                    <Pill tone="green">{Math.max(1, Math.round(clip.size / 1024 / 1024))} MB</Pill>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </main>
  );
}
