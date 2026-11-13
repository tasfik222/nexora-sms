"use client"

import { useState } from "react"
import { ArrowDownToLine, FileArchive, Package } from "lucide-react"

export default function Home() {
  const [downloading, setDownloading] = useState(false)
  const [message, setMessage] = useState("")

  async function downloadArchive() {
    setDownloading(true)
    setMessage("")

    try {
      const response = await fetch("/api/download", { cache: "no-store" })
      if (!response.ok) {
        const result = (await response.json().catch(() => null)) as { error?: string } | null
        throw new Error(result?.error || "The download could not be started. Please try again.")
      }

      const archive = await response.blob()
      const objectUrl = URL.createObjectURL(archive)
      const link = document.createElement("a")
      link.href = objectUrl
      link.download = "nexora-files-v6.zip"
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
      setMessage("Your download is ready.")
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Download failed. Please try again.")
    } finally {
      setDownloading(false)
    }
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Nexora home">
          <span className="brand-mark"><Package size={19} strokeWidth={1.8} /></span>
          <span>nexora<span className="brand-period">.</span></span>
        </a>
      </header>

      <section className="hero" aria-labelledby="hero-title">
        <div className="eyebrow"><span className="status-dot" /> NEXORA ARCHIVE <span className="eyebrow-divider">/</span> VERSION 6
        </div>
        <h1 id="hero-title">Everything you need,<br /><span>one click away.</span></h1>
        <p className="hero-copy">
          Get the complete Nexora Telegram Bot v6 package in one convenient download.
          All project files are bundled together in a single ZIP archive.
        </p>

        <div className="download-panel">
          <div className="file-icon"><FileArchive size={23} strokeWidth={1.7} /></div>
          <div className="file-details">
            <strong>nexora-files-v6.zip</strong>
            <span>Complete project package <span className="file-separator">·</span> ZIP archive</span>
          </div>
          <button className="download-button" onClick={downloadArchive} disabled={downloading}>
            {downloading ? <span className="spinner" aria-hidden="true" /> : <ArrowDownToLine size={18} />}
            <span>{downloading ? "Preparing…" : "Download"}</span>
          </button>
        </div>
        <p className="feedback" role="status" aria-live="polite">{message}</p>

        <div className="trust-row">
          <div><FileArchive size={15} /><span>Complete package</span></div>
          <span className="trust-divider" />
          <div><ArrowDownToLine size={15} /><span>One-click download</span></div>
        </div>
      </section>

      <section className="details-grid" aria-label="Download details">
        <article className="detail-card">
          <span className="detail-index">01</span>
          <h2>Everything in one place</h2>
          <p>The complete v6 package, neatly bundled as a single ZIP archive.</p>
        </article>
        <article className="detail-card">
          <span className="detail-index">02</span>
          <h2>Ready to get started</h2>
          <p>Keep your project files together and pick up right where you left off.</p>
        </article>
        <article className="detail-card">
          <span className="detail-index">03</span>
          <h2>Simple and straightforward</h2>
          <p>Select download and save the complete package to your device.</p>
        </article>
      </section>

      <footer className="footer">
        <span>© {new Date().getFullYear()} Nexora</span>
        <span>Built for a faster handoff</span>
      </footer>
    </main>
  )
}
