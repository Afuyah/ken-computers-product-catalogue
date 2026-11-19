/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/**/*.py",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        display: [
          "Space Grotesk",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
      },
      colors: {
        ink: {
          DEFAULT: "#0B0B0F",
          900: "#0B0B0F",
          800: "#141418",
          700: "#1D1D23",
          600: "#2A2A32",
          500: "#3D3D47",
        },
        paper: {
          DEFAULT: "#FAF9F6",
          50: "#FFFFFF",
          100: "#F4F2EC",
          200: "#EAE7DF",
        },
        accent: {
          DEFAULT: "#2F4BFF",
          600: "#2440E6",
          700: "#1B33C4",
          50: "#EEF0FF",
          100: "#DDE2FF",
        },
        /* Re-map the old neutral/blue scales onto the new palette so every
           page (catalogue and admin) shares one visual language. */
        slate: {
          50: "#FAF9F6",
          100: "#F4F2EC",
          200: "#E7E3DA",
          300: "#D2CDC2",
          400: "#A9A399",
          500: "#7D7770",
          600: "#57524B",
          700: "#3B3732",
          800: "#26231F",
          900: "#141210",
        },
        blue: {
          50: "#EEF0FF",
          100: "#DDE2FF",
          200: "#C3CBFF",
          300: "#9DA9FF",
          400: "#6C7DFF",
          500: "#4A62FF",
          600: "#2F4BFF",
          700: "#2440E6",
          800: "#1B33C4",
          900: "#152A9E",
        },
      },
      letterSpacing: {
        tightest: "-0.045em",
      },
      opacity: {
        12: "0.12",
        15: "0.15",
        35: "0.35",
        45: "0.45",
        55: "0.55",
        65: "0.65",
        85: "0.85",
      },
      borderRadius: {
        "4xl": "2rem",
      },
      boxShadow: {
        lift: "0 1px 2px rgba(11,11,15,.04), 0 12px 32px -12px rgba(11,11,15,.18)",
        card: "0 1px 2px rgba(11,11,15,.05)",
      },
    },
  },
  plugins: [],
};
