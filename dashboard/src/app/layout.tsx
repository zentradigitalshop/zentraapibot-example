import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Zentra API Bot — Admin",
  description: "Admin dashboard for the Zentra API starter bot.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
