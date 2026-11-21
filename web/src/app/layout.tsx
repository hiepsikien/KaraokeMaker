import type { Metadata } from "next";
import { Be_Vietnam_Pro } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const sans = Be_Vietnam_Pro({
  subsets: ["vietnamese", "latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "KaraokeMaker",
  description: "Ghép lời, ảnh và nhạc thành video karaoke.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body className={sans.className}>
        <nav className="mx-auto flex max-w-[96rem] items-center gap-2 px-5 pt-6 text-sm sm:px-8">
          <Link
            href="/"
            className="rounded-full border border-[var(--line)] px-3 py-1.5 text-[var(--muted)] transition hover:border-[var(--gold)] hover:text-white"
          >
            Karaoke
          </Link>
          <Link
            href="/studio"
            className="rounded-full border border-[var(--line)] px-3 py-1.5 text-[var(--muted)] transition hover:border-[var(--gold)] hover:text-white"
          >
            AI Studio
          </Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
