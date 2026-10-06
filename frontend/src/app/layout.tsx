import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import "@xyflow/react/dist/style.css";
import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/components/ui/overlay";

export const metadata: Metadata = {
  title: { default: "FORGE", template: "%s · FORGE" },
  description: "Don't trust an agent. Verify the workflow. Typed, observable, permission-controlled agent workflows on Nebius + NVIDIA Nemotron.",
  icons: { icon: "/icon.svg" },
};

// Static, first-party script: applies the saved theme before paint to avoid a flash.
const THEME_BOOT = `try{var t=localStorage.getItem("forge.theme");document.documentElement.setAttribute("data-theme",t==="light"?"light":"dark")}catch(e){document.documentElement.setAttribute("data-theme","dark")}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <style>{`:root{--font-sans:var(--font-geist-sans);--font-mono:var(--font-geist-mono)}`}</style>
        <AuthProvider>
          <ToastProvider>{children}</ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
