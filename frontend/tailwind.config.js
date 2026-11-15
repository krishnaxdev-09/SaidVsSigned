/** @type {import('tailwindcss').Config} */
// "Ledger" design system: warm paper neutrals (replacing Tailwind's cool slate), ink-blue brand, and the four
// result statuses of doc 08 §2. Each status differs in lightness and has its own icon, not just a hue.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        slate: { 50: "#F6F5F1", 100: "#EFEEE9", 200: "#E3E1DA", 300: "#CFCDC5", 400: "#8E918F", 500: "#5F6470",
                 600: "#4A4F59", 700: "#3A3E46", 800: "#262930", 900: "#17191E" },
        ink: { DEFAULT: "#17191E", soft: "#22252B", line: "#3A3D45", muted: "#B9BCC4" },
        brand: { 50: "#EEF1FB", 100: "#DCE2F6", 300: "#B9C3E8", 500: "#4A63C4", 600: "#2440A8", 700: "#182E7E", 900: "#101F57" },
        mismatch: { fg: "#A1261B", bg: "#FBEAE7", line: "#F1C9C3" },
        clarify: { fg: "#7A4E00", bg: "#FDF2DA", line: "#F0DDAE" },
        match: { fg: "#1D6342", bg: "#E6F3EC", line: "#C4E2D1" },
        unchecked: { fg: "#4C525C", bg: "#EEEEEA", line: "#DDDCD5" },
        highlight: "#FFE7A8",
      },
      // Tighter radii than Tailwind's defaults: controls 6 px, panels 8 px. Pills (rounded-full) are kept for avatars/play only.
      borderRadius: { sm: "3px", DEFAULT: "4px", md: "6px", lg: "6px", xl: "8px", "2xl": "8px", "3xl": "10px" },
      boxShadow: {
        card: "0 1px 2px rgb(23 25 30 / 0.05)",
        focus: "0 1px 2px rgb(23 25 30 / 0.05), 0 4px 12px -4px rgb(23 25 30 / 0.08)",
      },
    },
  },
  plugins: [],
};
