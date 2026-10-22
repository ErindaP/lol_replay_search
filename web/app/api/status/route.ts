import path from "node:path";
import {
  countJsonl,
  dataPath,
  jsonResponse,
  listClipAssets,
  listFiles,
  projectRoot,
  readJsonFile,
} from "@/lib/project";

export const dynamic = "force-dynamic";

export async function GET() {
  const rawDir = dataPath("raw");
  const processedPath = dataPath("processed", "windows.jsonl");
  const indexInfoPath = dataPath("index", "index_info.json");
  const manifestPath = dataPath("clips", "manifest.json");

  const matchFiles = listFiles(rawDir, (file) => file.endsWith(".match.json"));
  const timelineFiles = listFiles(rawDir, (file) => file.endsWith(".timeline.json"));
  const clips = listClipAssets();

  return jsonResponse({
    status: "ok",
    projectRoot: projectRoot(),
    dataRoot: path.join(projectRoot(), "data"),
    raw: {
      matches: matchFiles.length,
      timelines: timelineFiles.length,
    },
    processed: {
      windows: countJsonl(processedPath),
    },
    index: readJsonFile<Record<string, unknown>>(indexInfoPath, {}),
    clips,
    manifest: readJsonFile<Record<string, unknown>>(manifestPath, { clips: [] }),
  });
}
