import type { Config } from "tailwindcss";

// Colours are CSS variables (app/globals.css). The app is light-only.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class", // light only: no `dark` class is ever set
  theme: {
    extend: {
      colors: {
        page: v("page"),
        surface: v("surface"),
        ink: v("ink"),
        ink2: v("ink2"),
        muted: v("muted"),
        line: v("line"),
        brand: v("brand"),
        "brand-ink": v("brand-ink"),
        good: v("good"),
        warn: v("warn"),
        serious: v("serious"),
        critical: v("critical"),
      },
      fontFamily: { sans: ["system-ui", "-apple-system", '"Segoe UI"', "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
} satisfies Config;
