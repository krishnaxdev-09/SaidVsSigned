// Login card: same calls as before (register → login), mode tabs, password rules and friendly errors.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "../../i18n";
import { _setFetch } from "../../lib/api";
import LoginPage from "./LoginPage";

const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { "content-type": "application/json" } });
const calls: string[] = [];
let loginStatus = 200;
afterEach(cleanup);
beforeEach(() => {
  calls.length = 0; loginStatus = 200;
  _setFetch(async (input) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith("/auth/register")) return json({ code: "email_taken", detail: "x" }, 409);
    if (url.endsWith("/auth/login")) return loginStatus === 200 ? json({ access_token: "t" })
      : json({ code: "unauthorized", detail: "Invalid email or password" }, 401);
    return json({});
  });
});

function page(onAuthed = vi.fn()) {
  render(<MemoryRouter><LoginPage onAuthed={onAuthed} /></MemoryRouter>);
  return onAuthed;
}

it("logs in and shows a friendly error for a wrong password", async () => {
  const onAuthed = page();
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Log in" }).at(-1)!);
  await waitFor(() => expect(onAuthed).toHaveBeenCalled());
  expect(calls).toEqual(["/api/v1/auth/login"]);
  cleanup(); loginStatus = 401;
  page();
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-password" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Log in" }).at(-1)!);
  expect((await screen.findByRole("alert")).textContent).toBe("That email and password don't match. Try again.");
});

it("create account: tab switch, 10-character rule, show/hide, email taken", async () => {
  page();
  fireEvent.click(screen.getByRole("tab", { name: "Create account" }));
  expect(screen.getByText("Create your account")).toBeTruthy();
  expect(screen.getByText("At least 10 characters.")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "b@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
  fireEvent.click(screen.getByRole("button", { name: "Show" }));
  expect((screen.getByLabelText("Password") as HTMLInputElement).type).toBe("text");
  fireEvent.submit(screen.getByLabelText("Email").closest("form")!);
  expect((await screen.findByRole("alert")).textContent).toBe("Use a password with at least 10 characters.");
  expect(calls).toEqual([]);  // nothing sent
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long enough password" } });
  fireEvent.submit(screen.getByLabelText("Email").closest("form")!);
  expect((await screen.findByRole("alert")).textContent).toContain("already exists");
  expect(calls).toEqual(["/api/v1/auth/register"]);
});
