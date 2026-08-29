import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff", 100: "#dae6ff", 500: "#3b6ef5",
          600: "#2f57d4", 700: "#2646ab",
        },
      },
    },
  },
  plugins: [],
};
export default config;
