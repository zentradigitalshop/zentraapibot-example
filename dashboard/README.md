# Admin Dashboard (Phase 4)

Not built yet — see the phase table in the repository root `README.md`.

When it lands, it will be a Next.js app deployed to Vercel, same shape as
[ZentraShopBot's own admin dashboard](https://github.com/snackshell/zentrashopbot-admin):
server components reading the same PostgreSQL database this bot writes to,
a single-password login (no separate user system), and every page a plain
`<form action={...}>` so nothing here needs client-side JavaScript to work.

Planned pages: Overview, Settings (markup + rail toggles), Orders, Customers,
Credit by hand.
