"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, EmptyState, PageHeader, Spinner, Table, Td, Th, stageTone } from "@/components/ui";

type Viewing = {
  id: string; asset_id: string; opportunity_id: string; scheduled_at: string;
  status: string; salesperson_id?: string | null; display_timezone: string;
};

export default function ViewingsPage() {
  const { t, locale } = useI18n();
  const [viewings, setViewings] = useState<Viewing[] | null>(null);

  const load = useCallback(() => {
    api.get<Viewing[]>("/viewings").then(setViewings).catch(() => setViewings([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function transition(v: Viewing, event: string) {
    await api.post(`/viewings/${v.id}/transition`, { event });
    load();
  }

  return (
    <div>
      <PageHeader title={t("viewings_title")} subtitle="الجدولة timezone-aware وحماية تعارض المواعيد مفعّلة" />
      {viewings === null ? <Spinner /> : viewings.length === 0 ? (
        <EmptyState icon="📅" title="لا توجد معاينات — احجزها من فرصة البيع أو وكيل الجدولة" />
      ) : (
        <Table head={
          <>
            <Th>{t("scheduled_at")}</Th>
            <Th>{t("status")}</Th>
            <Th>الوحدة</Th>
            <Th>{t("actions")}</Th>
          </>
        }>
          {viewings.map((v) => (
            <tr key={v.id} className="hover:bg-slate-50">
              <Td>
                <p className="font-semibold">{fmtDate(v.scheduled_at, locale)}</p>
                <p className="text-xs text-slate-400">{v.display_timezone}</p>
              </Td>
              <Td><Badge tone={stageTone(v.status)}>{v.status}</Badge></Td>
              <Td className="text-xs">#{v.asset_id.slice(0, 8)}</Td>
              <Td>
                <div className="flex flex-wrap gap-1.5">
                  {v.status === "REQUESTED" && (
                    <Button variant="outline" onClick={() => transition(v, "confirm")}>{t("confirm")}</Button>
                  )}
                  {v.status === "CONFIRMED" && (
                    <>
                      <Button onClick={() => transition(v, "attend")}>حضر</Button>
                      <Button variant="ghost" onClick={() => transition(v, "no_show")}>{t("no_show")}</Button>
                    </>
                  )}
                  {v.status === "ATTENDED" && (
                    <Button onClick={() => transition(v, "complete", )}>{t("complete")}</Button>
                  )}
                  {["REQUESTED", "CONFIRMED"].includes(v.status) && (
                    <Button variant="ghost" onClick={() => transition(v, "cancel")}>{t("cancel")}</Button>
                  )}
                </div>
              </Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}
