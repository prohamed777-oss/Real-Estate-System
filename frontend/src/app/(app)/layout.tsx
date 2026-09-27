"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useI18n, type Dict } from "@/lib/i18n";

type NavItem = { href: string; key: keyof Dict; icon: string };
type NavGroup = { titleKey: keyof Dict; items: NavItem[] };

const NAV: NavGroup[] = [
  {
    titleKey: "nav_group_crm",
    items: [
      { href: "/dashboard", key: "nav_overview", icon: "📊" },
      { href: "/inbox", key: "nav_inbox", icon: "💬" },
      { href: "/leads", key: "nav_leads", icon: "🎯" },
      { href: "/pipeline", key: "nav_pipeline", icon: "📈" },
      { href: "/viewings", key: "nav_viewings", icon: "📅" },
    ],
  },
  {
    titleKey: "nav_group_property",
    items: [
      { href: "/properties", key: "nav_properties", icon: "🏢" },
    ],
  },
  {
    titleKey: "nav_group_intelligence",
    items: [
      { href: "/deals", key: "nav_deals", icon: "🤝" },
      { href: "/analytics", key: "nav_analytics", icon: "🧠" },
    ],
  },
  {
    titleKey: "nav_group_system",
    items: [
      { href: "/settings", key: "nav_settings", icon: "⚙️" },
    ],
  },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { t, toggle, locale } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    const dev = localStorage.getItem("dev_email");
    if (!token && !dev) {
      router.replace("/login");
      return;
    }
    setEmail(localStorage.getItem("user_email") || dev || "");
    setReady(true);
  }, [router]);

  if (!ready) return null;

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col bg-ink text-slate-300">
        <div className="flex items-center gap-3 px-5 py-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600 text-lg text-white">🏙️</div>
          <div>
            <p className="text-sm font-bold text-white">{t("app_name")}</p>
            <p className="text-[11px] text-slate-400">{t("app_tagline")}</p>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 pb-4">
          {NAV.map((group) => (
            <div key={group.titleKey} className="mb-4">
              <p className="mb-1.5 px-3 text-[11px] font-bold uppercase tracking-wider text-slate-500">
                {t(group.titleKey)}
              </p>
              {group.items.map((item) => {
                const active = pathname === item.href ||
                  (item.href !== "/" && pathname.startsWith(item.href));
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`mb-0.5 flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition ${
                      active
                        ? "bg-brand-600/15 text-brand-100 ring-1 ring-brand-600/40"
                        : "hover:bg-white/5 hover:text-white"
                    }`}
                  >
                    <span className="text-base">{item.icon}</span>
                    {t(item.key)}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="border-t border-white/10 p-4">
          <p className="mb-2 truncate text-xs text-slate-400" dir="ltr">{email}</p>
          <div className="flex gap-2">
            <button
              onClick={toggle}
              className="flex-1 rounded-lg bg-white/5 px-2 py-1.5 text-xs font-semibold text-slate-300 hover:bg-white/10"
            >
              {t("language")}
            </button>
            <button
              onClick={() => {
                localStorage.clear();
                router.replace("/login");
              }}
              className="flex-1 rounded-lg bg-white/5 px-2 py-1.5 text-xs font-semibold text-rose-300 hover:bg-rose-500/20"
            >
              {t("logout")}
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-x-hidden p-8" dir={locale === "ar" ? "rtl" : "ltr"}>
        {children}
      </main>
    </div>
  );
}
