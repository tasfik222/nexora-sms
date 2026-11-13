"use client"

import { useState } from "react"
import {
  Activity,
  ArrowDownToLine,
  ArrowUpRight,
  Archive,
  Bot,
  Cable,
  Database,
  FileArchive,
  MessageCircle,
  Package,
  Settings2,
  ShieldCheck,
  WalletCards,
} from "lucide-react"

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
        <a
          className="support-button"
          href="https://t.me/ldrofbtw"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Contact support on Telegram"
        >
          <MessageCircle size={16} />
          <span>Support</span>
          <ArrowUpRight size={14} aria-hidden="true" />
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

      <section className="overview-section" aria-labelledby="modules-title">
        <div className="section-heading">
          <span className="section-kicker">PROJECT BREAKDOWN</span>
          <h2 id="modules-title">A closer look inside</h2>
          <p>A modular Python Telegram bot with integrations, account records, activity tools, and admin workflows.</p>
        </div>

        <div className="details-grid module-grid">
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><Bot size={18} /></span><code>bot.py</code></div>
            <h3>Telegram experience</h3>
            <p>Conversation menus, help, balance views, activity tools, and admin controls.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><Cable size={18} /></span><code>api_client.py</code></div>
            <h3>Service integrations</h3>
            <p>Connects configured providers and normalizes available services, ranges, and incoming messages.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><Database size={18} /></span><code>storage.py</code></div>
            <h3>Account records</h3>
            <p>Local per-user balance and activity history, with safeguards against duplicate credits.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><WalletCards size={18} /></span><code>withdrawals.py</code></div>
            <h3>Request management</h3>
            <p>Tracks pending, completed, rejected, and cancelled withdrawal requests for admin review.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><Activity size={18} /></span><code>traffic.py · console_api.py</code></div>
            <h3>Activity insights</h3>
            <p>Summarizes recent service activity and parses live console entries for the bot interface.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><FileArchive size={18} /></span><code>channel_feed.py · otp_format.py</code></div>
            <h3>Message presentation</h3>
            <p>Formats incoming message notices; optional channel updates mask number details by default.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><Archive size={18} /></span><code>backup.py</code></div>
            <h3>Backup and recovery</h3>
            <p>Creates data snapshots and supports restoring user records and request history.</p>
          </article>
          <article className="detail-card module-card">
            <div className="module-card-top"><span className="module-icon"><Settings2 size={18} /></span><code>settings.py</code></div>
            <h3>Runtime settings</h3>
            <p>Holds the configurable menu, service, and rate settings used by the bot.</p>
          </article>
        </div>
      </section>

      <section className="bot-summary" aria-labelledby="bot-summary-title">
        <div className="summary-main">
          <span className="section-kicker">AFTER SETUP</span>
          <h2 id="bot-summary-title">What the bot does</h2>
          <p>After setup, the bot provides a Telegram interface for authorized SMS verification workflows. It connects configured services, delivers incoming verification messages, tracks user balances and activity, and lets administrators review settings and requests.</p>
          <p className="stack-note">Built with Python, python-telegram-bot, and HTTPX. Dependencies are listed in requirements.txt.</p>
        </div>
        <aside className="safety-note" aria-label="Authorized testing notice">
          <span className="safety-icon"><ShieldCheck size={18} /></span>
          <h3>Use with authorization</h3>
          <p>Only connect services, phone numbers, and accounts you own or are explicitly authorized to test.</p>
        </aside>
      </section>

      <footer className="footer">
        <span>© {new Date().getFullYear()} Nexora</span>
        <span>Built for a faster handoff</span>
      </footer>
    </main>
  )
}
