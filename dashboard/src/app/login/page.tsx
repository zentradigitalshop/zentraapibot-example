const ERRORS: Record<string, string> = {
  wrong: "Wrong password.",
  throttled: "Too many attempts — wait a few minutes and try again.",
  unconfigured: "ADMIN_PASSWORD is not set in this deployment's environment.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>Zentra API Bot</h1>
        <p>Sign in to manage your shop.</p>
        {error && <div className="notice notice-error">{ERRORS[error] ?? "Something went wrong."}</div>}
        <form action="/api/auth/login" method="post">
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoFocus required />
          </div>
          <button type="submit">Sign in</button>
        </form>
      </div>
    </div>
  );
}
