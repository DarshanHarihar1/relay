import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Relay",
  description: "A bounded, auditable repair plan.",
  manifest: "/manifest.webmanifest",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
