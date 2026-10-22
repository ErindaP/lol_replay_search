import { statSync } from "node:fs";
import { NextRequest } from "next/server";
import { clipStream, getContentType, resolveClipPath } from "@/lib/project";

export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ file: string[] }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  const { file } = await context.params;
  const filePath = resolveClipPath(file.map(decodeURIComponent));
  if (!filePath) {
    return new Response("Not found", { status: 404 });
  }

  const stat = statSync(filePath);
  const range = request.headers.get("range");
  const headers = {
    "Content-Type": getContentType(filePath),
    "Accept-Ranges": "bytes",
  };

  if (range) {
    const [startRaw, endRaw] = range.replace(/bytes=/, "").split("-");
    const start = Number.parseInt(startRaw, 10);
    const end = endRaw ? Number.parseInt(endRaw, 10) : stat.size - 1;
    const chunkSize = end - start + 1;
    return new Response(clipStream(filePath, { start, end }), {
      status: 206,
      headers: {
        ...headers,
        "Content-Range": `bytes ${start}-${end}/${stat.size}`,
        "Content-Length": String(chunkSize),
      },
    });
  }

  return new Response(clipStream(filePath), {
    headers: {
      ...headers,
      "Content-Length": String(stat.size),
    },
  });
}
