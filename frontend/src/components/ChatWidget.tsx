"use client";

import { useState, useRef, useEffect } from "react";

type Msg = { role: "visitor" | "agent"; text: string };

export default function ChatWidget({
  tenantSlug,
  accent = "#059669",
  greeting = "أهلاً! أنا مساعدك العقاري 🏠 — اسألني عن أي شقة أو منطقة",
}: {
  tenantSlug: string;
  accent?: string;
  greeting?: string;
}) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([{ role: "agent", text: greeting }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sessionId] = useState(() => `wc-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  async function send() {
    if (!input.trim() || sending) return;
    const text = input.trim();
    setInput(""); setSending(true);
    setMessages(m => [...m, { role: "visitor", text }]);
    try {
      const res = await fetch("/backend/api/v1/webchat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, tenant_slug: tenantSlug, message: text }),
      });
      const data = await res.json();
      setMessages(m => [...m, { role: "agent", text: data.response || "معلش حصلت مشكلة، جرب تاني." }]);
    } catch {
      setMessages(m => [...m, { role: "agent", text: "حصلت مشكلة في الاتصال. جرب تاني." }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      {/* floating bubble */}
      <button
        onClick={() => setOpen(!open)}
        className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full text-2xl text-white shadow-xl transition hover:scale-110"
        style={{ backgroundColor: accent }}
        aria-label="Chat"
      >
        {open ? "✕" : "💬"}
      </button>

      {/* chat panel */}
      {open && (
        <div className="fixed bottom-24 right-6 z-50 flex h-[520px] w-[380px] max-w-[calc(100vw-3rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
          {/* header */}
          <div className="flex items-center gap-3 px-5 py-4" style={{ backgroundColor: accent }}>
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-lg">🏠</div>
            <div>
              <p className="text-sm font-bold text-white">مساعدك العقاري</p>
              <p className="flex items-center gap-1 text-[11px] text-white/70">
                <span className="inline-block h-2 w-2 rounded-full bg-green-400" /> متصل الآن
              </p>
            </div>
          </div>

          {/* messages */}
          <div className="flex-1 space-y-3 overflow-y-auto bg-slate-50 p-4">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "visitor" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${
                    m.role === "visitor"
                      ? "rounded-br-sm bg-brand-600 text-white"
                      : "rounded-bl-sm border border-slate-200 bg-white text-slate-800"
                  }`}
                >
                  {m.text}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-2">
                  <span className="inline-flex gap-1">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: "0ms" }} />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: "150ms" }} />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: "300ms" }} />
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* input */}
          <div className="flex gap-2 border-t border-slate-100 p-3">
            <input
              className="flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-600"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
              placeholder="اكتب سؤالك..."
              dir="auto"
            />
            <button
              onClick={send}
              disabled={sending || !input.trim()}
              className="rounded-xl px-4 py-2 text-sm font-semibold text-white transition disabled:opacity-40"
              style={{ backgroundColor: accent }}
            >
              ➤
            </button>
          </div>
        </div>
      )}
    </>
  );
}
