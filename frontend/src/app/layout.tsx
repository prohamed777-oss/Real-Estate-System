import type { Metadata } from "next";
import { Cairo, Inter } from "next/font/google";
import { I18nProvider } from "@/lib/i18n";
import "./globals.css";

const cairo = Cairo({ subsets: ["arabic", "latin"], variable: "--font-cairo" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Real Estate Revenue OS — نظام إيرادات العقارات",
  description: "Multi-tenant, AI-native real estate revenue operating system",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl" className={`${cairo.variable} ${inter.variable}`}>
      <body className="antialiased">
        <I18nProvider>{children}</I18nProvider>
      </body>
    </html>
  );
}
