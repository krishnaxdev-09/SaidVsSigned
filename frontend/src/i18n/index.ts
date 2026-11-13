import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import hi from "./hi.json";

// Hindi strings are drafts pending native-speaker review (doc 10 §7).
i18n.use(initReactI18next).init({ resources: { en: { translation: en }, hi: { translation: hi } }, lng: "en",
  fallbackLng: "en", interpolation: { escapeValue: false } });
export default i18n;
