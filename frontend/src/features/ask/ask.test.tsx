// T3.4 Ask panel with a fake API: suggestions, sending a question (idempotent POST), cited answer and fallback.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import "../../i18n";
import { _setFetch } from "../../lib/api";
import AskPanel from "./AskPanel";

const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { "content-type": "application/json" } });
const posts: { url: string; key: string | null; body: unknown }[] = [];
afterEach(cleanup);
beforeEach(() => {
  posts.length = 0;
  _setFetch(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push({ url, key: new Headers(init.headers).get("idempotency-key"), body: JSON.parse(String(init.body)) });
      return json({ qa_id: "n", question: "x", status: "routing" }, 202);
    }
    if (url.endsWith("/qa/suggestions")) return json(["What is the processing fee?"]);
    return json([
      { qa_id: "1", question: "What is the processing fee?", route: "quote", status: "done", answer: {
        answer_text: "The processing fee is 2% of the loan amount plus GST.", not_in_document: false, fallback_clauses: [],
        citations: [{ chunk_id: "c", quote: "Processing fee | 2% of loan amount + GST" }] } },
      { qa_id: "2", question: "Is insurance deducted?", route: "quote", status: "done", answer: {
        answer_text: "I couldn't find a reliable answer in your document. These clauses may help:", not_in_document: false,
        citations: [], fallback_clauses: [{ chunk_id: "k", page: "2", text: "5. Insurance. The premium is deducted." }] } },
    ]);
  });
});

it("asks with an idempotency key and shows cited answers and fallback clauses", async () => {
  render(<QueryClientProvider client={new QueryClient()}><AskPanel sessionId="s" /></QueryClientProvider>);
  expect(await screen.findByText("“Processing fee | 2% of loan amount + GST”")).toBeTruthy();
  expect(screen.getByText(/Page 2: 5. Insurance/)).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", { name: "What is the processing fee?" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe("/api/v1/sessions/s/qa");
  expect(posts[0].key).toBeTruthy();
  expect(posts[0].body).toEqual({ question: "What is the processing fee?" });
});
