import Link from "next/link";
import ChatWidget from "@/components/ChatWidget";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-emerald-950 text-white">
      {/* Nav */}
      <nav className="flex items-center justify-between px-8 py-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500 text-xl">🏙️</div>
          <span className="text-lg font-bold">Real Estate Revenue OS</span>
        </div>
        <Link href="/login" className="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-400">
          تسجيل الدخول
        </Link>
      </nav>

      {/* Hero */}
      <section className="mx-auto max-w-6xl px-8 pb-20 pt-16 text-center">
        <h1 className="mx-auto max-w-3xl text-5xl font-extrabold leading-tight tracking-tight">
          نظام تشغيل الإيرادات
          <span className="block bg-gradient-to-r from-emerald-400 to-teal-300 bg-clip-text text-transparent">
            العقارية الذكي
          </span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-slate-300">
          منصة واحدة تدير عملاءك العقارية من أول رسالة واتساب لحد توقيع العقد —
          مدعومة بالذكاء الاصطناعي، آمنة بالكامل، ومبنية للتوسع.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-4">
          <Link href="/login" className="rounded-2xl bg-emerald-500 px-8 py-3.5 text-base font-bold text-white shadow-lg shadow-emerald-500/30 transition hover:bg-emerald-400">
            ابدأ الآن مجاناً
          </Link>
          <a href="#features" className="rounded-2xl border border-slate-600 px-8 py-3.5 text-base font-semibold text-slate-200 transition hover:bg-white/5">
            اعرف أكثر
          </a>
        </div>
      </section>

      {/* Features Grid */}
      <section id="features" className="mx-auto max-w-6xl px-8 pb-20">
        <h2 className="mb-12 text-center text-3xl font-bold">كل اللي تحتاجه في مكان واحد</h2>
        <div className="grid gap-6 md:grid-cols-3">
          {[
            { icon: "💬", title: "Unified Inbox", desc: "كل رسائل واتساب والموقع والإيميل في صندوق واحد — مع AI يرد تلقائياً" },
            { icon: "🎯", title: "Smart Leads", desc: "تأهيل ذكي، تقييم مبني على سلوك العميل، وتوجيه تلقائي لأفضل مندوب" },
            { icon: "🏠", title: "Property Inventory", desc: "إدارة عقارات ووحدات بحماية concurrency — مستحيل حجز مزدوج" },
            { icon: "📈", title: "Sales Pipeline", desc: "من أول تواصل لحجز الوحدة — كل مرحلة فيها state machine محكم" },
            { icon: "🧠", title: "AI Copilots", desc: "9 وكلاء ذكيين: استقبال، تأهيل، مطابقة، جدولة، متابعة، وإعادة تنشيط" },
            { icon: "📊", title: "Revenue Intelligence", desc: "اسأل بالعربي: 'مين العملاء اللي ميزانيتهم فوق 5 مليون؟' — والنظام يجاوب" },
          ].map((f) => (
            <div key={f.title} className="rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur transition hover:border-emerald-500/30 hover:bg-white/10">
              <span className="text-3xl">{f.icon}</span>
              <h3 className="mt-4 text-lg font-bold">{f.title}</h3>
              <p className="mt-2 text-sm text-slate-400">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Security */}
      <section className="mx-auto max-w-4xl px-8 pb-20 text-center">
        <h2 className="text-2xl font-bold">آمن من الأساس</h2>
        <div className="mt-8 grid grid-cols-2 gap-4 md:grid-cols-4">
          {[
            ["🔐", "Multi-tenant RLS"],
            ["⚡", "Concurrency-safe"],
            ["📋", "Full audit trail"],
            ["🤖", "AI trust filter"],
          ].map(([icon, label]) => (
            <div key={label} className="rounded-xl border border-white/10 bg-white/5 p-4">
              <span className="text-2xl">{icon}</span>
              <p className="mt-2 text-xs font-semibold text-slate-300">{label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/10 py-8 text-center text-sm text-slate-500">
        Real Estate Revenue OS — Built with FastAPI + Next.js + Supabase
      </footer>

      {/* Chat Widget — the AI Reception Agent talks to visitors */}
      <ChatWidget tenantSlug="prod-realty" />
    </div>
  );
}
