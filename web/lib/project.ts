import { spawn } from "node:child_process";
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";

export type CommandResult = {
  ok: boolean;
  code: number | null;
  stdout: string;
  stderr: string;
  json: unknown | null;
};

export type ClipAsset = {
  file: string;
  href: string;
  size: number;
  mtimeMs: number;
};

export function projectRoot(): string {
  if (process.env.LOL_SEARCH_PROJECT_ROOT) {
    return path.resolve(process.env.LOL_SEARCH_PROJECT_ROOT);
  }

  const cwd = process.cwd();
  return path.basename(cwd) === "web" ? path.resolve(cwd, "..") : cwd;
}

export function dataPath(...parts: string[]): string {
  return path.join(projectRoot(), "data", ...parts);
}

export function readJsonFile<T>(filePath: string, fallback: T): T {
  if (!existsSync(filePath)) {
    return fallback;
  }

  try {
    return JSON.parse(readFileSync(filePath, "utf8")) as T;
  } catch {
    return fallback;
  }
}

export function readJsonl(filePath: string, limit = 5000): Record<string, unknown>[] {
  if (!existsSync(filePath)) {
    return [];
  }

  return readFileSync(filePath, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(0, limit)
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

export function countJsonl(filePath: string): number {
  if (!existsSync(filePath)) {
    return 0;
  }

  return readFileSync(filePath, "utf8").split(/\r?\n/).filter(Boolean).length;
}

export function listFiles(dir: string, matcher: (file: string) => boolean): string[] {
  if (!existsSync(dir)) {
    return [];
  }

  return readdirSync(dir).filter(matcher).sort();
}

export function listClipAssets(): ClipAsset[] {
  const dir = dataPath("clips");
  if (!existsSync(dir)) {
    return [];
  }

  return readdirSync(dir)
    .filter((file) => /\.(mp4|webm|mov|mkv)$/i.test(file))
    .map((file) => {
      const fullPath = path.join(dir, file);
      const st = statSync(fullPath);
      return {
        file,
        href: `/api/clips/${encodeURIComponent(file)}`,
        size: st.size,
        mtimeMs: st.mtimeMs,
      };
    })
    .sort((a, b) => b.mtimeMs - a.mtimeMs);
}

export function resolveClipPath(parts: string[]): string | null {
  const dir = dataPath("clips");
  const candidate = path.resolve(dir, ...parts);
  const root = path.resolve(dir);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    return null;
  }
  if (!existsSync(candidate) || !statSync(candidate).isFile()) {
    return null;
  }
  return candidate;
}

export function clipStream(filePath: string, options?: { start?: number; end?: number }): ReadableStream {
  return Readable.toWeb(createReadStream(filePath, options)) as ReadableStream;
}

export async function runPython(args: string[], timeoutMs = 120_000): Promise<CommandResult> {
  const root = projectRoot();
  const venvPython = path.join(root, ".venv", "bin", "python");
  const python = process.env.LOL_SEARCH_PYTHON || (existsSync(venvPython) ? venvPython : "python3");
  const pythonPath = path.join(root, "src");
  const env = {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH ? `${pythonPath}${path.delimiter}${process.env.PYTHONPATH}` : pythonPath,
  };

  return new Promise((resolve) => {
    const child = spawn(python, args, { cwd: root, env });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({
        ok: code === 0,
        code,
        stdout,
        stderr,
        json: parseLastJson(stdout),
      });
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ ok: false, code: null, stdout, stderr: error.message, json: null });
    });
  });
}

export function parseLastJson(text: string): unknown | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }

  const lines = trimmed.split(/\r?\n/).reverse();
  for (const line of lines) {
    try {
      return JSON.parse(line);
    } catch {
      continue;
    }
  }

  const start = Math.max(trimmed.lastIndexOf("\n{"), trimmed.lastIndexOf("\n["));
  if (start >= 0) {
    try {
      return JSON.parse(trimmed.slice(start + 1));
    } catch {
      return null;
    }
  }
  return null;
}

export function jsonResponse(payload: unknown, status = 200): Response {
  return Response.json(payload, { status });
}

export function getContentType(filePath: string): string {
  if (filePath.endsWith(".webm")) {
    return "video/webm";
  }
  if (filePath.endsWith(".mp4")) {
    return "video/mp4";
  }
  if (filePath.endsWith(".mov")) {
    return "video/quicktime";
  }
  return "application/octet-stream";
}
