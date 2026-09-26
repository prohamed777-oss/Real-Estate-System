"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useI18n } from "@/lib/i18n";
import { Button, Card, Field, inputClass } from "@/components/ui";

export default function LoginPage() {
  const { t, toggle, locale } = useI18n();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [isSignup, setIsSignup] = useState(false);
  const [tenantName, setTenantName] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
      if (supabaseUrl && password) {
        // Production path: Supabase Auth, then backend bootstrap/verification
        const { createClient } = await import("@supabase/supabase-js");
        const supabase = createClient(
          supabaseUrl,
          process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "",
        );
        if (isSignup) {
          // Signup → provision the tenant structure server-side → sign in
          const { data: suData, error: suError } = await supabase.auth.signUp({
            email, password,
            options: { data: { full_name: tenantName } },
          });
          if (suError || !suData.session) throw new Error("auth");
          localStorage.setItem("access_token", suData.session.access_token);
          const res = await fetch("/backend/api/v1/auth/bootstrap", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${suData.session.access_token}`,
            },
            body: JSON.stringify({ tenant_name: tenantName || email }),
          });
          if (!res.ok) throw new Error("bootstrap");
        } else {
          const { data, error } = await supabase.auth.signInWithPassword({ email, password });
          if (error || !data.session) throw new Error("auth");
          localStorage.setItem("access_token", data.session.access_token);
        }
      } else {
        // Dev path: X-Dev-Email against a provisioned tenant user
        localStorage.setItem("dev_email", email);
        const res = await fetch("/backend/api/v1/auth/me", {
          headers: { "X-Dev-Email": email },
        });
        if (!res.ok) throw new Error("auth");
      }
      localStorage.setItem("user_email", email);
      router.push("/");
    } catch {
      setError(t("signin_error"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-ink via-ink-soft to-brand-900 p-4">
      <Card className="w-full max-w-md p-8 fade-up">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600 text-2xl text-white shadow-lg shadow-brand-600/30">
            🏙️
          </div>
          <h1 className="text-xl font-extrabold text-ink">{t("app_name")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("app_tagline")}</p>
        </div>
        <div className="mb-4 flex text-sm">
          <button type="button" onClick={() => setIsSignup(false)}
            className={`flex-1 rounded-l-xl py-2 font-semibold transition ${!isSignup ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500"}`}>
            {t("login")}
          </button>
          <button type="button" onClick={() => setIsSignup(true)}
            className={`flex-1 rounded-r-xl py-2 font-semibold transition ${isSignup ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500"}`}>
            حساب جديد
          </button>
        </div>
        <form onSubmit={submit}>
          {isSignup && (
            <Field label="اسم الشركة / Company">
              <input className={inputClass} value={tenantName}
                onChange={(e) => setTenantName(e.target.value)} />
            </Field>
          )}
          <Field label={t("email")}>
            <input
              type="email" required value={email} dir="ltr"
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass} placeholder="owner@company.com"
            />
          </Field>
          <Field label={`${t("password")} (${locale === "ar" ? "اختياري في وضع التطوير" : "optional in dev mode"})`}>
            <input
              type="password" value={password} dir="ltr"
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass} placeholder="••••••••"
            />
          </Field>
          {error && <p className="mb-3 text-sm font-medium text-rose-600">{error}</p>}
          <Button type="submit" disabled={loading} className="w-full justify-center">
            {loading ? t("loading") : t("login")}
          </Button>
        </form>
        <p className="mt-4 text-center text-xs text-slate-400">{t("dev_login_hint")}</p>
        <div className="mt-6 border-t border-slate-100 pt-4 text-center">
          <button onClick={toggle} className="text-sm font-semibold text-brand-600 hover:text-brand-700">
            {t("language")}
          </button>
        </div>
      </Card>
    </div>
  );
}
