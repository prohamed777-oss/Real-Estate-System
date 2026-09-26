"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  Badge, Button, EmptyState, Field, Modal, PageHeader, Spinner, Table, Td, Th, inputClass, stageTone,
} from "@/components/ui";

type Asset = {
  id: string; title: string; asset_type: string; property_type: string;
  bedrooms?: number | null; bathrooms?: number | null; area_value?: string | null;
  location: Record<string, string>; finishing?: string | null;
  delivery_status?: string | null; project_id?: string | null;
};
type InventoryState = Record<string, { state: string; confidence: string }>;

export default function PropertiesPage() {
  const { t } = useI18n();
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [availability, setAvailability] = useState<InventoryState>({});
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    title: "", property_type: "apartment", bedrooms: "3", bathrooms: "2",
    area_value: "120", city: "القاهرة", area: "التجمع الخامس", price: "",
  });

  const load = useCallback(async () => {
    const r = await api.get<{ items: Asset[] }>("/assets?limit=100");
    setAssets(r.items);
    if (r.items.length) {
      const ids = r.items.map((a) => a.id).join(",");
      const avail = await api.get<InventoryState>(`/inventory/availability?asset_ids=${ids}`);
      setAvailability(avail);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function createProperty() {
    const asset = await api.post<{ id: string }>("/assets", {
      title: form.title, property_type: form.property_type,
      bedrooms: Number(form.bedrooms) || null, bathrooms: Number(form.bathrooms) || null,
      area_value: form.area_value || null,
      location: { city: form.city, area: form.area },
    });
    if (form.price) {
      await api.post(`/properties/assets/${asset.id}/price`, { amount: Number(form.price) });
    }
    setCreating(false);
    load();
  }

  async function hold(assetId: string) {
    await api.post(`/inventory/${assetId}/hold`);
    load();
  }
  async function release(assetId: string) {
    await api.post(`/inventory/${assetId}/release`);
    load();
  }

  const stateTone = (state?: string) =>
    state === "AVAILABLE" ? "green" : state === "HELD" ? "amber" :
    state === "RESERVED" ? "blue" : state === "CONTRACTED" ? "violet" :
    state === "SOLD" ? "red" : "gray";

  return (
    <div>
      <PageHeader
        title={t("properties_title")}
        actions={<Button onClick={() => setCreating(true)}>＋ {t("new_property")}</Button>}
      />
      {assets === null ? (
        <Spinner />
      ) : assets.length === 0 ? (
        <EmptyState icon="🏢" title={t("no_properties")} />
      ) : (
        <Table head={
          <>
            <Th>{t("properties_title")}</Th>
            <Th>{t("property_type")}</Th>
            <Th>{t("bedrooms")}</Th>
            <Th>{t("price")}</Th>
            <Th>{t("state")}</Th>
            <Th>{t("actions")}</Th>
          </>
        }>
          {assets.map((a) => {
            const inv = availability[a.id];
            return (
              <tr key={a.id} className="hover:bg-slate-50">
                <Td>
                  <p className="font-semibold">{a.title}</p>
                  <p className="text-xs text-slate-400">
                    {a.location?.city} · {a.location?.area} · {a.area_value ? `${a.area_value} م²` : ""}
                  </p>
                </Td>
                <Td><Badge>{a.property_type}</Badge></Td>
                <Td>{a.bedrooms ?? "—"}</Td>
                <Td>{fmtMoney(null)}{/* per-asset price fetched in detail view */}</Td>
                <Td>
                  <Badge tone={stateTone(inv?.state)}>
                    {inv?.state === "AVAILABLE" ? t("available") :
                      inv?.state === "HELD" ? t("held") :
                      inv?.state === "RESERVED" ? t("reserved") :
                      inv?.state === "CONTRACTED" ? t("contracted") :
                      inv?.state === "SOLD" ? t("sold") : (inv?.state || "—")}
                  </Badge>
                </Td>
                <Td>
                  {inv?.state === "AVAILABLE" && (
                    <Button variant="outline" onClick={() => hold(a.id)}>🔒 {t("hold")}</Button>
                  )}
                  {inv?.state === "HELD" && (
                    <Button variant="ghost" onClick={() => release(a.id)}>🔓 {t("release")}</Button>
                  )}
                </Td>
              </tr>
            );
          })}
        </Table>
      )}

      <Modal open={creating} onClose={() => setCreating(false)} title={t("new_property")}>
        <Field label="العنوان / Title">
          <input className={inputClass} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("property_type")}>
            <select className={inputClass} value={form.property_type} onChange={(e) => setForm({ ...form, property_type: e.target.value })}>
              <option value="apartment">شقة</option><option value="villa">فيلا</option>
              <option value="duplex">دوبلكس</option><option value="studio">استوديو</option>
              <option value="office">مكتب</option><option value="shop">محل</option>
              <option value="land">أرض</option><option value="chalet">شاليه</option>
            </select>
          </Field>
          <Field label={t("bedrooms")}>
            <input type="number" className={inputClass} value={form.bedrooms} onChange={(e) => setForm({ ...form, bedrooms: e.target.value })} />
          </Field>
          <Field label="المساحة (م²)">
            <input className={inputClass} value={form.area_value} onChange={(e) => setForm({ ...form, area_value: e.target.value })} />
          </Field>
          <Field label={t("price")}>
            <input className={inputClass} dir="ltr" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} placeholder="4000000" />
          </Field>
          <Field label="المدينة">
            <input className={inputClass} value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
          </Field>
          <Field label={t("area")}>
            <input className={inputClass} value={form.area} onChange={(e) => setForm({ ...form, area: e.target.value })} />
          </Field>
        </div>
        <Button onClick={createProperty} className="w-full justify-center" disabled={!form.title}>
          {t("save")}
        </Button>
      </Modal>
    </div>
  );
}
