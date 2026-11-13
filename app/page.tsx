"use client"

import { useState } from "react"
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  FileArchive,
  FolderGit2,
  LockKeyhole,
  ShieldCheck,
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
        throw new Error(result?.error || "ডাউনলোড শুরু করা যায়নি। কিছুক্ষণ পর আবার চেষ্টা করুন।")
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
      setMessage("ডাউনলোড প্রস্তুত। আপনার ZIP ফাইলটি সেভ হয়েছে।")
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "ডাউনলোড ব্যর্থ হয়েছে।")
    } finally {
      setDownloading(false)
    }
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Nexora home">
          <span className="brand-mark"><FolderGit2 size={19} strokeWidth={1.8} /></span>
          <span>nexora<span className="brand-period">.</span></span>
        </a>
        <a
          className="repo-link"
          href="https://github.com/tasfik222/nexora-telegram-bot-python-code/tree/main/files%20v6"
          target="_blank"
          rel="noreferrer"
        >
          <span>GitHub repository</span><ArrowUpRight size={15} />
        </a>
      </header>

      <section className="hero" aria-labelledby="hero-title">
        <div className="eyebrow"><span className="status-dot" /> PRIVATE REPOSITORY <span className="eyebrow-divider">/</span> V6
        </div>
        <h1 id="hero-title">আপনার পরের প্রজেক্ট,<br /><span>এক ক্লিক দূরে।</span></h1>
        <p className="hero-copy">
          Nexora Telegram Bot-এর v6 ফাইলগুলো একসাথে ZIP আকারে ডাউনলোড করুন।
          সোর্স কোড, ডেটা, ব্যাকআপ—সবই আর্কাইভে অন্তর্ভুক্ত।
        </p>

        <div className="download-panel">
          <div className="file-icon"><FileArchive size={23} strokeWidth={1.7} /></div>
          <div className="file-details">
            <strong>nexora-files-v6.zip</strong>
            <span>সম্পূর্ণ ফোল্ডার <span className="file-separator">·</span> ZIP archive</span>
          </div>
          <button className="download-button" onClick={downloadArchive} disabled={downloading}>
            {downloading ? <span className="spinner" aria-hidden="true" /> : <ArrowDownToLine size={18} />}
            <span>{downloading ? "তৈরি হচ্ছে…" : "ডাউনলোড"}</span>
          </button>
        </div>
        <p className="feedback" role="status" aria-live="polite">{message}</p>

        <div className="trust-row">
          <div><LockKeyhole size={15} /><span>সার্ভার-সাইড নিরাপত্তা</span></div>
          <span className="trust-divider" />
          <div><ShieldCheck size={15} /><span>টোকেন ব্রাউজারে যায় না</span></div>
        </div>
      </section>

      <section className="details-grid" aria-label="Download details">
        <article className="detail-card">
          <span className="detail-index">01</span>
          <h2>একটি সম্পূর্ণ আর্কাইভ</h2>
          <p><code>files v6</code> ফোল্ডারের সব ফাইল—Python modules, data, backups এবং cache—ZIP-এ থাকবে।</p>
        </article>
        <article className="detail-card">
          <span className="detail-index">02</span>
          <h2>Private source, secure access</h2>
          <p>GitHub token শুধু server route-এ ব্যবহৃত হয়; page source বা browser response-এ প্রকাশ করা হয় না।</p>
        </article>
        <article className="detail-card setup-card">
          <span className="setup-icon"><Check size={15} /></span>
          <h2>Environment প্রস্তুত</h2>
          <p><code>GITHUB_TOKEN</code> Vercel Vars-এ রাখা আছে এবং download request-এ server-side পড়া হয়।</p>
        </article>
      </section>

      <footer className="footer">
        <span>© {new Date().getFullYear()} Nexora</span>
        <span>Built for a faster handoff</span>
      </footer>
    </main>
  )
}
