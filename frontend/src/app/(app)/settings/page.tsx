"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, Card, Field, PageHeader, inputClass } from "@/components/ui";

type ImportResult = {
  job_id: string; status: string; total_rows: number; ok_rows: number;
  error_rows: number; errors_sample: { row: number; error: string }[];
};
type Account = { id: string; channel: string; provider: string; status: string; display_name?: string | null };
type Membership = { id: string; role: string | null; user: { email: string; full_name: string; is_active: boolean } };
type AuditEntry = {
  id: string; action: string; entity_type: string; source: string;
  actor_type: string; occurred_at: string;
};

export default function SettingsPage() {
  const { t, locale } = useI18n();
  const [tenantSlug, setTenantSlug] = useState("");
  const [simForm, setSimForm] = useState({ from_phone: "+201000000001", name: "عميل تجريبي", text: "عايز شقة في التجمع الخامس" });
  const [simResult, setSimResult] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [team, setTeam] = useState<Membership[] | null>(null);
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);
  const [importKind, setImportKind] = useState("properties");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<{ user: Record<string, unknown>; tenant_slug?: string }>("/auth/me").then((me) => {
      if (me.tenant_slug) setTenantSlug(me.tenant_slug);
    }).catch(() => {});
    api.get<Account[]>("/channels/accounts").then(setAccounts).catch(() => setAccounts([]));
    api.get<Membership[]>("/org/users").then(setTeam).catch(() => setTeam(null));
    api.get<{ items: AuditEntry[] }>("/audit?limit=15").then((r) => setAudit(r.items)).catch(() => setAudit(null));
  }, []);

  const loadAccounts = useCallback(() => {
    api.get<Account[]>("/channels/accounts").then(setAccounts).catch(() => setAccounts([]));
  }, []);

  async function simulateInbound() {
    // discover the tenant slug from /auth/me bootstrap context
    const slugRes = await fetch("/backend/health").catch(() => null);
    const res = await api.post<{ status: string; conversation_id?: string; lead_id?: string }>(
      "/channels/simulator/inbound",
      { tenant_slug: tenantSlug || localStorage.getItem("tenant_slug") || "", ...simForm },
    );
    setSimResult(`${res.status} · conversation ${res.conversation_id?.slice(0, 8) ?? "—"} · lead ${res.lead_id?.slice(0, 8) ?? "—"}`);
    loadAccounts();
  }

  async function connectSimulator() {
    await api.post("/channels/accounts", {
      channel: "whatsapp", provider: "simulator", display_name: "WhatsApp (Simulator)", config: {},
    });
    loadAccounts();
  }

  async function connectMeta() {
    const accessToken = prompt("Meta WhatsApp access token:");
    const phoneNumberId = prompt("Meta phone_number_id:");
    if (!accessToken || !phoneNumberId) return;
    await api.post("/channels/accounts", {
      channel: "whatsapp", provider: "meta_whatsapp", display_name: "WhatsApp Business",
      config: { access_token: accessToken, phone_number_id: phoneNumberId },
    });
    loadAccounts();
  }

  async function doImport() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("kind", importKind);
      fd.append("file", file);
      setImportResult(await api.upload<ImportResult>("/imports", fd));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t("settings_title")} />

      {/* Channels */}
      <Card className="p-6">
        <h3 className="mb-4 font-bold">📱 {t("settings_channels")}</h3>
        <div className="mb-4 flex flex-wrap gap-2">
          {accounts.map((a) => (
            <Badge key={a.id} tone={a.status === "connected" ? "green" : "gray"}>
              {a.display_name || a.provider} · {a.status === "connected" ? "متصل" : "غير متصل"}
            </Badge>
          ))}
          {accounts.length === 0 && <span className="text-sm text-slate-400">لا توجد قنوات مربوطة</span>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={connectSimulator}>🧪 ربط المحاكي (تطوير)</Button>
          <Button variant="outline" onClick={connectMeta}>📲 ربط WhatsApp Business (Meta)</Button>
        </div>
        <div className="mt-5 rounded-xl bg-slate-50 p-4">
          <p className="mb-3 text-sm font-bold">{t("channel_simulator")}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label={t("from_phone")}>
              <input className={inputClass} dir="ltr" value={simForm.from_phone}
                     onChange={(e) => setSimForm({ ...simForm, from_phone: e.target.value })} />
            </Field>
            <Field label="الاسم">
              <input className={inputClass} value={simForm.name}
                     onChange={(e) => setSimForm({ ...simForm, name: e.target.value })} />
            </Field>
            <Field label={t("message_text")}>
              <input className={inputClass} value={simForm.text}
                     onChange={(e) => setSimForm({ ...simForm, text: e.target.value })} />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={simulateInbound}>📨 {t("simulate_inbound")}</Button>
            {simResult && <span className="font-mono text-xs text-brand-700">{simResult}</span>}
          </div>
          <p className="mt-2 text-xs text-slate-400">
            الرسالة تظهر فورًا في صندوق الوارد — ونفس الخط ده هيشتغل مع Meta لما توصّل المفاتيح.
          </p>
        </div>
      </Card>

      {/* Import */}
      <Card className="p-6">
        <h3 className="mb-4 font-bold">📥 {t("import_title")}</h3>
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("import_kind")}>
            <select className={inputClass} value={importKind} onChange={(e) => setImportKind(e.target.value)}>
              <option value="properties">{t("properties")}</option>
              <option value="people">{t("people")}</option>
            </select>
          </Field>
          <Field label="الملف (Excel/CSV)">
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" className={inputClass} />
          </Field>
          <Button onClick={doImport} disabled={uploading}>{uploading ? t("loading") : t("upload")}</Button>
        </div>
        {importResult && (
          <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm">
            <p className="font-bold">{t("import_result")}: {importResult.status}</p>
            <p className="mt-1">✅ {t("ok_rows")}: {importResult.ok_rows} · ❌ {t("error_rows")}: {importResult.error_rows} / {importResult.total_rows}</p>
            {importResult.errors_sample?.length > 0 && (
              <ul className="mt-2 list-inside list-disc text-xs text-rose-600">
                {importResult.errors_sample.map((e, i) => (
                  <li key={i}>صف {e.row}: {e.error}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      {/* Team */}
      <Card className="p-6">
        <h3 className="mb-4 font-bold">👥 {t("settings_team")}</h3>
        {team === null ? (
          <p className="text-sm text-slate-400">{t("loading")}</p>
        ) : (
          <div className="space-y-2">
            {team.map((m) => (
              <div key={m.id} className="flex items-center justify-between rounded-xl border border-slate-100 p-3">
                <div>
                  <p className="text-sm font-semibold">{m.user.full_name || m.user.email}</p>
                  <p className="text-xs text-slate-400" dir="ltr">{m.user.email}</p>
                </div>
                <Badge tone="blue">{m.role || "viewer"}</Badge>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Audit */}
      <Card className="p-6">
        <h3 className="mb-4 font-bold">🧾 {t("audit_log")} (§94)</h3>
        {audit === null ? (
          <p className="text-sm text-slate-400">{t("loading")}</p>
        ) : (
          <div className="space-y-1.5 font-mono text-xs">
            {audit.map((entry) => (
              <div key={entry.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
                <span className="text-slate-400">{fmtDate(entry.occurred_at, locale)}</span>
                <Badge tone={entry.source === "ai" ? "violet" : entry.source === "automation" ? "amber" : "gray"}>
                  {entry.source}
                </Badge>
                <span className="font-semibold">{entry.action}</span>
                <span className="text-slate-400">{entry.entity_type}</span>
                <span className="text-slate-400">بواسطة {entry.actor_type}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
