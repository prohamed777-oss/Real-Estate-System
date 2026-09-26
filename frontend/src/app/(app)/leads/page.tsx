"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  Badge, Button, EmptyState, Field, Modal, PageHeader, Spinner, Table, Td, Th, inputClass, stageTone,
} from "@/components/ui";

type Lead = {
  id: string; person_name?: string; person_phone?: string; source?: string;
  lifecycle_stage: string; lead_score: number; intent_score: number;
  engagement_score: number; fit_score: number; owner_id?: string | null;
  next_action?: string | null; created_at: string;
};
type Detail = Lead & {
  allowed_events: string[];
  requirements: { explicit: Record<string, unknown>; behavioral: Record<string, unknown>; objections: unknown[]; timeline?: string; confidence: number };
  score_signals: Record<string, unknown>;
};
type Person = { id: string; full_name: string; phone?: string | null };

const STAGES = ["NEW", "CONTACTED", "QUALIFYING", "QUALIFIED", "NURTURE", "CONVERTED", "DISQUALIFIED", "DORMANT"];
const EVENT_LABELS: Record<string, { ar: string; en: string }> = {
  contact: { ar: "تم التواصل", en: "Contact" },
  qualify: { ar: "بدء تأهيل", en: "Start qualifying" },
  qualified: { ar: "تأهيل", en: "Qualify" },
  nurture: { ar: "تنشئة", en: "Nurture" },
  convert: { ar: "تحويل لفرصة", en: "Convert" },
  disqualify: { ar: "استبعاد", en: "Disqualify" },
  mark_dormant: { ar: "تعليم خامل", en: "Mark dormant" },
  reactivate: { ar: "إعادة تنشيط", en: "Reactivate" },
};

export default function LeadsPage() {
  const { t, locale } = useI18n();
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [creating, setCreating] = useState(false);
  const [people, setPeople] = useState<Person[]>([]);
  const [form, setForm] = useState({ full_name: "", phone: "", email: "", source: "manual" });
  const [q, setQ] = useState("");

  const load = useCallback(() => {
    api.get<{ items: Lead[] }>(`/leads?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`)
      .then((r) => setLeads(r.items))
      .catch(() => setLeads([]));
  }, [q]);

  useEffect(() => { load(); }, [load]);

  async function openDetail(id: string) {
    const d = await api.get<Detail>(`/leads/${id}`);
    setDetail(d);
  }

  async function transition(leadId: string, event: string) {
    await api.post(`/leads/${leadId}/transition`, { event });
    await openDetail(leadId);
    load();
  }

  async function assignToMe(leadId: string) {
    const me = await api.get<{ user: { id: string } }>("/auth/me");
    await api.post(`/leads/${leadId}/assign`, { owner_id: me.user.id });
    load();
  }

  async function createLead() {
    const person = await api.post<Person>("/people", {
      full_name: form.full_name, phone: form.phone || null, email: form.email || null,
    });
    await api.post("/leads", { person_id: person.id, source: form.source || "manual" });
    setCreating(false);
    setForm({ full_name: "", phone: "", email: "", source: "manual" });
    load();
  }

  return (
    <div>
      <PageHeader
        title={t("leads_title")}
        actions={
          <>
            <input
              value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="🔍 بحث..." className={inputClass + " w-48"}
            />
            <Button onClick={() => setCreating(true)}>＋ {t("new_lead")}</Button>
          </>
        }
      />

      {leads === null ? (
        <Spinner />
      ) : leads.length === 0 ? (
        <EmptyState icon="🎯" title={t("no_leads")} hint="ابدأ بإضافة عميل محتمل أو اربط قناة واتساب" />
      ) : (
        <Table head={
          <>
            <Th>{t("requirements")}</Th>
            <Th>{t("stage")}</Th>
            <Th>{t("score")}</Th>
            <Th>{t("source")}</Th>
            <Th>{t("created")}</Th>
            <Th>{t("actions")}</Th>
          </>
        }>
          {leads.map((lead) => (
            <tr key={lead.id} className="cursor-pointer hover:bg-slate-50" onClick={() => openDetail(lead.id)}>
              <Td>
                <p className="font-semibold">{lead.person_name || "—"}</p>
                <p className="text-xs text-slate-400" dir="ltr">{lead.person_phone || ""}</p>
              </Td>
              <Td><Badge tone={stageTone(lead.lifecycle_stage)}>{lead.lifecycle_stage}</Badge></Td>
              <Td>
                <span className="font-bold text-brand-700">{lead.lead_score}</span>
                <span className="ms-2 text-xs text-slate-400">
                  🎯{lead.intent_score} · 💬{lead.engagement_score} · 🏠{lead.fit_score}
                </span>
              </Td>
              <Td className="text-xs">{lead.source || "—"}</Td>
              <Td className="text-xs text-slate-400">{fmtDate(lead.created_at, locale)}</Td>
              <Td onClick={(e) => e.stopPropagation()}>
                <Button variant="ghost" onClick={() => assignToMe(lead.id)}>{t("assign_to_me")}</Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}

      {/* Create modal */}
      <Modal open={creating} onClose={() => setCreating(false)} title={t("new_lead")}>
        <Field label="الاسم / Name">
          <input className={inputClass} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        </Field>
        <Field label={t("from_phone")}>
          <input className={inputClass} dir="ltr" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+2010..." />
        </Field>
        <Field label={t("email")}>
          <input className={inputClass} dir="ltr" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
        <Button onClick={createLead} className="w-full justify-center">{t("save")}</Button>
      </Modal>

      {/* Detail modal */}
      <Modal open={!!detail} onClose={() => setDetail(null)} title={detail?.person_name || ""}>
        {detail && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={stageTone(detail.lifecycle_stage)}>{detail.lifecycle_stage}</Badge>
              <Badge tone="green">{t("score")}: {detail.lead_score}</Badge>
              {detail.requirements.confidence > 0 && (
                <Badge tone="blue">ثقة المتطلبات: {detail.requirements.confidence}%</Badge>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3 rounded-xl bg-slate-50 p-4 text-sm">
              <div><span className="text-slate-400">{t("budget")}:</span> <b>{String(detail.requirements.explicit.max_budget ?? "—")}</b></div>
              <div><span className="text-slate-400">{t("bedrooms")}:</span> <b>{String(detail.requirements.explicit.bedrooms ?? "—")}</b></div>
              <div><span className="text-slate-400">{t("area")}:</span> <b>{String(detail.requirements.explicit.areas ?? "—")}</b></div>
              <div><span className="text-slate-400">{t("timeline")}:</span> <b>{detail.requirements.timeline ?? "—"}</b></div>
            </div>
            {detail.requirements.objections?.length > 0 && (
              <div className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">
                <b>اعتراضات:</b> {JSON.stringify(detail.requirements.objections)}
              </div>
            )}
            <div>
              <p className="mb-2 text-sm font-semibold text-slate-500">{t("transition")}:</p>
              <div className="flex flex-wrap gap-2">
                {detail.allowed_events.map((event) => (
                  <Button key={event} variant="outline" onClick={() => transition(detail.id, event)}>
                    {(EVENT_LABELS[event]?.[locale]) ?? event}
                  </Button>
                ))}
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
