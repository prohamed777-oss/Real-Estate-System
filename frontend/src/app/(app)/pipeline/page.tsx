"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, EmptyState, PageHeader, Spinner, stageTone } from "@/components/ui";
import { Card } from "@/components/ui";

type Opportunity = {
  id: string; stage: string; estimated_value?: string | null; currency: string;
  probability: number; person_id: string; created_at: string; closed_at?: string | null;
};
type Asset = { id: string; title: string };

const STAGE_ORDER = ["DISCOVERY", "QUALIFIED", "MATCHED", "VIEWING", "OFFER", "NEGOTIATION", "RESERVATION", "WON"];

export default function PipelinePage() {
  const { t } = useI18n();
  const [opps, setOpps] = useState<Opportunity[] | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);

  const load = useCallback(() => {
    api.get<Opportunity[]>("/opportunities?limit=100").then(setOpps).catch(() => setOpps([]));
    api.get<{ items: Asset[] }>("/properties/assets?limit=50")
      .then((r) => setAssets(r.items)).catch(() => setAssets([]));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function advance(opp: Opportunity, event: string) {
    await api.post(`/opportunities/${opp.id}/transition`, { event });
    load();
  }

  const nextEvent = (stage: string): [string, string] | null =>
    stage === "DISCOVERY" ? ["qualify", "تأهيل"] :
    stage === "QUALIFIED" ? ["match", "مطابقة"] :
    stage === "MATCHED" ? ["view", "معاينة"] :
    stage === "VIEWING" ? ["offer", "عرض"] :
    stage === "OFFER" ? ["negotiate", "تفاوض"] :
    stage === "NEGOTIATION" ? ["reserve", "حجز"] :
    stage === "RESERVATION" ? ["win", "فوز"] : null;

  return (
    <div>
      <PageHeader title={t("pipeline_title")} subtitle="Lead → QUALIFIED → MATCHED → VIEWING → OFFER → NEGOTIATION → RESERVATION → WON" />
      {opps === null ? <Spinner /> : opps.length === 0 ? (
        <EmptyState icon="📈" title="لا توجد فرص — حوّل عميل مؤهل من صفحة العملاء" />
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-4">
          {STAGE_ORDER.map((stage) => {
            const columnOpps = opps.filter((o) => o.stage === stage);
            return (
              <div key={stage} className="w-64 shrink-0">
                <div className="mb-2 flex items-center justify-between px-1">
                  <Badge tone={stageTone(stage)}>{stage}</Badge>
                  <span className="text-xs font-bold text-slate-400">{columnOpps.length}</span>
                </div>
                <div className="space-y-2">
                  {columnOpps.map((opp) => {
                    const next = nextEvent(opp.stage);
                    return (
                      <Card key={opp.id} className="p-4 fade-up">
                        <p className="text-xs text-slate-400">{t("opportunity")} #{opp.id.slice(0, 8)}</p>
                        {opp.estimated_value && (
                          <p className="mt-1 font-bold text-ink">{fmtMoney(opp.estimated_value, opp.currency)}</p>
                        )}
                        <p className="mt-1 text-xs text-slate-400">🎯 {opp.probability}% · {fmtDate(opp.created_at)}</p>
                        {next && (
                          <Button variant="outline" className="mt-2 w-full justify-center" onClick={() => advance(opp, next[0])}>
                            {next[1]} →
                          </Button>
                        )}
                      </Card>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
