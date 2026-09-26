"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, EmptyState, PageHeader, Spinner, Table, Td, Th, stageTone } from "@/components/ui";

type Deal = {
  id: string; opportunity_id: string; asset_id?: string | null; status: string;
  gross_value?: string | null; currency: string; closed_at?: string | null; created_at: string;
};

export default function DealsPage() {
  const { t, locale } = useI18n();
  const [deals, setDeals] = useState<Deal[] | null>(null);

  const load = useCallback(() => {
    api.get<Deal[]>("/deals").then(setDeals).catch(() => setDeals([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function close(deal: Deal, event: string) {
    await api.post(`/deals/${deal.id}/close`, { event });
    load();
  }

  const totalWon = (deals || []).filter((d) => d.status === "WON")
    .reduce((sum, d) => sum + Number(d.gross_value || 0), 0);

  return (
    <div>
      <PageHeader
        title={t("deals_title")}
        subtitle={`${t("revenue")}: ${fmtMoney(totalWon)} · العمولات تُحسب آليًا بعد الإغلاق`}
      />
      {deals === null ? <Spinner /> : deals.length === 0 ? (
        <EmptyState icon="🤝" title="لا توجد صفقات — أغلق فرصة بيع كـWON" />
      ) : (
        <Table head={
          <>
            <Th>{t("deals_title")}</Th>
            <Th>{t("status")}</Th>
            <Th>{t("gross_value")}</Th>
            <Th>{t("created")}</Th>
            <Th>{t("actions")}</Th>
          </>
        }>
          {deals.map((d) => (
            <tr key={d.id} className="hover:bg-slate-50">
              <Td><p className="font-semibold">#{d.id.slice(0, 8)}</p></Td>
              <Td><Badge tone={stageTone(d.status)}>{d.status}</Badge></Td>
              <Td><b>{fmtMoney(d.gross_value, d.currency)}</b></Td>
              <Td className="text-xs text-slate-400">{fmtDate(d.created_at, locale)}</Td>
              <Td>
                {d.status === "OPEN" && (
                  <div className="flex gap-1.5">
                    <Button onClick={() => close(d, "contract")}>توقيع العقد</Button>
                    <Button variant="outline" onClick={() => close(d, "win")}>{t("close_won")}</Button>
                    <Button variant="ghost" onClick={() => close(d, "lose")}>{t("lost")}</Button>
                  </div>
                )}
              </Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}
