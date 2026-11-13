import { expect, it } from "vitest";
import en from "./en.json";
import hi from "./hi.json";

// Attestation must never be presented as agent consent (doc 01 §5); no accusatory wording (doc 08 §8).
const FORBIDDEN = /agent (has )?(consented|agreed)|agent'?s consent|fraud|liar|\bscam\b|illegal|धोखा|झूठ|अवैध/i;

it("i18n strings pass the wording lint and have identical keys", () => {
  for (const v of [...Object.values(en), ...Object.values(hi)]) expect(v).not.toMatch(FORBIDDEN);
  expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort());
});
