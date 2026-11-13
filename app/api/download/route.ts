import { NextResponse } from "next/server"
import { unzipSync, zipSync } from "fflate"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

const repositoryArchiveUrl =
  "https://api.github.com/repos/tasfik222/nexora-telegram-bot-python-code/zipball/main"
const folderSuffix = "/files v6/"

export async function GET() {
  const token = process.env.GITHUB_TOKEN
  if (!token) {
    return NextResponse.json(
      { error: "Downloads are temporarily unavailable. Please try again later." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    )
  }

  try {
    const githubResponse = await fetch(repositoryArchiveUrl, {
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      cache: "no-store",
      redirect: "follow",
    })

    if (!githubResponse.ok) {
      return NextResponse.json(
        { error: "The download is temporarily unavailable. Please try again later." },
        { status: 502, headers: { "Cache-Control": "no-store" } },
      )
    }

    const sourceArchive = new Uint8Array(await githubResponse.arrayBuffer())
    const repositoryFiles = unzipSync(sourceArchive)
    const selectedFiles: Record<string, Uint8Array> = {}

    for (const [archivePath, content] of Object.entries(repositoryFiles)) {
      const folderIndex = archivePath.indexOf(folderSuffix)
      if (folderIndex === -1) continue

      const relativePath = archivePath.slice(folderIndex + folderSuffix.length)
      if (!relativePath || relativePath.endsWith("/")) continue
      selectedFiles[relativePath] = content
    }

    if (Object.keys(selectedFiles).length === 0) {
      return NextResponse.json(
        { error: "The requested package is not available right now." },
        { status: 404, headers: { "Cache-Control": "no-store" } },
      )
    }

    const zip = zipSync(selectedFiles, { level: 6 })
    return new Response(new Blob([zip], { type: "application/zip" }), {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": 'attachment; filename="nexora-files-v6.zip"',
        "Cache-Control": "private, no-store, max-age=0",
        "X-Content-Type-Options": "nosniff",
      },
    })
  } catch {
    return NextResponse.json(
      { error: "The download could not be prepared. Please try again later." },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    )
  }
}
