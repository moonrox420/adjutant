"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { Field } from "./ui";

type Mode = "login" | "register" | "forgot" | "resend" | "verify" | "reset";

export function AccountAccess({ signedIn }: { signedIn: () => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [delivery, setDelivery] = useState("");
  useEffect(() => {
    function readAccountLink() {
      const fragment = new URLSearchParams(window.location.hash.slice(1));
      if (fragment.has("stop-pending")) {
        setNotice(
          "You are signed out. Cancellation was requested, but process exit is still awaiting verification. Sign in and check Activity for the final result.",
        );
        history.replaceState(null, "", window.location.pathname);
      } else if (fragment.has("signed-out")) {
        setNotice(
          "You are signed out. Any running jobs for the revoked sessions have stopped and their worker exits were verified.",
        );
        history.replaceState(null, "", window.location.pathname);
      }
      for (const purpose of ["verify", "reset"] as const) {
        const value = fragment.get(purpose);
        if (value) {
          setError("");
          setNotice("");
          setToken(value);
          setMode(purpose);
          history.replaceState(null, "", window.location.pathname);
        }
      }
    }
    readAccountLink();
    window.addEventListener("hashchange", readAccountLink);
    api<{ delivery: string }>("/auth/options")
      .then((options) => setDelivery(options.delivery))
      .catch((e) => setError(e.message));
    return () => window.removeEventListener("hashchange", readAccountLink);
  }, []);

  function changeMode(next: Mode) {
    setMode(next);
    setError("");
    setNotice("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "login") {
        await api("/auth/login", "POST", data);
        signedIn();
        return;
      }
      const path =
        mode === "register"
          ? "/auth/register"
          : mode === "verify"
            ? "/auth/verify"
            : mode === "reset"
              ? "/auth/reset-password"
              : `/auth/request-link?purpose=${mode === "forgot" ? "reset" : "verify"}`;
      const result = await api<{
        message: string;
        delivery?: string;
        local_mailbox?: string;
      }>(
        path,
        "POST",
        mode === "verify" || mode === "reset" ? { ...data, token } : data,
      );
      setNotice(
        result.message +
          (result.delivery === "file"
            ? ` Local development delivery: open the latest email in ${result.local_mailbox}. It is not sent to an external inbox.`
            : ""),
      );
      if (mode === "verify" || mode === "reset") {
        setToken("");
        setMode("login");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "The account request failed.");
    } finally {
      setBusy(false);
    }
  }
  const title = {
    login: "Open your workspace.",
    register: "Make room for good decisions.",
    forgot: "Recover your account.",
    resend: "Get a fresh verification link.",
    verify: "Confirm your email address.",
    reset: "Choose a new password.",
  }[mode];
  const action = {
    login: "Sign in →",
    register: "Create account →",
    forgot: "Send recovery link →",
    resend: "Send verification link →",
    verify: "Verify email →",
    reset: "Save new password →",
  }[mode];
  return (
    <main className="login-page">
      <section className="login-story">
        <div className="wordmark light">
          adjutant<span>↗</span>
        </div>
        <div>
          <span className="eyebrow">YOUR CAMPAIGN OPERATING ROOM</span>
          <h1>
            Good judgment.
            <br />
            <em>At every step.</em>
          </h1>
          <p>
            Know your brand. Shape the campaign.
            <br />
            Make the decision with everything in view.
          </p>
        </div>
        <div className="login-footer">
          <span>01 / UNDERSTAND</span>
          <span>02 / CREATE</span>
          <span>03 / APPROVE</span>
        </div>
      </section>
      <section className="login-form">
        <span className="eyebrow">WELCOME TO ADJUTANT</span>
        <h2>{title}</h2>
        <p className="muted">
          {mode === "register"
            ? "Create your workspace, verify your email, then bring your first brand into focus."
            : mode === "verify"
              ? "Confirm this request to finish activating your account."
              : "Your brands and campaign decisions, together in one workspace."}
        </p>
        {error && (
          <p role="alert" className="message error">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="message">
            {notice}
          </p>
        )}
        <form
          key={mode}
          onSubmit={submit}
          onChange={() => setError("")}
          className="form-stack"
        >
          {mode === "register" && (
            <>
              <Field label="Account type">
                <select name="account_type" defaultValue="business">
                  <option value="business">Business — one brand</option>
                  <option value="agency">
                    Agency — multiple client brands
                  </option>
                </select>
              </Field>
              <Field label="Full name">
                <input
                  name="full_name"
                  autoComplete="name"
                  maxLength={120}
                  required
                />
              </Field>
              <Field label="Workspace name">
                <input
                  name="workspace_name"
                  autoComplete="organization"
                  maxLength={120}
                  required
                />
              </Field>
            </>
          )}
          {mode !== "verify" && mode !== "reset" && (
            <Field label="Email address">
              <input
                name="email"
                type="email"
                autoComplete="username"
                maxLength={254}
                required
              />
            </Field>
          )}
          {["login", "register", "reset"].includes(mode) && (
            <Field label="Password">
              <input
                aria-describedby={
                  mode === "login" ? undefined : "account-password-hint"
                }
                name="password"
                type="password"
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                minLength={mode === "login" ? 1 : 15}
                maxLength={mode === "login" ? 256 : 128}
                required
              />
            </Field>
          )}
          {["register", "reset"].includes(mode) && (
            <p id="account-password-hint" className="footnote">
              Use a passphrase of 15 to 128 characters. Spaces are preserved.
            </p>
          )}
          {["register", "reset"].includes(mode) && (
            <Field label="Confirm password">
              <input
                name="confirm_password"
                type="password"
                autoComplete="new-password"
                minLength={15}
                maxLength={128}
                required
              />
            </Field>
          )}
          <button className="button primary" disabled={busy}>
            {busy ? "Please wait…" : action}
          </button>
        </form>
        <div className="account-links">
          {mode !== "login" && (
            <button
              className="button compact"
              disabled={busy}
              onClick={() => changeMode("login")}
            >
              Back to sign in
            </button>
          )}
          {mode === "login" && (
            <>
              <button
                className="button compact"
                onClick={() => changeMode("register")}
              >
                Create an account
              </button>
              <button
                className="text-button"
                onClick={() => changeMode("forgot")}
              >
                Forgot password?
              </button>
              <button
                className="text-button"
                onClick={() => changeMode("resend")}
              >
                Resend verification email
              </button>
            </>
          )}
        </div>
        {delivery === "file" && (
          <p className="login-note">
            Local development · account emails are delivered to the private
            .local/mail folder on this computer.
          </p>
        )}
      </section>
    </main>
  );
}
