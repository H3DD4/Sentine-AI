import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Logo } from "@/components/brand/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { authLogin, authRegister } from "@/lib/auth";
import { useState, useEffect } from "react";
import {
  ShieldCheck,
  Eye,
  EyeOff,
  AlertCircle,
  ArrowRight,
  User,
  Mail,
  Lock,
  Activity,
  Database,
  Radar,
} from "lucide-react";

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

type Tab = "login" | "register";

function LoginPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("login");

  // Login state
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginShowPassword, setLoginShowPassword] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  // Register state
  const [regUsername, setRegUsername] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regConfirm, setRegConfirm] = useState("");
  const [regShowPassword, setRegShowPassword] = useState(false);
  const [regLoading, setRegLoading] = useState(false);
  const [regError, setRegError] = useState<string | null>(null);
  const [regSuccess, setRegSuccess] = useState(false);

  // Clear errors when switching tabs
  useEffect(() => {
    setLoginError(null);
    setRegError(null);
    setRegSuccess(false);
  }, [tab]);

  // ── Login ────────────────────────────────────────────────────────────────
  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoginError(null);

    if (!loginEmail.trim() || !loginPassword) {
      setLoginError("Please fill in all fields.");
      return;
    }

    setLoginLoading(true);
    try {
      await authLogin(loginEmail.trim(), loginPassword);
      navigate({ to: "/" });
    } catch (err: unknown) {
      setLoginError(err instanceof Error ? err.message : "Login failed. Please try again.");
    } finally {
      setLoginLoading(false);
    }
  }

  // ── Register ─────────────────────────────────────────────────────────────
  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setRegError(null);
    setRegSuccess(false);

    if (!regUsername.trim() || !regEmail.trim() || !regPassword) {
      setRegError("Please fill in all fields.");
      return;
    }
    if (regPassword !== regConfirm) {
      setRegError("Passwords do not match.");
      return;
    }
    if (regPassword.length < 8) {
      setRegError("Password must be at least 8 characters.");
      return;
    }

    setRegLoading(true);
    try {
      await authRegister(regUsername.trim(), regEmail.trim(), regPassword);
      await authLogin(regEmail.trim(), regPassword);
      setRegSuccess(true);
      navigate({ to: "/" });
    } catch (err: unknown) {
      setRegError(err instanceof Error ? err.message : "Registration failed. Please try again.");
    } finally {
      setRegLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen bg-white">
      {/* ── Left panel: branding ──────────────────────────────────────────── */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden border-r border-border bg-[#f7f9fc] p-12 lg:flex xl:p-16">
        <div className="pointer-events-none absolute inset-0 login-soft-field" />
        <div className="pointer-events-none absolute inset-0 login-fine-grid opacity-45" />
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <svg
            viewBox="0 0 900 900"
            preserveAspectRatio="none"
            className="h-full w-full"
            aria-hidden="true"
          >
            <defs>
              <linearGradient id="line-primary" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stopColor="#171c8f" stopOpacity="0" />
                <stop offset="0.42" stopColor="#171c8f" stopOpacity="0.32" />
                <stop offset="1" stopColor="#0072ce" stopOpacity="0.06" />
              </linearGradient>
              <linearGradient id="line-secondary" x1="0" y1="1" x2="1" y2="0">
                <stop offset="0" stopColor="#0072ce" stopOpacity="0.05" />
                <stop offset="0.55" stopColor="#25b5e6" stopOpacity="0.42" />
                <stop offset="1" stopColor="#25b5e6" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path
              className="login-flow-line login-flow-line-a"
              d="M-70 690 C140 680 120 450 330 468 S550 650 760 385 S930 185 1010 210"
              fill="none"
              stroke="url(#line-primary)"
              strokeWidth="3"
              pathLength="1"
            />
            <path
              className="login-flow-line login-flow-line-b"
              d="M-100 755 C160 610 235 785 410 610 S665 280 990 345"
              fill="none"
              stroke="url(#line-secondary)"
              strokeWidth="2"
              pathLength="1"
            />
            <path
              className="login-flow-line login-flow-line-c"
              d="M-80 235 C190 320 260 125 490 225 S710 535 1010 118"
              fill="none"
              stroke="#171c8f"
              strokeOpacity="0.11"
              strokeWidth="1.5"
              pathLength="1"
            />
          </svg>
        </div>

        {/* Logo */}
        <div className="relative z-10">
          <div className="flex items-center gap-3">
            <img
              src="/favicon.png"
              alt=""
              className="h-9 w-9 object-contain opacity-90"
              onError={(event) => {
                event.currentTarget.style.display = "none";
              }}
            />
            <div className="flex flex-col border-l border-brand-navy/20 pl-3 leading-tight">
              <span className="text-sm font-semibold text-brand-navy">Sentinel AI</span>
              <span className="text-[11px] text-muted-foreground">Security assistant</span>
            </div>
          </div>
        </div>

        {/* Hero text */}
        <div className="relative z-10 max-w-xl space-y-7 pb-16">
          <div className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-widest text-brand-cyan">
              Forvis Mazars · Internal
            </div>
            <h1 className="text-[2.75rem] font-light leading-[1.08] text-brand-navy xl:text-[3.4rem]">
              Evidence in.
              <br />
              Defensible findings out.
            </h1>
            <p className="max-w-md text-[15px] leading-relaxed text-muted-foreground">
              A controlled analysis surface for validating findings against live security knowledge,
              preserving source provenance, and preparing report-ready evidence.
            </p>
          </div>

          <div className="grid grid-cols-3 border-y border-brand-navy/10 bg-white/45 backdrop-blur-sm">
            {[
              { label: "Knowledge", value: "6 sources", Icon: Database },
              { label: "Analysis", value: "Provenance", Icon: Radar },
              { label: "Output", value: "DOCX", Icon: Activity },
            ].map(({ label, value, Icon }) => (
              <div key={label} className="border-r border-brand-navy/10 px-3 py-4 last:border-r-0">
                <Icon className="mb-2 h-4 w-4 text-brand-cyan" strokeWidth={1.8} />
                <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  {label}
                </div>
                <div className="mt-1 text-sm font-semibold text-brand-navy">{value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer note */}
        <div className="relative z-10 text-[11px] text-muted-foreground">
          Internal security workspace · v2.4.1
        </div>
      </div>

      {/* ── Right panel: form ─────────────────────────────────────────────── */}
      <div className="relative flex min-h-screen flex-1 flex-col items-center justify-center bg-white px-5 py-10 sm:px-10 lg:w-1/2 lg:px-14 xl:px-20">
        <div className="pointer-events-none absolute left-0 top-1/2 hidden h-48 w-px -translate-y-1/2 bg-gradient-to-b from-transparent via-brand-cyan/35 to-transparent lg:block" />
        {/* Mobile logo */}
        <div className="mb-8 lg:hidden">
          <Logo className="gap-4" imageClassName="h-12 sm:h-14" textClassName="text-sm" />
        </div>

        <div className="w-full max-w-[420px]">
          <div className="mb-7 lg:hidden">
            <div className="mb-3 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-brand-cyan">
              <span className="h-1.5 w-1.5 rounded-full bg-brand-cyan" /> Internal security
              workspace
            </div>
            <h1 className="text-2xl font-light text-brand-navy">
              Evidence in. Defensible findings out.
            </h1>
          </div>
          {/* Tab switcher */}
          <div
            className="mb-8 flex border-b border-border"
            role="tablist"
            aria-label="Authentication mode"
          >
            {(["login", "register"] as Tab[]).map((t) => (
              <button
                key={t}
                id={`auth-tab-${t}`}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn(
                  "relative flex-1 py-3 text-sm font-semibold transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  tab === t
                    ? "text-brand-navy after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-brand-cyan"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          {/* Panel */}
          <div className="bg-white">
            {/* ── Login form ───────────────────────────────────────────── */}
            {tab === "login" && (
              <form onSubmit={handleLogin} noValidate>
                <div className="px-6 pt-6 pb-4 space-y-1 border-b border-border">
                  <h2 className="text-lg font-semibold text-foreground">Welcome back</h2>
                  <p className="text-sm text-muted-foreground">
                    Sign in to your analyst account to continue.
                  </p>
                </div>

                <div className="px-6 py-5 space-y-4">
                  {loginError && (
                    <div className="flex items-start gap-2.5 rounded-sm border border-destructive/25 bg-destructive/8 px-3 py-2.5">
                      <AlertCircle
                        className="h-4 w-4 text-destructive shrink-0 mt-0.5"
                        strokeWidth={2}
                      />
                      <p className="text-sm text-destructive leading-snug">{loginError}</p>
                    </div>
                  )}

                  <div className="space-y-1.5">
                    <Label htmlFor="login-email" className="text-sm font-medium text-foreground">
                      Email address
                    </Label>
                    <div className="relative">
                      <Mail
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
                        strokeWidth={1.8}
                      />
                      <Input
                        id="login-email"
                        type="email"
                        autoComplete="email"
                        placeholder="analyst@forvismazars.com"
                        value={loginEmail}
                        onChange={(e) => setLoginEmail(e.target.value)}
                        className="pl-9 h-10"
                        disabled={loginLoading}
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="login-password" className="text-sm font-medium text-foreground">
                      Password
                    </Label>
                    <div className="relative">
                      <Lock
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
                        strokeWidth={1.8}
                      />
                      <Input
                        id="login-password"
                        type={loginShowPassword ? "text" : "password"}
                        autoComplete="current-password"
                        placeholder="••••••••"
                        value={loginPassword}
                        onChange={(e) => setLoginPassword(e.target.value)}
                        className="pl-9 pr-10 h-10"
                        disabled={loginLoading}
                        required
                      />
                      <button
                        type="button"
                        tabIndex={-1}
                        aria-label={loginShowPassword ? "Hide password" : "Show password"}
                        onClick={() => setLoginShowPassword((v) => !v)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {loginShowPassword ? (
                          <EyeOff className="h-4 w-4" strokeWidth={1.8} />
                        ) : (
                          <Eye className="h-4 w-4" strokeWidth={1.8} />
                        )}
                      </button>
                    </div>
                  </div>
                </div>

                <div className="px-6 pb-6">
                  <Button
                    id="login-submit"
                    type="submit"
                    className="w-full h-10 gap-2"
                    disabled={loginLoading}
                  >
                    {loginLoading ? (
                      <>
                        <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                        Signing in…
                      </>
                    ) : (
                      <>
                        Sign in
                        <ArrowRight className="h-4 w-4" strokeWidth={2} />
                      </>
                    )}
                  </Button>

                  <p className="mt-4 text-center text-[13px] text-muted-foreground">
                    Don't have an account?{" "}
                    <button
                      type="button"
                      onClick={() => setTab("register")}
                      className="font-semibold text-brand-navy hover:text-brand-cyan transition-colors underline-offset-2 hover:underline"
                    >
                      Create one
                    </button>
                  </p>
                </div>
              </form>
            )}

            {/* ── Register form ────────────────────────────────────────── */}
            {tab === "register" && (
              <form onSubmit={handleRegister} noValidate>
                <div className="px-6 pt-6 pb-4 space-y-1 border-b border-border">
                  <h2 className="text-lg font-semibold text-foreground">Create account</h2>
                  <p className="text-sm text-muted-foreground">
                    Register to access the Sentinel AI workspace.
                  </p>
                </div>

                <div className="px-6 py-5 space-y-4">
                  {regError && (
                    <div className="flex items-start gap-2.5 rounded-sm border border-destructive/25 bg-destructive/8 px-3 py-2.5">
                      <AlertCircle
                        className="h-4 w-4 text-destructive shrink-0 mt-0.5"
                        strokeWidth={2}
                      />
                      <p className="text-sm text-destructive leading-snug">{regError}</p>
                    </div>
                  )}

                  {regSuccess && (
                    <div className="flex items-start gap-2.5 rounded-sm border border-verdict-confirmed/30 bg-verdict-confirmed/8 px-3 py-2.5">
                      <ShieldCheck
                        className="h-4 w-4 text-verdict-confirmed shrink-0 mt-0.5"
                        strokeWidth={2}
                      />
                      <p className="text-sm text-verdict-confirmed leading-snug font-medium">
                        Account created. Opening your workspace...
                      </p>
                    </div>
                  )}

                  <div className="space-y-1.5">
                    <Label htmlFor="reg-username" className="text-sm font-medium text-foreground">
                      Username
                    </Label>
                    <div className="relative">
                      <User
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
                        strokeWidth={1.8}
                      />
                      <Input
                        id="reg-username"
                        type="text"
                        autoComplete="username"
                        placeholder="jsmith"
                        value={regUsername}
                        onChange={(e) => setRegUsername(e.target.value)}
                        className="pl-9 h-10"
                        disabled={regLoading || regSuccess}
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="reg-email" className="text-sm font-medium text-foreground">
                      Email address
                    </Label>
                    <div className="relative">
                      <Mail
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
                        strokeWidth={1.8}
                      />
                      <Input
                        id="reg-email"
                        type="email"
                        autoComplete="email"
                        placeholder="analyst@forvismazars.com"
                        value={regEmail}
                        onChange={(e) => setRegEmail(e.target.value)}
                        className="pl-9 h-10"
                        disabled={regLoading || regSuccess}
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="reg-password" className="text-sm font-medium text-foreground">
                      Password
                    </Label>
                    <div className="relative">
                      <Lock
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
                        strokeWidth={1.8}
                      />
                      <Input
                        id="reg-password"
                        type={regShowPassword ? "text" : "password"}
                        autoComplete="new-password"
                        placeholder="Min. 8 characters"
                        value={regPassword}
                        onChange={(e) => setRegPassword(e.target.value)}
                        className="pl-9 pr-10 h-10"
                        disabled={regLoading || regSuccess}
                        required
                      />
                      <button
                        type="button"
                        tabIndex={-1}
                        aria-label={regShowPassword ? "Hide password" : "Show password"}
                        onClick={() => setRegShowPassword((v) => !v)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {regShowPassword ? (
                          <EyeOff className="h-4 w-4" strokeWidth={1.8} />
                        ) : (
                          <Eye className="h-4 w-4" strokeWidth={1.8} />
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="reg-confirm" className="text-sm font-medium text-foreground">
                      Confirm password
                    </Label>
                    <div className="relative">
                      <Lock
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
                        strokeWidth={1.8}
                      />
                      <Input
                        id="reg-confirm"
                        type={regShowPassword ? "text" : "password"}
                        autoComplete="new-password"
                        placeholder="Repeat password"
                        value={regConfirm}
                        onChange={(e) => setRegConfirm(e.target.value)}
                        className={cn(
                          "pl-9 h-10",
                          regConfirm && regConfirm !== regPassword
                            ? "border-destructive/60 focus-visible:ring-destructive/40"
                            : "",
                        )}
                        disabled={regLoading || regSuccess}
                        required
                      />
                    </div>
                    {regConfirm && regConfirm !== regPassword && (
                      <p className="text-xs text-destructive mt-1">Passwords do not match</p>
                    )}
                  </div>

                  {/* Password strength bar */}
                  {regPassword && <PasswordStrength password={regPassword} />}
                </div>

                <div className="px-6 pb-6">
                  <Button
                    id="register-submit"
                    type="submit"
                    className="w-full h-10 gap-2"
                    disabled={regLoading || regSuccess}
                  >
                    {regLoading ? (
                      <>
                        <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                        Creating account…
                      </>
                    ) : regSuccess ? (
                      <>
                        <ShieldCheck className="h-4 w-4" strokeWidth={2} />
                        Account created!
                      </>
                    ) : (
                      <>
                        Create account
                        <ArrowRight className="h-4 w-4" strokeWidth={2} />
                      </>
                    )}
                  </Button>

                  <p className="mt-4 text-center text-[13px] text-muted-foreground">
                    Already have an account?{" "}
                    <button
                      type="button"
                      onClick={() => setTab("login")}
                      className="font-semibold text-brand-navy hover:text-brand-cyan transition-colors underline-offset-2 hover:underline"
                    >
                      Sign in
                    </button>
                  </p>
                </div>
              </form>
            )}
          </div>

          {/* Footer */}
          <p className="mt-6 text-center text-[11px] text-muted-foreground">
            Forvis Mazars · Internal security workspace. Access is restricted to authorised
            personnel only.
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Password strength indicator ────────────────────────────────────────────

function PasswordStrength({ password }: { password: string }) {
  const score = computeStrength(password);

  const levels = [
    { label: "Weak", color: "bg-sev-critical" },
    { label: "Fair", color: "bg-sev-medium" },
    { label: "Good", color: "bg-sev-low" },
    { label: "Strong", color: "bg-verdict-confirmed" },
  ];
  const level = levels[score] ?? levels[0];

  return (
    <div className="space-y-1.5">
      <div className="flex gap-1">
        {levels.map((l, i) => (
          <div
            key={l.label}
            className={cn(
              "h-1 flex-1 rounded-full transition-all duration-300",
              i <= score ? level.color : "bg-border",
            )}
          />
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Strength:{" "}
        <span
          className={cn(
            "font-semibold",
            score === 0 && "text-sev-critical",
            score === 1 && "text-sev-medium",
            score === 2 && "text-sev-low",
            score === 3 && "text-verdict-confirmed",
          )}
        >
          {level.label}
        </span>
      </p>
    </div>
  );
}

function computeStrength(pw: string): number {
  let s = 0;
  if (pw.length >= 8) s++;
  if (pw.length >= 12) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  if (/[0-9!@#$%^&*]/.test(pw)) s++;
  return Math.min(s, 3);
}
