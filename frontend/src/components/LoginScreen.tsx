import React, { useEffect, useRef, useState } from 'react';
import { ApiError, loginOwner } from '../lib/api';
import { MANDATORY_PLAN_DISCLAIMER } from '../pages/PagesRegistry';

export interface LoginScreenProps {
  /** Called after a successful login (the token is already stored via setAuthToken + sessionStorage). */
  onAuthenticated: () => void;
  /** Optional message shown above the form, e.g. "Your session has expired". */
  notice?: string | null;
}

/**
 * TRADEIQ single-owner login. There is deliberately no signup, no username and no "forgot password":
 * the only credential is the owner password configured on the server.
 */
export const LoginScreen: React.FC<LoginScreenProps> = ({ onAuthenticated, notice }) => {
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockedFor, setLockedFor] = useState(0); // seconds left on a 429 lockout
  const inputRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    inputRef.current?.focus();
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (lockedFor <= 0) return;
    const t = setTimeout(() => setLockedFor((s) => Math.max(0, s - 1)), 1000);
    return () => clearTimeout(t);
  }, [lockedFor]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || lockedFor > 0) return;
    if (password.length === 0) {
      setError('Enter your owner password to continue.');
      inputRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await loginOwner(password);
      setPassword('');
      onAuthenticated();
    } catch (err) {
      if (!mounted.current) return;
      const apiErr = err instanceof ApiError ? err : null;
      setError(apiErr?.message || 'Sign-in failed. Please try again.');
      if (apiErr?.status === 429 && apiErr.retryAfterSec) setLockedFor(apiErr.retryAfterSec);
      if (apiErr?.status === 401 || apiErr?.status === 429) {
        setPassword('');
        inputRef.current?.focus();
      }
    } finally {
      if (mounted.current) setSubmitting(false);
    }
  };

  const disabled = submitting || lockedFor > 0;

  return (
    <div
      className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center px-4 py-8"
      style={{ paddingTop: 'max(2rem, env(safe-area-inset-top))', paddingBottom: 'max(2rem, env(safe-area-inset-bottom))' }}
    >
      <div className="w-full max-w-sm space-y-4" data-testid="owner-login">
        <div className="text-center space-y-2">
          <div className="text-3xl font-extrabold tracking-tight text-emerald-400">TRADEIQ</div>
          <div className="text-xs text-slate-400">NSE Cash Equity Intelligence</div>
          <span className="inline-block px-2 py-0.5 rounded text-[11px] font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40">
            PAPER ONLY • OWNER ACCESS
          </span>
        </div>

        <form onSubmit={submit} className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4" noValidate>
          <div>
            <h1 className="text-base font-bold text-slate-100">Owner sign in</h1>
            <p className="text-xs text-slate-400 mt-1">This workspace is private. Enter the owner password to continue.</p>
          </div>

          {notice && !error && (
            <div role="status" className="rounded-lg bg-sky-500/10 border border-sky-500/30 px-3 py-2 text-xs text-sky-200">
              {notice}
            </div>
          )}

          {/* Hidden username lets password managers pair the saved password with this single-owner account. */}
          <input type="text" name="username" autoComplete="username" defaultValue="owner" tabIndex={-1} aria-hidden="true" className="sr-only" readOnly />

          <div className="space-y-1.5">
            <label htmlFor="owner-password" className="block text-xs font-medium text-slate-300">
              Owner password
            </label>
            <div className="relative">
              <input
                id="owner-password"
                ref={inputRef}
                type={showPassword ? 'text' : 'password'}
                name="password"
                autoComplete="current-password"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                maxLength={256}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={disabled}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? 'login-error' : undefined}
                className="w-full min-h-[48px] pl-3 pr-16 rounded-lg bg-slate-950 border border-slate-700 text-base text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 disabled:opacity-60"
                placeholder="Enter password"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-pressed={showPassword}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                className="absolute inset-y-0 right-0 px-3 text-xs font-medium text-slate-400 hover:text-slate-200 min-w-[56px]"
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {error && (
            <div id="login-error" role="alert" className="rounded-lg bg-rose-950/30 border border-rose-500/40 px-3 py-2 text-xs text-rose-200">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={disabled}
            className="w-full min-h-[48px] rounded-lg bg-emerald-600 hover:bg-emerald-500 text-sm font-semibold text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? 'Signing in…' : lockedFor > 0 ? `Try again in ${lockedFor}s` : 'Sign in'}
          </button>
        </form>

        <p className="text-[11px] leading-relaxed text-slate-500 text-center px-2">{MANDATORY_PLAN_DISCLAIMER}</p>
      </div>
    </div>
  );
};
