"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Card, PageHeader, Spinner, StatCard } from "@/components/ui";

type Overview = Record<string, number>;
type Funnel = {
  leads: number; qualified: number; opportunities: number; viewings_attended: number;
  offers: number; reservations: number; deals_won: number; revenue: number;
  pipeline_value: number; conversion_rates: Record<string, number | null>;
};

export default function DashboardPage() {
  const { t, locale } = useI18n();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [funnel, setFunnel] = useState<Funnel | null>(null);

  useEffect(() => {
    api.get<Overview>("/analytics/overview").then(setOverview).catch(() => setOverview({}));
    api.get<Funnel>("/analytics/funnel").then(setFunnel).catch(() => setFunnel(null));
  }, []);

  if (overview === null) return <Spinner />;

  const rates = funnel?.conversion_rates || {};

  return (
    <div>
      <PageHeader title={t("nav_overview")} subtitle={t("app_tagline")} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label={t("active_leads")} value={overview.active_leads ?? 0} tone="emerald" />
        <StatCard label={t("unassigned_leads")} value={overview.unassigned_leads ?? 0} tone="amber" />
        <StatCard label={t("upcoming_viewings")} value={overview.upcoming_viewings ?? 0} tone="sky" />
        <StatCard label={t("open_tasks")} value={overview.open_tasks ?? 0} tone="violet" />
        <StatCard label={t("available_units")} value={overview.available_units ?? 0} tone="emerald" />
        <StatCard label={t("reserved_units")} value={overview.reserved_units ?? 0} tone="amber" />
        <StatCard label={t("contracted_units")} value={overview.contracted_units ?? 0} tone="sky" />
        <StatCard
          label={t("revenue")}
          value={new Intl.NumberFormat(locale === "ar" ? "ar-EG" : "en-US", {
            notation: "compact", maximumFractionDigits: 1,
          }).format(funnel?.revenue || 0)}
          tone="emerald"
        />
      </div>

      {funnel && (
        <Card className="mt-6 p-6">
          <h3 className="mb-4 font-bold text-ink">{t("funnel")}</h3>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {[
              ["lead_to_qualified", funnel.leads, funnel.qualified],
              ["qualified_to_viewing", funnel.qualified, funnel.viewings_attended],
              ["viewing_to_offer", funnel.viewings_attended, funnel.offers],
              ["offer_to_reservation", funnel.offers, funnel.reservations],
              ["reservation_to_deal", funnel.reservations, funnel.deals_won],
            ].map(([key, from, to]) => {
              const rate = from ? Math.round((Number(to) / Number(from)) * 100) : 0;
              return (
                <div key={String(key)} className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
                  <p className="text-xs font-medium text-slate-500">{t(key as never)}</p>
                  <p className="mt-1 text-2xl font-extrabold text-brand-700">{rate}%</p>
                  <p className="text-xs text-slate-400">{Number(to)} / {Number(from)}</p>
                </div>
              );
            })}
          </div>
          <div className="mt-4 flex flex-wrap gap-6 text-sm">
            <span className="text-slate-500">{t("pipeline_value")}: <b className="text-ink">{new Intl.NumberFormat(locale === "ar" ? "ar-EG" : "en-US").format(funnel.pipeline_value || 0)}</b></span>
            <span className="text-slate-500">{t("deals_title")}: <b className="text-ink">{funnel.deals_won} {t("won")}</b></span>
          </div>
        </Card>
      )}
    </div>
  );
}
