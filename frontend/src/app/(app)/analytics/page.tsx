"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, Card, PageHeader, inputClass } from "@/components/ui";

type QueryResult = {
  plan: { filters: Record<string, unknown>; explanation_parts: string[]; limit: number; sort: string };
  count: number;
  results: Array<{
    lead_id: string; stage: string; score: number; person_id: string;
    budget?: string | number | null; bedrooms?: string | number | null; days_silent?: number;
  }>;
};

export default function AnalyticsPage() {
  const { t } = useI18n();
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function ask() {
    if (!question.trim()) return;
    setLoading(true); setError("");
    try {
      setResult(await api.post<QueryResult>("/analytics/query", { question }));
    } catch {
      setError(t("error_generic"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <PageHeader
        title={t("analytics_title")}
        subtitle="Natural-language queries → deterministic structured plans → data service (LLM never touches SQL — §73)"
      />

      <Card className="mb-6 p-6">
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            className={inputClass}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder={t("nl_hint")}
          />
          <Button onClick={ask} disabled={loading}>{loading ? t("loading") : `🧠 ${t("ask")}`}</Button>
        </div>
        {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
        {result && (
          <div className="mt-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Badge tone="green">{result.count} {t("results")}</Badge>
              {result.plan.explanation_parts.map((part) => (
                <Badge key={part} tone="blue">{part}</Badge>
              ))}
            </div>
            {result.results.length > 0 && (
              <div className="overflow-hidden rounded-xl border border-slate-100">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-xs text-slate-400">
                    <tr>
                      <th className="p-2 text-start">Lead</th>
                      <th className="p-2 text-start">{t("stage")}</th>
                      <th className="p-2 text-start">{t("score")}</th>
                      <th className="p-2 text-start">{t("budget")}</th>
                      <th className="p-2 text-start">{t("bedrooms")}</th>
                      <th className="p-2 text-start">صمت (أيام)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {result.results.map((r) => (
                      <tr key={r.lead_id}>
                        <td className="p-2 font-mono text-xs">#{r.lead_id.slice(0, 8)}</td>
                        <td className="p-2">{r.stage}</td>
                        <td className="p-2 font-bold text-brand-700">{r.score}</td>
                        <td className="p-2">{r.budget ? String(r.budget) : "—"}</td>
                        <td className="p-2">{r.bedrooms ? String(r.bedrooms) : "—"}</td>
                        <td className="p-2">{r.days_silent ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Card>

      <Card className="p-6">
        <h3 className="mb-2 font-bold">كيف يعمل؟</h3>
        <div className="grid gap-3 text-sm text-slate-600 md:grid-cols-4">
          {[
            ["1. فهم السؤال", "مفكك نوايا عربي/إنجليزي ينبّه الميزانية والغرف وصمت التواصل"],
            ["2. خطة منظمة", "المخرجات Structured Query Plan — لا SQL من الـLLM أبدًا"],
            ["3. تنفيذ مفوّض", "خدمة البيانات تنفذ داخل نطاق الـtenant والصلاحيات"],
            ["4. شرح شفاف", "ترى الخطة نفسها — كل رقم قابل للتتبع (§72)"],
          ].map(([title, body]) => (
            <div key={title} className="rounded-xl bg-slate-50 p-4">
              <p className="mb-1 font-bold text-ink">{title}</p>
              <p className="text-xs">{body}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
