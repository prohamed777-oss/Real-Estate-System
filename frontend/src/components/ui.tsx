"use client";

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,.04)] ${className}`}>
      {children}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

const STAT_COLORS: Record<string, string> = {
  emerald: "bg-brand-50 text-brand-700",
  amber: "bg-amber-50 text-amber-700",
  sky: "bg-sky-50 text-sky-700",
  rose: "bg-rose-50 text-rose-700",
  violet: "bg-violet-50 text-violet-700",
};

export function StatCard({ label, value, tone = "emerald", hint }: {
  label: string; value: string | number; tone?: keyof typeof STAT_COLORS; hint?: string;
}) {
  return (
    <Card className="p-5 fade-up">
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <span className={`rounded-lg px-2 py-1 text-xs font-semibold ${STAT_COLORS[tone]}`}>●</span>
      </div>
      <p className="mt-2 text-3xl font-extrabold tracking-tight text-ink">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </Card>
  );
}

const BADGE_TONES: Record<string, string> = {
  gray: "bg-slate-100 text-slate-600",
  green: "bg-brand-100 text-brand-700",
  amber: "bg-amber-100 text-amber-700",
  red: "bg-rose-100 text-rose-700",
  blue: "bg-sky-100 text-sky-700",
  violet: "bg-violet-100 text-violet-700",
};

export function Badge({ children, tone = "gray" }: { children: React.ReactNode; tone?: keyof typeof BADGE_TONES }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${BADGE_TONES[tone]}`}>
      {children}
    </span>
  );
}

export function stageTone(stage: string): keyof typeof BADGE_TONES {
  if (["WON", "CONVERTED", "COMPLETED", "ACCEPTED", "SOLD", "CONTRACTED", "active"].includes(stage)) return "green";
  if (["LOST", "DISQUALIFIED", "failed", "REJECTED", "dead"].includes(stage)) return "red";
  if (["RESERVATION", "RESERVED", "negotiation", "HELD"].includes(stage)) return "amber";
  if (["VIEWING", "viewing", "SENT", "pending"].includes(stage)) return "blue";
  return "gray";
}

export function Button({ children, variant = "primary", className = "", ...props }: {
  children: React.ReactNode; variant?: "primary" | "ghost" | "outline" | "danger";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const styles = {
    primary: "bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50",
    ghost: "text-slate-600 hover:bg-slate-100",
    outline: "border border-slate-300 text-ink hover:bg-slate-50",
    danger: "bg-rose-600 text-white hover:bg-rose-700",
  }[variant];
  return (
    <button
      className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-semibold transition disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function EmptyState({ icon = "📭", title, hint }: { icon?: string; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <span className="text-4xl">{icon}</span>
      <p className="font-semibold text-slate-600">{title}</p>
      {hint && <p className="max-w-sm text-sm text-slate-400">{hint}</p>}
    </div>
  );
}

export function Spinner() {
  return (
    <div className="flex justify-center py-16">
      <div className="h-8 w-8 animate-spin rounded-full border-3 border-slate-200 border-t-brand-600" />
    </div>
  );
}

export function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`px-4 py-3 text-start text-xs font-semibold uppercase tracking-wide text-slate-400 ${className}`}>{children}</th>;
}

export function Td({ children, className = "", ...props }: {
  children: React.ReactNode; className?: string;
} & React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={`px-4 py-3 text-sm text-ink ${className}`} {...props}>{children}</td>;
}

export function Table({ head, children }: { head: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[640px]">
        <thead className="border-b border-slate-100 bg-slate-50/60"><tr>{head}</tr></thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </Card>
  );
}

export function Modal({ open, onClose, title, children }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl fade-up" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-bold">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mb-3 block">
      <span className="mb-1.5 block text-sm font-medium text-slate-600">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none transition focus:border-brand-600 focus:ring-2 focus:ring-brand-100";
