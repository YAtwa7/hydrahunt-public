import { useEffect, useRef, useState } from 'react'
import { askQuestion } from '../api'

const SUGGESTIONS = [
  'Where do remote jobs come from, and which sources need no API key?',
  'How does the region lock detect a Brazil-only role?',
  'Why was a job hidden from my pool?',
  'How does DorkLab breed new search queries?',
  'How do I add a new remote job board?',
  'What does POOL_WORTHY_SQL actually do?',
]

function Bubble({ role, text, model }) {
  return (
    <div className={`bubble-row ${role}`}>
      <div className="bubble">
        {role === 'assistant' ? (
          <pre className="ask-answer">{text}</pre>
        ) : (
          <span>{text}</span>
        )}
        {model && <div className="bubble-model">model: {model}</div>}
      </div>
    </div>
  )
}

export default function AskPanel() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  async function send(q) {
    const question = (q ?? input).trim()
    if (!question || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', text: question }])
    try {
      const res = await askQuestion(question)
      if (res.error) {
        setMessages((m) => [...m, { role: 'assistant', text: `⚠️ ${res.error}` }])
      } else {
        setMessages((m) => [
          ...m,
          { role: 'assistant', text: res.answer, model: res.model },
        ])
      }
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: `⚠️ ${e.message}` },
      ])
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="ask-panel">
      <div className="ask-head">
        <h2>💬 Ask HydraHunt</h2>
        <p className="ask-sub">
          Questions about how this codebase works. Answers are grounded on
          <code> docs/REPO_BRIEF.md</code> and this instance&apos;s live state, via
          the same free-model chain used for scoring. Nothing is stored.
        </p>
      </div>

      {messages.length === 0 && (
        <div className="ask-suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip-btn" onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="ask-log">
        {messages.map((m, i) => (
          <Bubble key={i} role={m.role} text={m.text} model={m.model} />
        ))}
        {busy && <div className="bubble-row assistant"><div className="bubble typing">🧠 thinking…</div></div>}
        <div ref={endRef} />
      </div>

      <form
        className="ask-input"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="How does the remote_boards collector work?"
          disabled={busy}
          maxLength={500}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Ask
        </button>
      </form>
      <p className="ask-foot">
        Needs an LLM key in <code>.env</code>. Turn it off with
        <code> ask.enabled: false</code> in <code>config.yml</code>.
      </p>
    </section>
  )
}