import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#171717",
        paper: "#f6f7f5",
        line: "#d9ddd7",
        moss: "#2f6f4e",
        tide: "#247a84",
        ember: "#b94e35",
        brass: "#a57421",
      },
      boxShadow: {
        panel: "0 12px 30px rgba(28, 27, 24, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
