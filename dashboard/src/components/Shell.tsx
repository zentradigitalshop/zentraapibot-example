import Nav from "./Nav";

export default function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <Nav />
      <main className="main">{children}</main>
    </div>
  );
}
