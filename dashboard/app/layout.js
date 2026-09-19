import "./globals.css";

export const metadata = { title: "Outbound CertiK — Dashboard" };

export default function RootLayout({ children }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
