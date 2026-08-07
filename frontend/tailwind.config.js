/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0a0a0f",
          900: "#111119",
          850: "#16161f",
          800: "#1c1c27",
          700: "#262633",
        },
        panel: "#14141c",
        edge: "#2a2a38",
        accent: {
          DEFAULT: "#a78bfa",
          soft: "#8b5cf6",
          dim: "#6d5bd0",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        serif: ["Georgia", "Cambria", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
