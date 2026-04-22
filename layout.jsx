import "./globals.css";

export const metadata = {
  title: "WildColor: Animal Coloring Book",
  description: "No API key smart animal coloring app",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
