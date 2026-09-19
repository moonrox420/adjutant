import type { Metadata } from "next";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/libre-caslon-display/400.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Adjutant · Campaign operations",
  description: "Your campaign operation, with every decision accounted for.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
