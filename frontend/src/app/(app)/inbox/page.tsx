"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, EmptyState, PageHeader, Spinner, inputClass } from "@/components/ui";

type Conversation = {
  id: string; person_name?: string; person_phone?: string; channel: string;
  status: string; unread_count: number; last_message_preview?: string | null;
  last_message_at?: string | null; ai_handling: boolean;
  assigned_user_id?: string | null;
};
type Message = {
  id: string; direction: "inbound" | "outbound"; sender_type: string;
  text?: string | null; message_type: string; status: string; created_at: string;
};

export default function InboxPage() {
  const { t, locale } = useI18n();
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [active, setActive] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(() => {
    api.get<Conversation[]>("/conversations")
      .then((rows) => { setConversations(rows); })
      .catch(() => setConversations([]));
  }, []);

  const loadMessages = useCallback((conv: Conversation) => {
    api.get<{ messages: Message[] }>(`/conversations/${conv.id}`)
      .then((d) => { setMessages(d.messages); })
      .catch(() => setMessages([]));
  }, []);

  useEffect(() => {
    loadConversations();
    const timer = setInterval(loadConversations, 5000);
    return () => clearInterval(timer);
  }, [loadConversations]);

  useEffect(() => {
    if (!active) return;
    loadMessages(active);
    const timer = setInterval(() => loadMessages(active), 4000);
    return () => clearInterval(timer);
  }, [active, loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!active || !draft.trim()) return;
    setSending(true);
    try {
      await api.post(`/conversations/${active.id}/reply`, { text: draft });
      setDraft("");
      loadMessages(active);
      loadConversations();
    } finally {
      setSending(false);
    }
  }

  async function runJobs() {
    // Dev helper: tick the local worker so queued outbound messages dispatch
    await fetch("/backend/api/v1/internal/jobs/tick", { method: "POST", headers: { "X-Cron-Secret": "dev-cron-secret" } }).catch(() => {});
    if (active) loadMessages(active);
    loadConversations();
  }

  const channelIcon = (channel: string) =>
    channel === "whatsapp" ? "🟢" : channel === "email" ? "✉️" : channel === "instagram" ? "📸" : "🌐";

  return (
    <div>
      <PageHeader
        title={t("inbox_title")}
        actions={<Button variant="outline" onClick={runJobs}>⚡ تشغيل الـWorker (تطوير)</Button>}
      />
      {conversations === null ? (
        <Spinner />
      ) : conversations.length === 0 ? (
        <EmptyState icon="💬" title={t("no_conversations")} />
      ) : (
        <div className="grid gap-4 md:grid-cols-[320px_1fr]">
          {/* conversation list */}
          <div className="space-y-2 md:max-h-[70vh] md:overflow-y-auto">
            {conversations.map((c) => (
              <button
                key={c.id}
                onClick={() => setActive(c)}
                className={`w-full rounded-2xl border p-4 text-start transition ${
                  active?.id === c.id
                    ? "border-brand-600 bg-brand-50"
                    : "border-slate-200 bg-white hover:border-slate-300"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate font-semibold text-ink">{c.person_name || c.person_phone}</p>
                  {c.unread_count > 0 && <Badge tone="red">{c.unread_count}</Badge>}
                </div>
                <p className="mt-1 truncate text-xs text-slate-500">
                  {channelIcon(c.channel)} {c.last_message_preview || "—"}
                </p>
                <p className="mt-1 text-[11px] text-slate-400">{fmtDate(c.last_message_at, locale)}</p>
              </button>
            ))}
          </div>

          {/* thread */}
          <div className="flex h-[70vh] flex-col rounded-2xl border border-slate-200 bg-white">
            {active ? (
              <>
                <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
                  <div>
                    <p className="font-bold">{active.person_name || active.person_phone}</p>
                    <p className="text-xs text-slate-400" dir="ltr">{active.person_phone} · {active.channel}</p>
                  </div>
                  {active.ai_handling && <Badge tone="violet">🤖 {t("ai_handling")}</Badge>}
                </div>
                <div className="flex-1 space-y-2 overflow-y-auto bg-slate-50/60 p-4">
                  {messages.map((m) => (
                    <div key={m.id} className={`flex ${m.direction === "outbound" ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`max-w-[75%] rounded-2xl px-4 py-2 text-sm ${
                          m.direction === "outbound"
                            ? "rounded-ee-sm bg-brand-600 text-white"
                            : "rounded-es-sm border border-slate-200 bg-white text-ink"
                        }`}
                      >
                        <p className="whitespace-pre-wrap">{m.text}</p>
                        <p className={`mt-1 text-[10px] ${m.direction === "outbound" ? "text-brand-100" : "text-slate-400"}`}>
                          {fmtDate(m.created_at, locale)} {m.direction === "outbound" && `· ${m.status}`}
                        </p>
                      </div>
                    </div>
                  ))}
                  <div ref={bottomRef} />
                </div>
                <div className="flex gap-2 border-t border-slate-100 p-3">
                  <input
                    className={inputClass}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
                    placeholder={t("write_message")}
                  />
                  <Button onClick={send} disabled={sending || !draft.trim()}>{t("send")}</Button>
                </div>
              </>
            ) : (
              <EmptyState icon="👈" title="اختر محادثة من القائمة" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
